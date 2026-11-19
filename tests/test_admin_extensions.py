"""管理端 Python 扩展加载、动作路由与生命周期契约。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.presentation.admin_extension_manager import AdminExtensionManager
from src.presentation.app_factory import create_flask_app


class AdminExtensionManagerTests(unittest.TestCase):
    def setUp(self):
        project_tmp = Path(__file__).resolve().parents[1] / "tmp"
        project_tmp.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=project_tmp)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "extensions"
        self.directory.mkdir()
        self.app = create_flask_app()
        self.app.config["TESTING"] = True
        self.auth = Mock()
        self.auth.is_auth_enabled.return_value = False
        self.auth.validate_session.return_value = False
        self.logger = Mock()
        self.ctx = SimpleNamespace(
            logger=self.logger,
            config_manager=Mock(),
            root_path=self.root,
            flask_app=self.app,
        )
        self.client = self.app.test_client()

    def write_extension(self, extension_id, source):
        folder = self.directory / extension_id
        folder.mkdir()
        (folder / "extension.py").write_text(source, encoding="utf-8")
        return folder

    def create_manager(self, services=None):
        return AdminExtensionManager(
            self.ctx,
            self.auth,
            Mock(),
            services=services,
            directory=self.directory,
        )

    def test_action_route_uses_extension_namespace_and_receives_context(self):
        self.write_extension(
            "sample-tools",
            """
class Extension:
    def __init__(self, context):
        self.context = context

    def register_routes(self, blueprint):
        @blueprint.post("/actions/ping")
        def ping():
            return {
                "extension": self.context.extension_id,
                "root": str(self.context.extension_path),
            }
""",
        )
        manager = self.create_manager()

        self.assertEqual(manager.extension_ids, ("sample-tools",))
        self.assertEqual(manager.errors, {})
        response = self.client.post("/api/extensions/sample-tools/actions/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["extension"], "sample-tools")
        self.assertEqual(response.get_json()["root"], str(self.directory / "sample-tools"))

    def test_action_route_keeps_admin_authentication(self):
        self.write_extension(
            "secure-tools",
            """
class Extension:
    def __init__(self, context):
        pass

    def register_routes(self, blueprint):
        @blueprint.get("/status")
        def status():
            return {"ok": True}
""",
        )
        self.auth.is_auth_enabled.return_value = True
        self.create_manager()

        response = self.client.get("/api/extensions/secure-tools/status")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Unauthorized"})

        self.auth.validate_session.return_value = True
        self.assertEqual(self.client.get("/api/extensions/secure-tools/status").status_code, 200)

    def test_extension_loads_relative_helper_module(self):
        folder = self.write_extension(
            "helper-tools",
            """
from .helpers import action_name


class Extension:
    def __init__(self, context):
        pass

    def register_routes(self, blueprint):
        @blueprint.get("/status")
        def status():
            return {"action": action_name}
""",
        )
        (folder / "helpers.py").write_text('action_name = "refresh"\n', encoding="utf-8")
        manager = self.create_manager()

        self.assertEqual(manager.extension_ids, ("helper-tools",))
        self.assertEqual(manager.errors, {})
        self.assertEqual(self.client.get("/api/extensions/helper-tools/status").get_json(), {"action": "refresh"})

    def test_start_and_close_follow_extension_lifecycle(self):
        events = []
        self.write_extension(
            "lifecycle-tools",
            """
class Extension:
    def __init__(self, context):
        self.events = context.services["events"]

    def start(self):
        self.events.append("start")

    def close(self):
        self.events.append("close")
""",
        )
        manager = self.create_manager({"events": events})
        manager.start()
        manager.close()
        manager.close()

        self.assertEqual(events, ["start", "close"])

    def test_broken_extension_does_not_block_valid_extension(self):
        self.write_extension("broken-tools", 'raise RuntimeError("broken extension")')
        self.write_extension(
            "working-tools",
            """
