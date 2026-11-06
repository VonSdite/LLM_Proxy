"""Vue 入口、会话上下文与静态资源装配契约。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.presentation.app_factory import create_flask_app
from src.presentation.auth_controller import AuthenticationController
from src.presentation.ui import UiController, render_ui
from src.presentation.web_controller import WebController


class UiEntryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_flask_app()
        self.app.config["TESTING"] = True
        self.config = Mock()
        self.config.is_oauth_enabled.return_value = True
        self.config.is_api_key_management_enabled.return_value = True
        self.config.is_model_mapping_enabled.return_value = True
        self.config.is_chat_whitelist_enabled.return_value = False
        self.auth = Mock()
        self.auth.is_auth_enabled.return_value = False
        self.auth.validate_session.return_value = False
        self.auth.get_session_username.return_value = "admin"
        UiController(SimpleNamespace(flask_app=self.app, config_manager=self.config), self.auth)
        self.client = self.app.test_client()

    def test_bootstrap_contains_only_ui_context(self):
        response = self.client.get("/api/ui/bootstrap")
        data = response.get_json()
        self.assertTrue(data["authenticated"])
        self.assertTrue(data["capabilities"]["agents"])
        self.assertEqual(data["username"], "")
        self.assertEqual(
            set(data), {"version", "username", "auth_enabled", "authenticated", "features", "capabilities"}
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_unauthenticated_bootstrap_hides_features_and_username(self):
        self.auth.is_auth_enabled.return_value = True
        data = self.client.get("/api/ui/bootstrap").get_json()
        self.assertFalse(data["authenticated"])
        self.assertFalse(any(data["features"].values()))
        self.assertFalse(data["capabilities"]["agents"])
        self.assertEqual(data["username"], "")

    def test_authenticated_remote_context_allows_agent_interface(self):
        self.auth.is_auth_enabled.return_value = True
        self.auth.validate_session.return_value = True
        data = self.client.get("/api/ui/bootstrap", base_url="http://proxy.example").get_json()
        self.assertEqual(data["username"], "admin")
        self.assertTrue(data["capabilities"]["agents"])

    def test_agent_capability_checks_both_peer_and_host(self):
        for host, peer, allowed in [
            ("localhost", "127.0.0.1", True),
            ("[::1]", "::1", True),
            ("127.0.0.1", "10.0.0.2", False),
            ("proxy.example", "127.0.0.1", False),
        ]:
            with self.subTest(host=host, peer=peer):
                data = self.client.get(
                    "/api/ui/bootstrap", base_url=f"http://{host}", environ_overrides={"REMOTE_ADDR": peer}
                ).get_json()
                self.assertEqual(data["capabilities"]["agents"], allowed)

    def test_manifest_entry_and_explicit_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app/assets").mkdir(parents=True)
            for name in ("main-123.js", "main-123.css", "shared-456.js"):
                (root / "app/assets" / name).write_text("/* fixture */", encoding="utf-8")
            manifest = {
                "index.html": {"file": "assets/main-123.js", "css": ["assets/main-123.css"], "imports": ["shared"]},
                "shared": {"file": "assets/shared-456.js"},
            }
            (root / "app/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.app.static_folder = str(root)
            self.app.add_url_rule("/settings", "test_ui", render_ui)
            response = self.client.get("/settings")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"/static/app/assets/main-123.js", response.data)
            self.assertIn(b"/static/app/assets/shared-456.js", response.data)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(self.client.get("/api/not-found").status_code, 404)
            self.assertEqual(self.client.get("/v1/not-found").status_code, 404)
            asset = self.client.get("/static/app/assets/main-123.js")
            self.assertIn("immutable", asset.headers["Cache-Control"])
            asset.close()

    def test_all_admin_deep_links_share_vue_entry_and_keep_server_guards(self):
        context = SimpleNamespace(flask_app=self.app, config_manager=self.config, logger=Mock())
        WebController(context, Mock(), Mock(), self.auth)
        AuthenticationController(context, self.auth)
        pages = ["/", "/providers", "/users", "/oauth", "/model-mappings", "/api-keys", "/statistics", "/settings"]
        for path in pages:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'id="app"', response.data)
                self.assertIn(b'type="module"', response.data)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn(b"bootstrap.bundle", response.data)
        self.config.is_model_mapping_enabled.return_value = False
        self.config.is_api_key_management_enabled.return_value = False
        for path in ["/model-mappings", "/api-keys"]:
            self.assertEqual(self.client.get(path).headers["Location"], "/settings")
        self.assertEqual(self.client.get("/login").headers["Location"], "/")
        self.auth.is_auth_enabled.return_value = True
        for path in pages:
            self.assertEqual(self.client.get(path).headers["Location"], "/login")
        login = self.client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn(b'id="app"', login.data)
        self.assertEqual(self.client.get("/api/unknown").status_code, 404)
        self.assertEqual(self.client.get("/v1/unknown").status_code, 404)

    def test_manifest_rejects_paths_outside_build_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "outside.js").write_text("sensitive", encoding="utf-8")
            (root / "app/manifest.json").write_text(
                json.dumps({"index.html": {"file": "../outside.js"}}), encoding="utf-8"
            )
            self.app.static_folder = str(root)
            self.app.add_url_rule("/settings", "test_ui", render_ui)
            with self.assertLogs(self.app.logger, level="ERROR"):
                response = self.client.get("/settings")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"sensitive", response.data)


if __name__ == "__main__":
    unittest.main()
