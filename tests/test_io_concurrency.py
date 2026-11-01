"""数据库、配置文件和运行日志的协作并发回归。"""

import json
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import gevent
from gevent.monkey import get_original

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.auth_group_manager import AuthGroupManager, AuthGroupSelectionError
from src.config.config_manager import ConfigManager
from src.config.provider_config import AuthGroupSchema
from src.repositories import AuthGroupRepository, LogRepository
from src.services.provider_service import ProviderService
from src.services.settings_service import SettingsService
from src.utils.async_logging import AsyncLogHandler, LogQueue, NativeLogListener, RotatingLogHandler
from src.utils.blocking_io import run_blocking
from src.utils.database import create_connection_factory, sqlite3


class DatabaseConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "requests.db"
        self.factory = create_connection_factory(self.path)
        self.repository = LogRepository(self.factory)

    def test_reader_snapshot_allows_log_and_statistics_commit(self) -> None:
        with self.factory() as reader:
            self.assertEqual("wal", reader.execute("PRAGMA journal_mode").fetchone()[0])
            reader.execute("BEGIN")
            self.assertEqual(0, reader.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0])
            self.repository.insert("mock/model", "model", 12, ip_address="127.0.0.1")
            self.assertEqual(0, reader.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0])
        with self.factory() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0])
            self.assertEqual(12, conn.execute("SELECT total_tokens FROM daily_request_stats").fetchone()[0])

    def test_sqlite_lock_wait_allows_scheduled_release(self) -> None:
        holder = sqlite3.connect(str(self.path))
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        released = []

        def release() -> None:
            released.append(True)
            holder.rollback()

        timer = gevent.spawn_later(0.05, release)
        try:
            job = gevent.spawn(self.repository.insert, "mock/model", "model", 3, ip_address="127.0.0.1")
            self.assertIsNotNone(job.get(timeout=2))
            self.assertEqual([True], released)
        finally:
            timer.kill()
            holder.rollback()
            job.join(timeout=6)

    def test_failed_transaction_preserves_detail_and_statistics_consistency(self) -> None:
        with self.assertRaises(ValueError):
            self.repository.insert("mock/model", "model", 9, api_key_id="invalid")
        with self.factory() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM daily_request_stats").fetchone()[0])

    def test_twenty_waiting_writers_leave_workers_available_for_reads(self) -> None:
        holder = sqlite3.connect(str(self.path))
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        jobs = [
            gevent.spawn(self.repository.insert, "mock/model", "model", 3, ip_address="127.0.0.1") for _ in range(20)
        ]
        gevent.sleep(0.05)
        reader = gevent.spawn(self.repository.get_unique_request_models)
        try:
            self.assertEqual([], reader.get(timeout=1))
            self.assertTrue(all(not job.ready() for job in jobs))
        finally:
            holder.rollback()
            gevent.joinall(jobs, timeout=10, raise_error=True)
            reader.join(timeout=2)
        self.assertTrue(all(job.ready() and job.value is not None for job in jobs))

    def test_twenty_concurrent_completions_preserve_all_statistics(self) -> None:
        jobs = [
            gevent.spawn(self.repository.insert, "mock/model", "model", 3, ip_address="127.0.0.1") for _ in range(20)
        ]
        gevent.joinall(jobs, timeout=10, raise_error=True)
        self.assertTrue(all(job.ready() and job.value is not None for job in jobs))
        with self.factory() as conn:
            self.assertEqual(20, conn.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0])
            row = conn.execute("SELECT request_count, total_tokens FROM daily_request_stats").fetchone()
            self.assertEqual((20, 60), tuple(row))

    def test_cancellation_waits_for_native_task_to_release_resources(self) -> None:
        native_sleep = get_original("time", "sleep")
        entered = get_original("queue", "SimpleQueue")()
        finished = []

        def slow_operation() -> None:
            entered.put(True)
            native_sleep(0.1)
            finished.append(True)

        job = gevent.spawn(run_blocking, slow_operation)
        with gevent.Timeout(2):
            while entered.empty():
                gevent.sleep(0.001)
        job.kill(block=False)
        job.join(timeout=2)
        self.assertTrue(job.ready())
        self.assertEqual([True], finished)


