import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.config_manager import ConfigManager
from src.services.provider_service import ProviderService


class FakeLogger:
    def info(self, msg: str, *args) -> None:
        del msg, args

    def warning(self, msg: str, *args) -> None:
        del msg, args

    def error(self, msg: str, *args) -> None:
        del msg, args

    def debug(self, msg: str, *args) -> None:
        del msg, args


class ProviderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.config_path = self.root_path / "config.yaml"
        self._write_config({"auth_groups": [], "providers": []})
        self.config_manager = ConfigManager(self.config_path, self.root_path)
        self.reload_count = 0

        def reload_callback() -> None:
            self.reload_count += 1
            self.config_manager.reload()

        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=self.config_manager,
            root_path=self.root_path,
            flask_app=Flask(__name__),
        )
        self.service = ProviderService(ctx, reload_callback)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, payload) -> None:
        with open(self.config_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)

    def _seed_providers(self, providers) -> None:
        self._write_config({"auth_groups": [], "providers": providers})
        self.config_manager.reload()
        self.reload_count = 0

    def test_update_provider_preserves_enabled_when_payload_omits_enabled(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "demo",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-demo",
                    "model_list": ["gpt-4.1"],
                }
            ]
        )

        updated = self.service.update_provider(
            "demo",
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "sk-demo",
                "model_list": ["gpt-4.1"],
                "hook": "example_hook.py",
            },
        )

        self.assertFalse(updated["enabled"])
        self.assertEqual("example_hook.py", updated["hook"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertFalse(current_config["providers"][0]["enabled"])
        self.assertEqual("example_hook.py", current_config["providers"][0]["hook"])

    def test_set_provider_enabled_updates_config(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-demo",
                    "model_list": ["gpt-4.1"],
                }
            ]
        )

        updated = self.service.set_provider_enabled("demo", False)

        self.assertFalse(updated["enabled"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertFalse(current_config["providers"][0]["enabled"])

    def test_batch_set_provider_enabled_updates_multiple_providers(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "demo-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "demo-b",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        result = self.service.batch_set_provider_enabled(["demo-a", "demo-b"], True)

        self.assertEqual(2, result["count"])
        self.assertTrue(result["enabled"])
        self.assertEqual(["demo-a", "demo-b"], result["names"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertEqual([True, True], [item["enabled"] for item in current_config["providers"]])

    def test_batch_delete_providers_removes_selected_entries(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "demo-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "demo-b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        result = self.service.batch_delete_providers(["demo-b"])

        self.assertEqual(1, result["count"])
        self.assertEqual(["demo-b"], result["names"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertEqual(["demo-a"], [item["name"] for item in current_config["providers"]])


if __name__ == "__main__":
    unittest.main()
