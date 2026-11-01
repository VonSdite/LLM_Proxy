"""在独立的 monkey patch 运行时中验证真实应用的并发行为。"""

from gevent import monkey

monkey.patch_all()


def main() -> None:
    import json
    import logging
    import sys
    import tempfile
    import time
    from pathlib import Path

    import gevent
    import requests
    import yaml
    from gevent.monkey import get_original
    from gevent.pywsgi import WSGIServer

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from src.application.application import Application
    from src.utils.database import sqlite3

    activity = {"active": 0, "peak": 0}

    def upstream(environ, start_response):
        environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
        start_response("200 OK", [("Content-Type", "text/event-stream")])
        activity["active"] += 1
        activity["peak"] = max(activity["peak"], activity["active"])
        try:
            for _ in range(10):
                gevent.sleep(0.05)
                yield (
                    b'data: {"id":"mock","model":"mock","choices":'
                    b'[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
                )
            yield (
                b'data: {"id":"mock","model":"mock","choices":[],"usage":'
                b'{"prompt_tokens":1,"completion_tokens":10,"total_tokens":11}}\n\n'
            )
            yield b"data: [DONE]\n\n"
        finally:
            activity["active"] -= 1

    native_ident = get_original("_thread", "get_ident")
    caller_ident = native_ident()
    sink_idents = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            sink_idents.append(native_ident())

    with tempfile.TemporaryDirectory(prefix="llm-proxy-concurrency-") as directory:
        root = Path(directory)
        upstream_server = WSGIServer(("127.0.0.1", 0), upstream, log=None)
        upstream_server.start()
        config_path = root / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "logging": {"path": str(root / "logs"), "level": "INFO"},
                    "database": {"path": str(root / "requests.db")},
                    "providers": [
                        {
                            "name": "mock",
                            "api": f"http://127.0.0.1:{upstream_server.server_port}/v1/chat/completions",
                            "model_list": ["mock"],
                            "max_retries": 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
            newline="\n",
        )

        class IsolatedApplication(Application):
            def _setup_config(self) -> None:
                self._root_path = root
                super()._setup_config()

        application = IsolatedApplication(config_path)
        sink = CaptureHandler()
        sink.lock = get_original("_thread", "RLock")()
        application._log_listener.handlers += (sink,)
        server = WSGIServer(("127.0.0.1", 0), application._flask_app, log=None)
        server.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        holder = sqlite3.connect(str(root / "requests.db"))
        try:
            requests.get(base_url + "/providers", timeout=5).raise_for_status()
            holder.execute("BEGIN IMMEDIATE")
            blocked = [gevent.spawn(application._log_repository.insert, "probe/model", "model", 1) for _ in range(20)]
            gevent.sleep(0.03)
            assert all(not job.ready() for job in blocked), "Probes must wait for the external SQLite writer"
            started = time.perf_counter()
            requests.get(base_url + "/api/providers", timeout=2).raise_for_status()
            requests.get(
                base_url + "/api/request-logs?start_date=2026-11-01&end_date=2026-11-01", timeout=2
            ).raise_for_status()
            console_ms = (time.perf_counter() - started) * 1000
            console_during_lock = all(not job.ready() for job in blocked)
            assert console_during_lock, "Console must respond while the SQLite writer remains blocked"
            holder.rollback()
            gevent.joinall(blocked, timeout=5, raise_error=True)
            assert all(job.ready() and job.value is not None for job in blocked)

            monitoring = True
            delays = []

            def monitor() -> None:
                while monitoring:
                    deadline = time.perf_counter() + 0.01
                    gevent.sleep(0.01)
                    requests.get(base_url + "/api/providers", timeout=5).raise_for_status()
                    delays.append(max(time.perf_counter() - deadline, 0))

            def proxy_request() -> None:
                response = requests.post(
                    base_url + "/v1/chat/completions",
                    json={"model": "mock/mock", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                    timeout=10,
                )
                response.raise_for_status()
                assert "[DONE]" in response.text

            observer = gevent.spawn(monitor)
            started = time.perf_counter()
            jobs = [gevent.spawn(proxy_request) for _ in range(20)]
            gevent.joinall(jobs, timeout=15, raise_error=True)
            elapsed = time.perf_counter() - started
            assert all(job.ready() and job.successful() for job in jobs)
            monitoring = False
            observer.get(timeout=5)
            assert activity["peak"] == 20
            with application._db_connection_factory() as conn:
                count = conn.execute("SELECT COUNT(*) FROM request_logs WHERE request_model = 'mock/mock'").fetchone()[
                    0
                ]
                assert count == 20

            application.reload_logging_settings()
            application._logger.info("Concurrency logging reload completed")
            application.close()
            assert "Concurrency logging reload completed" in (root / "logs" / "app.log").read_text(encoding="utf-8")
            assert sink_idents and all(ident != caller_ident for ident in sink_idents)
            print(
                json.dumps(
                    {
                        "peak_upstream_requests": activity["peak"],
                        "completed_streams": len(jobs),
                        "elapsed_s": round(elapsed, 3),
                        "console_max_ms": round(max(delays) * 1000, 2),
                        "console_during_database_lock_ms": round(console_ms, 2),
                        "console_responded_during_database_lock": console_during_lock,
                        "blocked_writers": len(blocked),
                        "log_sink_used_native_thread": True,
                    }
                )
            )
        finally:
            holder.rollback()
            holder.close()
            server.stop()
            upstream_server.stop()
            application.close()


if __name__ == "__main__":
    main()
