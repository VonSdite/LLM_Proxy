from __future__ import annotations

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
        runtime_root = Path(__file__).resolve().parents[1] / "data" / "_test_runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=runtime_root)
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

    def _current_provider_names(self) -> list[str]:
        current_config = self.config_manager.get_raw_config()
        return [item["name"] for item in current_config["providers"]]

    def test_config_manager_migrates_legacy_target_format_on_load(self) -> None:
        self._write_config(
            {
                "auth_groups": [],
                "providers": [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-demo",
                        "target_format": "claude_chat",
                        "model_list": ["gpt-4.1"],
                    }
                ],
            }
        )

        migrated_manager = ConfigManager(self.config_path, self.root_path)

        current_config = migrated_manager.get_raw_config()
        provider = current_config["providers"][0]
        self.assertNotIn("target_format", provider)
        self.assertEqual(["claude_chat"], provider["target_formats"])

        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        persisted_provider = persisted["providers"][0]
        self.assertNotIn("target_format", persisted_provider)
        self.assertEqual(["claude_chat"], persisted_provider["target_formats"])

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
        self.assertNotIn("target_format", updated)
        self.assertEqual(["openai_chat"], updated["target_formats"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertFalse(current_config["providers"][0]["enabled"])
        self.assertEqual("example_hook.py", current_config["providers"][0]["hook"])
        self.assertNotIn("target_format", current_config["providers"][0])
        self.assertEqual(["openai_chat"], current_config["providers"][0]["target_formats"])

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
        self.assertNotIn("target_format", updated)
        self.assertEqual(["openai_chat"], updated["target_formats"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertFalse(current_config["providers"][0]["enabled"])
        self.assertNotIn("target_format", current_config["providers"][0])

    def test_create_provider_inserts_enabled_provider_before_disabled_group(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        created = self.service.create_provider(
            {
                "name": "enabled-b",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "sk-c",
                "model_list": ["gpt-4.1-nano"],
            }
        )

        self.assertEqual("enabled-b", created["name"])
        self.assertEqual(1, self.reload_count)
        self.assertEqual(
            ["enabled-a", "enabled-b", "disabled-a"],
            self._current_provider_names(),
        )

    def test_set_provider_enabled_moves_disabled_provider_to_first_disabled_position(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "enabled-b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-c",
                    "model_list": ["gpt-4.1-nano"],
                },
            ]
        )

        self.service.set_provider_enabled("enabled-b", False)

        self.assertEqual(
            ["enabled-a", "enabled-b", "disabled-a"],
            self._current_provider_names(),
        )

    def test_set_provider_enabled_moves_enabled_provider_to_last_enabled_position(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "enabled-b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-c",
                    "model_list": ["gpt-4.1-nano"],
                },
                {
                    "name": "disabled-b",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-d",
                    "model_list": ["gpt-4.1-micro"],
                },
            ]
        )

        self.service.set_provider_enabled("disabled-b", True)

        self.assertEqual(
            ["enabled-a", "enabled-b", "disabled-b", "disabled-a"],
            self._current_provider_names(),
        )

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
        self.assertTrue(all("target_format" not in item for item in current_config["providers"]))

    def test_batch_disable_providers_preserves_stable_relative_order(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "enabled-b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
                {
                    "name": "enabled-c",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-c",
                    "model_list": ["gpt-4.1-nano"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-d",
                    "model_list": ["gpt-4.1-micro"],
                },
            ]
        )

        self.service.batch_set_provider_enabled(["enabled-c", "enabled-a"], False)

        self.assertEqual(
            ["enabled-b", "enabled-a", "enabled-c", "disabled-a"],
            self._current_provider_names(),
        )

    def test_batch_enable_providers_preserves_stable_relative_order(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
                {
                    "name": "disabled-b",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-c",
                    "model_list": ["gpt-4.1-nano"],
                },
                {
                    "name": "disabled-c",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-d",
                    "model_list": ["gpt-4.1-micro"],
                },
            ]
        )

        self.service.batch_set_provider_enabled(["disabled-c", "disabled-a"], True)

        self.assertEqual(
            ["enabled-a", "disabled-a", "disabled-c", "disabled-b"],
            self._current_provider_names(),
        )

    def test_reorder_providers_updates_config_order(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "enabled-b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-c",
                    "model_list": ["gpt-4.1-nano"],
                },
            ]
        )

        result = self.service.reorder_providers(
            ["enabled-b", "enabled-a", "disabled-a"]
        )

        self.assertEqual(3, result["count"])
        self.assertEqual(
            ["enabled-b", "enabled-a", "disabled-a"],
            result["names"],
        )
        self.assertEqual(1, self.reload_count)
        self.assertEqual(
            ["enabled-b", "enabled-a", "disabled-a"],
            self._current_provider_names(),
        )

    def test_reorder_providers_rejects_missing_name(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        with self.assertRaisesRegex(
            ValueError, "Provider order must include every provider exactly once"
        ):
            self.service.reorder_providers(["enabled-a"])

    def test_reorder_providers_rejects_duplicate_name(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        with self.assertRaisesRegex(
            ValueError, "Duplicate provider name in order list: enabled-a"
        ):
            self.service.reorder_providers(["enabled-a", "enabled-a"])

    def test_reorder_providers_rejects_unknown_name(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        with self.assertRaisesRegex(
            ValueError, "Provider order must include every provider exactly once"
        ):
            self.service.reorder_providers(["enabled-a", "unknown-provider"])

    def test_reorder_providers_rejects_disabled_provider_before_enabled_provider(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled-a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "disabled-a",
                    "enabled": False,
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        with self.assertRaisesRegex(
            ValueError, "Enabled providers must appear before disabled providers"
        ):
            self.service.reorder_providers(["disabled-a", "enabled-a"])

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
