"""在与 main.py 一致的协作运行时验证跨请求 SSH 会话及数据平面独立性。"""

from gevent import monkey

monkey.patch_all()


def main():
    import json
    import os
    import sys
    import tempfile
    import time
    from pathlib import Path
    from unittest.mock import patch

    import gevent
    import requests
    import yaml
    from gevent.pywsgi import WSGIServer

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.application.application import Application
    from src.external.agent_config_transport import LocalConfigFiles

    from test_agent_config import draft
    from test_agent_config_transport import SshServer, TunnelProxy

    with (
        tempfile.TemporaryDirectory(prefix="agent-ssh-scenario-") as directory,
        patch.dict(os.environ, {"CODEX_HOME": "", "CLAUDE_CONFIG_DIR": ""}),
    ):
        root = Path(directory)
        config = root / "config.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "database": {"path": str(root / "requests.db")},
                    "logging": {"path": str(root / "logs"), "level": "ERROR"},
                    "providers": [
                        {"name": "main", "api": "http://127.0.0.1:1/v1/responses", "model_list": ["gpt-5.4"]}
                    ],
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        application = Application(config)
        application._agent_config_service.local = LocalConfigFiles(root / "local-home")
        server = WSGIServer(("127.0.0.1", 0), application._flask_app, log=None)
        server.start()
        ssh = SshServer(root / "remote")
        browser = requests.Session()
        browser.trust_env = False
        browser.headers["X-Agent-Config"] = "1"
        origin = f"http://127.0.0.1:{server.server_port}"
        browser.headers["Origin"] = origin

        def request(method, path, **kwargs):
            response = browser.request(method, origin + path, timeout=10, **kwargs)
            assert response.status_code < 400, (response.status_code, response.text)
            return response.json()

        try:
            assert browser.get(origin + "/agents").status_code == 200
            bootstrap = request("GET", "/api/agent-config/bootstrap")
            assert bootstrap["models"] == ["main/gpt-5.4"]
            machine = request(
                "POST",
                "/api/agent-config/machines",
                json={
                    "name": "test",
                    "host": "localhost",
                    "port": ssh.port,
                    "user": "agent",
                    "password": "test-password",
                },
            )["machine"]
            base = "/api/agent-config/machines/" + machine["id"]
            unknown = browser.post(origin + base + "/connect", json={})
            assert unknown.status_code == 409, unknown.text
            assert unknown.json()["code"] == "host_key_required"
            snapshot = request("POST", base + "/connect", json={"trustFingerprint": unknown.json()["fingerprint"]})
            assert snapshot["machine"]["home"] == "/srv/custom-home"
            for agent in ("claude", "codex", "opencode"):
                snapshot = request("GET", base + "/snapshot")
                preview = request(
                    "POST",
                    base + "/" + agent + "/preview",
                    json={
                        "versions": {target: value["version"] for target, value in snapshot["files"].items()},
                        "draft": draft(agent),
                    },
                )
                saved = request("POST", base + "/apply", json={"token": preview["token"]})
                assert saved["written"] == (2 if agent == "codex" else 1)
            assert (root / "remote/srv/custom-home/.claude/settings.json").is_file()
            assert (root / "remote/srv/custom-home/.codex/auth.json").is_file()
            transport = application._agent_config_service.sessions[
                next(iter(application._agent_config_service.sessions))
            ]["files"]
            original_read = transport.read

            def slow_read(path):
                gevent.sleep(0.05)
                return original_read(path)

            with patch.object(transport, "read", side_effect=slow_read):
                loading = gevent.spawn(request, "GET", base + "/snapshot")
                gevent.sleep(0.01)
                started = time.monotonic()
                models = browser.get(origin + "/v1/models", timeout=2)
                elapsed = time.monotonic() - started
                assert models.status_code == 200
                assert not loading.ready(), "Data plane waited for Agent file operations"
                loading.get(timeout=5)
            request("POST", base + "/disconnect", json={})
            assert not transport.alive()
            for scheme in ("http", "socks5h"):
                proxy = TunnelProxy(scheme)
                try:
                    request(
                        "PUT",
                        base,
                        json={
                            "name": "test",
                            "host": "localhost",
                            "port": ssh.port,
                            "user": "agent",
                            "proxy": {"mode": "custom", "url": proxy.url},
                        },
                    )
                    connected = request("POST", base + "/connect", json={})
                    assert connected["machine"]["connected"]
                    content = request("GET", base + "/codex/files")["files"]
                    assert "main/org/model.v1" in content[0]["text"]
                    assert proxy.requests
                    request("POST", base + "/disconnect", json={})
                finally:
                    proxy.close()
            print(
                json.dumps(
                    {
                        "agents_saved": 3,
                        "remote_home": snapshot["machine"]["home"],
                        "data_plane_seconds": elapsed,
                        "disconnected": True,
                    }
                )
            )
        finally:
            browser.close()
            server.stop()
            application.close()
            ssh.close()


if __name__ == "__main__":
    main()