class Extension:
    def __init__(self, context):
        pass

    def register_routes(self, blueprint):
        @blueprint.get("/status")
        def status():
            return {"ok": True}
""",
        )
        manager = self.create_manager()

        self.assertEqual(manager.extension_ids, ("working-tools",))
        self.assertIn("broken-tools", manager.errors)
        self.assertIn("broken extension", manager.errors["broken-tools"])
        self.assertEqual(self.client.get("/api/extensions/working-tools/status").status_code, 200)
        self.logger.error.assert_called()

    def test_extension_exception_returns_controlled_error(self):
        self.write_extension(
            "failing-tools",
            """
class Extension:
    def __init__(self, context):
        pass

    def register_routes(self, blueprint):
        @blueprint.post("/actions/fail")
        def fail():
            raise RuntimeError("sensitive detail")
""",
        )
        self.create_manager()

        response = self.client.post("/api/extensions/failing-tools/actions/fail")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "Extension action failed"})
        self.assertNotIn(b"sensitive detail", response.data)

    def test_failed_blueprint_registration_leaves_no_active_routes(self):
        self.write_extension(
            "broken-registration",
            """
class Extension:
    def __init__(self, context):
        pass

    def register_routes(self, blueprint):
        @blueprint.get("/status")
        def status():
            return {"status": "must-not-be-active"}

        blueprint.add_url_rule("/one", endpoint="duplicate", view_func=lambda: "one")
        blueprint.add_url_rule("/two", endpoint="duplicate", view_func=lambda: "two")
""",
        )
        manager = self.create_manager()

        self.assertEqual(manager.extension_ids, ())
        self.assertIn("broken-registration", manager.errors)
        self.assertEqual(self.client.get("/api/extensions/broken-registration/status").status_code, 404)

    def test_hyphen_and_underscore_ids_have_independent_modules_and_blueprints(self):
        source = """
class Extension:
    def __init__(self, context):
        pass

    def register_routes(self, blueprint):
        @blueprint.get("/status")
        def status():
            from .helpers import VALUE
            return {"value": VALUE}
"""
        first = self.write_extension("foo-bar", source)
        second = self.write_extension("foo_bar", source)
        (first / "helpers.py").write_text('VALUE = "hyphen"\n', encoding="utf-8")
        (second / "helpers.py").write_text('VALUE = "underscore"\n', encoding="utf-8")
        manager = self.create_manager()

        self.assertEqual(manager.extension_ids, ("foo-bar", "foo_bar"))
        self.assertEqual(manager.errors, {})
        self.assertEqual(self.client.get("/api/extensions/foo-bar/status").get_json(), {"value": "hyphen"})
        self.assertEqual(self.client.get("/api/extensions/foo_bar/status").get_json(), {"value": "underscore"})

    def test_registration_callback_runs_once_on_the_main_app(self):
        events = []
        self.write_extension(
            "registration-once",
            """
class Extension:
    def __init__(self, context):
        self.events = context.services["events"]

    def register_routes(self, blueprint):
        @blueprint.record_once
        def initialize(state):
            self.events.append(state.app)
            assert state.app.config["TESTING"]

        @blueprint.get("/status")
        def status():
            return {"ok": True}
""",
        )
        manager = self.create_manager({"events": events})
        self.addCleanup(manager.close)

        self.assertEqual(manager.errors, {})
        self.assertEqual(events, [self.app])
        self.assertEqual(self.client.get("/api/extensions/registration-once/status").status_code, 200)

    def test_failed_registration_restores_routes_callbacks_templates_and_commands(self):
        events = []

        @self.app.get("/core")
        def core():
            return {"core": True}

        @self.app.before_request
        def core_before_request():
            events.append("core request")

        @self.app.template_filter("marker")
        def core_filter(value):
            return value

        @self.app.errorhandler(404)
        def core_not_found(error):
            return {"error": "Core not found"}, 404

        original_rules = [(rule.rule, rule.endpoint) for rule in self.app.url_map.iter_rules()]
        original_views = dict(self.app.view_functions)
        original_blueprints = dict(self.app.blueprints)
        original_commands = dict(self.app.cli.commands)
        self.write_extension(
            "broken-callback",
            """
