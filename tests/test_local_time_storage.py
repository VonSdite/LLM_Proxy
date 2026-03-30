import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.repositories import LogRepository, UserRepository
from src.services import LogService, UserService
from src.utils.database import create_connection_factory
from src.utils.local_time import parse_local_datetime


class DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class LocalTimeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tempdir.name) / "requests.db"
        self._get_connection = create_connection_factory(self._db_path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _table_sql(self, table_name: str) -> str:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        self.assertIsNotNone(row)
        return row["sql"]

    def test_user_table_schema_removes_timestamp_defaults(self) -> None:
        UserRepository(self._get_connection)

        sql = self._table_sql("users")

        self.assertIn("created_at TEXT NOT NULL", sql)
        self.assertIn("updated_at TEXT NOT NULL", sql)
        self.assertNotIn("CURRENT_TIMESTAMP", sql)

    def test_log_tables_schema_remove_timestamp_defaults(self) -> None:
        LogRepository(self._get_connection)

        request_logs_sql = self._table_sql("request_logs")
        daily_stats_sql = self._table_sql("daily_request_stats")

        self.assertIn("start_time TEXT NOT NULL", request_logs_sql)
        self.assertIn("created_at TEXT NOT NULL", request_logs_sql)
        self.assertNotIn("CURRENT_TIMESTAMP", request_logs_sql)
        self.assertIn("created_at TEXT NOT NULL", daily_stats_sql)
        self.assertIn("updated_at TEXT NOT NULL", daily_stats_sql)
        self.assertNotIn("CURRENT_TIMESTAMP", daily_stats_sql)

    def test_user_repository_create_writes_local_time_text(self) -> None:
        repository = UserRepository(self._get_connection)

        user_id = repository.create("alice", "127.0.0.1")
        user = repository.get_by_id(user_id)

        self.assertIsNotNone(user)
        self.assertEqual(user["created_at"], user["updated_at"])
        self.assertIn(".", user["created_at"])
        self.assertIsNotNone(parse_local_datetime(user["created_at"]))

    def test_log_repository_insert_writes_local_times_and_stat_date(self) -> None:
        repository = LogRepository(self._get_connection)

        log_id = repository.insert(
            request_model="demo-model",
            response_model="demo-response",
            total_tokens=12,
            prompt_tokens=5,
            completion_tokens=7,
            start_time="2026-03-30 09:10:11.123456",
            end_time="2026-03-30 09:10:12.654321",
            ip_address="127.0.0.1",
        )

        with self._get_connection() as conn:
            log_row = conn.execute(
                "SELECT start_time, end_time, created_at FROM request_logs WHERE id = ?",
                (log_id,),
            ).fetchone()
            stat_row = conn.execute(
                """
                SELECT stat_date, created_at, updated_at
                FROM daily_request_stats
                WHERE ip_address = ? AND request_model = ?
                """,
                ("127.0.0.1", "demo-model"),
            ).fetchone()

        self.assertEqual("2026-03-30 09:10:11.123456", log_row["start_time"])
        self.assertEqual("2026-03-30 09:10:12.654321", log_row["end_time"])
        self.assertIn(".", log_row["created_at"])
        self.assertEqual("2026-03-30", stat_row["stat_date"])
        self.assertIn(".", stat_row["created_at"])
        self.assertIn(".", stat_row["updated_at"])


class LocalTimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tempdir.name) / "requests.db"
        self._get_connection = create_connection_factory(self._db_path)
        self._ctx = SimpleNamespace(logger=DummyLogger())

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_user_service_returns_normalized_local_time_strings(self) -> None:
        repository = UserRepository(self._get_connection)
        user_id = repository.create("alice", "127.0.0.1")

        with self._get_connection() as conn:
            conn.execute(
                "UPDATE users SET created_at = ?, updated_at = ? WHERE id = ?",
                ("2026-03-30 08:09:10", "2026-03-30 08:09:11", user_id),
            )

        service = UserService(self._ctx, repository)
        user = service.get_user_by_id(user_id)

        self.assertEqual("2026-03-30 08:09:10.000000", user["created_at"])
        self.assertEqual("2026-03-30 08:09:11.000000", user["updated_at"])

    def test_log_service_returns_normalized_local_time_strings(self) -> None:
        UserRepository(self._get_connection)
        repository = LogRepository(self._get_connection)
        log_id = repository.insert(
            request_model="demo-model",
            response_model="demo-response",
            total_tokens=4,
            start_time="2026-03-30 08:09:10",
            end_time="2026-03-30 08:09:11",
            ip_address="127.0.0.1",
        )

        with self._get_connection() as conn:
            conn.execute(
                "UPDATE request_logs SET created_at = ? WHERE id = ?",
                ("2026-03-30 08:09:12", log_id),
            )

        service = LogService(self._ctx, repository)
        payload = service.get_request_logs(page=1, page_size=10)
        log = payload["logs"][0]

        self.assertEqual("2026-03-30 08:09:10.000000", log["start_time"])
        self.assertEqual("2026-03-30 08:09:11.000000", log["end_time"])
        self.assertEqual("2026-03-30 08:09:12.000000", log["created_at"])


class LocalTimeSqlScriptTests(unittest.TestCase):
    def test_manual_sql_scripts_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migrate_sql = (root / "scripts" / "sql" / "001_migrate_created_at_to_localtime.sql").read_text(
            encoding="utf-8"
        )
        rebuild_sql = (root / "scripts" / "sql" / "002_rebuild_tables_remove_time_defaults.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("datetime(created_at, 'localtime') || '.000000'", migrate_sql)
        self.assertIn("ALTER TABLE users RENAME TO users_old;", rebuild_sql)
        self.assertIn("DROP INDEX IF EXISTS idx_users_ip;", rebuild_sql)
        self.assertIn("DROP INDEX IF EXISTS idx_start_time;", rebuild_sql)
        self.assertIn("DROP INDEX IF EXISTS idx_daily_stats_date;", rebuild_sql)
        self.assertIn("CREATE TABLE request_logs (", rebuild_sql)
        self.assertIn("created_at TEXT NOT NULL", rebuild_sql)
        self.assertNotIn("CURRENT_TIMESTAMP", rebuild_sql)


if __name__ == "__main__":
    unittest.main()