class AsyncLoggingTests(unittest.TestCase):
    def test_queue_bounds_records_and_bytes_without_waiting(self) -> None:
        record = logging.makeLogRecord({"name": "app", "msg": "example"})
        queue = LogQueue(max_records=1)
        queue.put_nowait(record)
        queue.put_nowait(record)
        self.assertEqual(1, queue.take_dropped())
        self.assertIs(record, queue.get(False))
        queue.close()
        self.assertIsNone(queue.get(False))
        tiny_queue = LogQueue(max_bytes=1)
        tiny_queue.put_nowait(record)
        self.assertEqual(1, tiny_queue.take_dropped())

    def test_slow_sink_runs_in_native_thread_and_preserves_queued_records(self) -> None:
        gate = get_original("_thread", "allocate_lock")()
        gate.acquire()
        native_ident = get_original("_thread", "get_ident")
        caller = native_ident()
        records = []

        class SlowHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if gate.acquire(timeout=2):
                    gate.release()
                records.append((record.getMessage(), native_ident()))

        listener = NativeLogListener({"app": [SlowHandler()]})
        listener.start()
        logger = logging.Logger("app", logging.INFO)
        handler = AsyncLogHandler(listener.queue)
        logger.addHandler(handler)
        try:
            logger.info("first %s", "record")
            logger.info("second record")
            self.assertEqual([], records)
            gevent.sleep(0.01)
            self.assertEqual([], records)
        finally:
            gate.release()
            listener.close()
            handler.close()
        self.assertEqual(["first record", "second record"], [item[0] for item in records])
        self.assertTrue(all(item[1] != caller for item in records))

    def test_routing_rotation_and_shutdown_flush(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sinks = {
                name: [
                    RotatingLogHandler(root / f"{name}.log", maxBytes=80, backupCount=1, encoding="utf-8", delay=True)
                ]
                for name in ("app", "access", "llm_request_trace")
            }
            listener = NativeLogListener(sinks)
            listener.start()
            try:
                for name in sinks:
                    handler = AsyncLogHandler(listener.queue)
                    self.addCleanup(handler.close)
                    logger = logging.Logger(name, logging.INFO)
                    logger.addHandler(handler)
                    for index in range(4):
                        logger.info("%s message %s%s", name, index, "x" * 20)
            finally:
                listener.close()
            for name in sinks:
                contents = (root / f"{name}.log").read_bytes()
                self.assertIn(f"{name} message 3".encode(), contents)
                self.assertNotIn(b"\r\n", contents)
                self.assertTrue((root / f"{name}.log.1").exists())


class AuthGroupConcurrencyTests(unittest.TestCase):
    def test_request_quota_is_reserved_before_next_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = AuthGroupRepository(create_connection_factory(Path(directory) / "requests.db"))
            manager = AuthGroupManager(SimpleNamespace(logger=logging.getLogger("test")), repository)
            group = AuthGroupSchema.from_mapping(
                {
                    "name": "shared",
                    "entries": [
                        {"id": "only", "headers": {"Authorization": "Bearer mock"}, "request_quota_per_minute": 1}
                    ],
                }
            )
            manager.load_auth_groups((group,))
            original = repository.increment_request_usage

            def slow_record(*args, **kwargs) -> None:
                gevent.sleep(0.02)
                original(*args, **kwargs)

            def acquire_and_dispatch() -> bool:
                try:
                    selection = manager.acquire("shared")
                except AuthGroupSelectionError:
                    return False
                manager.mark_request_dispatched(selection)
                return True

            with patch.object(repository, "increment_request_usage", side_effect=slow_record):
                jobs = [gevent.spawn(acquire_and_dispatch) for _ in range(20)]
                gevent.joinall(jobs, timeout=5, raise_error=True)
            self.assertTrue(all(job.ready() for job in jobs))
            self.assertEqual(1, sum(job.value for job in jobs))


class ConfigFileConcurrencyTests(unittest.TestCase):
    def test_concurrent_control_plane_updates_preserve_each_config_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.yaml"
            path.write_text("providers: []\n", encoding="utf-8", newline="\n")
            config = ConfigManager(path, root)
            ctx = SimpleNamespace(config_manager=config, root_path=root, logger=logging.getLogger("test"))
            providers = ProviderService(ctx, reload_callback=config.reload)
            settings = SettingsService(ctx)
            jobs = [
                gevent.spawn(
                    providers.create_provider,
                    {"name": f"provider_{index}", "api": "https://example.com", "model_list": ["mock"]},
                )
                for index in range(10)
            ]
            jobs.append(gevent.spawn(settings.update_chat_whitelist_enabled, True))
            gevent.joinall(jobs, timeout=10, raise_error=True)
            self.assertTrue(all(job.ready() and job.successful() for job in jobs))
            config.reload()
            self.assertEqual(10, len(config.get_raw_config()["providers"]))
            self.assertTrue(config.is_chat_whitelist_enabled())

    def test_slow_fsync_allows_other_greenlets_to_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.yaml"
            path.write_text("providers: []\n", encoding="utf-8", newline="\n")
            config = ConfigManager(path, root)
            fsync = get_original("os", "fsync")
            native_sleep = get_original("time", "sleep")
            progress = []

            def slow_fsync(fd: int) -> None:
                native_sleep(0.1)
                fsync(fd)

            timer = gevent.spawn_later(0.03, progress.append, True)
            try:
                with patch("src.config.config_manager.os.fsync", side_effect=slow_fsync):
                    config.write_raw_config({"providers": [], "chat": {"whitelist_enabled": True}})
                    self.assertEqual([True], progress)
            finally:
                timer.kill()
            self.assertTrue(config.is_chat_whitelist_enabled())
            self.assertNotIn(b"\r\n", path.read_bytes())


class PatchedRuntimeConcurrencyTests(unittest.TestCase):
    def test_actual_application_with_monkey_patch_and_twenty_streams(self) -> None:
        scenario = Path(__file__).with_name("io_concurrency_scenario.py")
        result = subprocess.run(
            [sys.executable, str(scenario)],
            cwd=scenario.parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        metrics = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(20, metrics["peak_upstream_requests"])
        self.assertEqual(20, metrics["completed_streams"])
        self.assertTrue(metrics["console_responded_during_database_lock"])
        self.assertEqual(20, metrics["blocked_writers"])
        self.assertTrue(metrics["log_sink_used_native_thread"])


if __name__ == "__main__":
    unittest.main()