from flask import Blueprint

class Extension:
    def __init__(self, context):
        self.events = context.services["events"]
        self.app = context.services["app"]

    def register_routes(self, blueprint):
        @blueprint.get("/status")
        def status():
            return {"status": "must-not-be-active"}

        @blueprint.before_app_request
        def before_request():
            return {"error": "Failed extension still active"}, 503

        @blueprint.app_errorhandler(404)
        def not_found(error):
            return {"error": "Failed extension handler"}, 404

        @blueprint.app_template_filter("marker")
        def marker(value):
            return "failed extension"

        @blueprint.cli.command("extension-command")
        def command():
            pass

        nested = Blueprint("nested", __name__)

        @nested.get("/nested")
        def nested_status():
            return {"ok": True}

        @nested.record_once
        def fail(state):
            self.events.append("initialize")
            if state.app is self.app:
                raise RuntimeError("Registration callback failed")

        blueprint.register_blueprint(nested)

    def close(self):
        self.events.append("close")
""",
        )
        manager = self.create_manager({"events": events, "app": self.app})

        self.assertEqual(manager.extension_ids, ())
        self.assertIn("broken-callback", manager.errors)
        self.assertEqual(events, ["initialize", "close"])
        self.assertEqual([(rule.rule, rule.endpoint) for rule in self.app.url_map.iter_rules()], original_rules)
        self.assertEqual(self.app.view_functions, original_views)
        self.assertEqual(self.app.blueprints, original_blueprints)
        self.assertEqual(self.app.cli.commands, original_commands)
        self.assertIs(self.app.jinja_env.filters["marker"], core_filter)
        self.assertEqual(self.client.get("/core").get_json(), {"core": True})
        for path in ("/api/extensions/broken-callback/status", "/api/extensions/broken-callback/nested"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json(), {"error": "Core not found"})

        self.assertEqual(events, ["initialize", "close", "core request", "core request", "core request"])

    def test_lifecycle_descriptor_errors_do_not_block_other_extensions(self):
        events = []
        self.write_extension(
            "bad-lifecycle",
            """
class Extension:
    def __init__(self, context):
        pass

    @property
    def start(self):
        raise RuntimeError("start descriptor failed")

    @property
    def close(self):
        raise RuntimeError("close descriptor failed")
""",
        )
        self.write_extension(
            "good-lifecycle",
            """
class Extension:
    def __init__(self, context):
        self.events = context.services["events"]

    def start(self):
        self.events.append("start")

    def close(self):
        self.events.append("close")
""",
        )
        manager = self.create_manager({"events": events})

        manager.start()
        manager.close()

        self.assertEqual(events, ["start", "close"])
        self.assertIn("bad-lifecycle", manager.errors)

    def test_directory_without_extension_file_is_ignored(self):
        (self.directory / "notes").mkdir()
        (self.directory / "notes" / "README.md").write_text("notes", encoding="utf-8")
        manager = self.create_manager()
        self.assertEqual(manager.extension_ids, ())
        self.assertEqual(manager.errors, {})


class ApplicationCloseTests(unittest.TestCase):
    def test_listener_stops_before_extension_resources_close(self):
        from src.application.application import Application

        events = []
        application = Application.__new__(Application)
        application._listener_runtime = SimpleNamespace(close=lambda: events.append("listener"))
        application._admin_extension_manager = SimpleNamespace(close=lambda: events.append("extensions"))
        application._agent_config_service = SimpleNamespace(close=lambda: events.append("agent"))
        application._llm_recording_service = SimpleNamespace(close=lambda: events.append("recording"))
        application._log_listener = SimpleNamespace(close=lambda: events.append("logs"))

        application.close()

        self.assertEqual(events, ["listener", "extensions", "agent", "recording", "logs"])


if __name__ == "__main__":
    unittest.main()
