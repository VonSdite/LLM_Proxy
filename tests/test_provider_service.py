from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import yaml
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.config_manager import ConfigManager
from src.repositories.auth_group_repository import AuthGroupRepository
from src.services.provider_service import ProviderService
from src.utils.database import create_connection_factory


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
        self.db_path = self.root_path / "requests.db"
        self._write_config({"auth_groups": [], "providers": []})
        self.config_manager = ConfigManager(self.config_path, self.root_path)
        self.auth_group_repository = AuthGroupRepository(create_connection_factory(self.db_path))
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
        self.service = ProviderService(ctx, reload_callback, self.auth_group_repository)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, payload) -> None:
        with open(self.config_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)

    def _seed_providers(self, providers) -> None:
        self._write_config({"auth_groups": [], "providers": providers})
        self.config_manager.reload()
        self.reload_count = 0

    def _write_hook_file(self, relative_path: str) -> None:
        hook_file = self.root_path / "hooks" / relative_path
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text("class Hook:\n    pass\n", encoding="utf-8")

    def _current_provider_names(self) -> list[str]:
        current_config = self.config_manager.get_raw_config()
        return [item["name"] for item in current_config["providers"]]

    def _current_auth_group_names(self) -> list[str]:
        current_config = self.config_manager.get_raw_config()
        return [item["name"] for item in current_config["auth_groups"]]

    def test_config_manager_removes_legacy_target_format_on_load(self) -> None:
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
        self.assertNotIn("target_formats", provider)

        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        persisted_provider = persisted["providers"][0]
        self.assertNotIn("target_format", persisted_provider)
        self.assertNotIn("target_formats", persisted_provider)

    def test_config_manager_removes_legacy_transport_on_load(self) -> None:
        self._write_config(
            {
                "auth_groups": [],
                "providers": [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-demo",
                        "transport": "http",
                        "model_list": ["gpt-4.1"],
                    }
                ],
            }
        )

        migrated_manager = ConfigManager(self.config_path, self.root_path)

        provider = migrated_manager.get_raw_config()["providers"][0]
        self.assertNotIn("transport", provider)

        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        persisted_provider = persisted["providers"][0]
        self.assertNotIn("transport", persisted_provider)

    def test_config_manager_normalizes_legacy_codex_protocol_aliases_on_load(self) -> None:
        self._write_config(
            {
                "auth_groups": [],
                "providers": [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/responses",
                        "api_key": "sk-demo",
                        "source_format": "codex",
                        "target_formats": ["codex"],
                        "model_list": ["gpt-5-codex"],
                    }
                ],
            }
        )

        migrated_manager = ConfigManager(self.config_path, self.root_path)

        provider = migrated_manager.get_raw_config()["providers"][0]
        self.assertEqual("openai_responses", provider["source_format"])
        self.assertNotIn("target_formats", provider)

        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        persisted_provider = persisted["providers"][0]
        self.assertEqual("openai_responses", persisted_provider["source_format"])
        self.assertNotIn("target_formats", persisted_provider)

    def test_config_manager_backfills_provider_proxy_mode_on_load(self) -> None:
        self._write_config(
            {
                "auth_groups": [],
                "providers": [
                    {
                        "name": "direct-provider",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-direct",
                        "model_list": ["gpt-4.1"],
                    },
                    {
                        "name": "custom-provider",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-custom",
                        "proxy": "http://127.0.0.1:7890",
                        "model_list": ["gpt-4.1-mini"],
                    },
                ],
            }
        )

        migrated_manager = ConfigManager(self.config_path, self.root_path)

        providers = migrated_manager.get_raw_config()["providers"]
        self.assertEqual("direct", providers[0]["proxy_mode"])
        self.assertEqual("custom", providers[1]["proxy_mode"])

        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        persisted_providers = persisted["providers"]
        self.assertEqual("direct", persisted_providers[0]["proxy_mode"])
        self.assertEqual("custom", persisted_providers[1]["proxy_mode"])

    def test_config_manager_backfills_oauth_proxy_mode_on_load(self) -> None:
        self._write_config(
            {
                "auth_groups": [],
                "oauth": {
                    "enabled": True,
                    "proxy": "http://127.0.0.1:7890",
                    "verify_ssl": False,
                },
                "providers": [],
            }
        )

        migrated_manager = ConfigManager(self.config_path, self.root_path)

        self.assertEqual("custom", migrated_manager.get_raw_config()["oauth"]["proxy_mode"])
        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertEqual("custom", persisted["oauth"]["proxy_mode"])

    def test_config_manager_backfills_oauth_direct_without_providers_on_load(self) -> None:
        self._write_config(
            {
                "auth_groups": [],
                "oauth": {
                    "enabled": False,
                    "proxy": "",
                    "verify_ssl": False,
                },
            }
        )

        migrated_manager = ConfigManager(self.config_path, self.root_path)

        self.assertEqual("direct", migrated_manager.get_raw_config()["oauth"]["proxy_mode"])
        with open(self.config_path, "r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertEqual("direct", persisted["oauth"]["proxy_mode"])

    def test_update_provider_preserves_enabled_when_payload_omits_enabled(self) -> None:
        self._write_hook_file("example_hook.py")
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
        self.assertNotIn("target_formats", updated)
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertFalse(current_config["providers"][0]["enabled"])
        self.assertEqual("example_hook.py", current_config["providers"][0]["hook"])
        self.assertNotIn("target_format", current_config["providers"][0])
        self.assertNotIn("target_formats", current_config["providers"][0])

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
        self.assertNotIn("target_formats", updated)
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertFalse(current_config["providers"][0]["enabled"])
        self.assertNotIn("target_format", current_config["providers"][0])
        self.assertNotIn("target_formats", current_config["providers"][0])

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
                "name": "enabledB",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "sk-c",
                "model_list": ["gpt-4.1-nano"],
            }
        )

        self.assertEqual("enabledB", created["name"])
        self.assertEqual(1, self.reload_count)
        self.assertEqual(
            ["enabled-a", "enabledB", "disabled-a"],
            self._current_provider_names(),
        )

    def test_legacy_invalid_provider_name_can_be_listed_and_toggled(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "legacy/provider",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-legacy",
                    "model_list": ["gpt-4.1"],
                }
            ]
        )

        listed = self.service.list_providers()
        updated = self.service.set_provider_enabled("legacy/provider", False)

        self.assertEqual("legacy/provider", listed[0]["name"])
        self.assertEqual("legacy/provider", updated["name"])
        self.assertFalse(updated["enabled"])
        self.assertEqual(1, self.reload_count)
        current_config = self.config_manager.get_raw_config()
        self.assertEqual("legacy/provider", current_config["providers"][0]["name"])
        self.assertFalse(current_config["providers"][0]["enabled"])

    def test_update_provider_rejects_legacy_invalid_name_until_renamed(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "legacy/provider",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-legacy",
                    "model_list": ["gpt-4.1"],
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "Provider name must start with a letter"):
            self.service.update_provider(
                "legacy/provider",
                {
                    "name": "legacy/provider",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-legacy",
                    "model_list": ["gpt-4.1"],
                },
            )

        updated = self.service.update_provider(
            "legacy/provider",
            {
                "name": "legacyProvider1",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "sk-legacy",
                "model_list": ["gpt-4.1"],
            },
        )

        self.assertEqual("legacyProvider1", updated["name"])
        self.assertEqual(["legacyProvider1"], self._current_provider_names())

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
        self.assertTrue(all("target_formats" not in item for item in current_config["providers"]))

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

        result = self.service.reorder_providers(["enabled-b", "enabled-a", "disabled-a"])

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

        with self.assertRaisesRegex(ValueError, "Provider order must include every provider exactly once"):
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

        with self.assertRaisesRegex(ValueError, "Duplicate provider name in order list: enabled-a"):
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

        with self.assertRaisesRegex(ValueError, "Provider order must include every provider exactly once"):
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

        with self.assertRaisesRegex(ValueError, "Enabled providers must appear before disabled providers"):
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

    def test_copy_provider_inserts_copy_below_source(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "enabled_a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "enabled_b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        copied = self.service.copy_provider("enabled_a")

        self.assertEqual("enabled_a_1", copied["name"])
        self.assertEqual(1, self.reload_count)
        self.assertEqual(
            ["enabled_a", "enabled_a_1", "enabled_b"],
            self._current_provider_names(),
        )

    def test_import_providers_renames_duplicate_names(self) -> None:
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

        result = self.service.import_providers(
            {
                "providers": [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-imported",
                        "model_list": ["gpt-4.1-mini"],
                    },
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-imported-2",
                        "model_list": ["gpt-4.1-nano"],
                    },
                ]
            }
        )

        self.assertEqual(2, result["count"])
        self.assertEqual(["demo_1", "demo_2"], result["names"])
        self.assertEqual(
            [
                {"from": "demo", "to": "demo_1"},
                {"from": "demo", "to": "demo_2"},
            ],
            result["renamed"],
        )
        self.assertEqual(["demo", "demo_1", "demo_2"], self._current_provider_names())

    def test_import_providers_drops_missing_hook(self) -> None:
        result = self.service.import_providers(
            {
                "providers": [
                    {
                        "name": "imported",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-imported",
                        "model_list": ["gpt-4.1"],
                        "hook": "missing_hook.py",
                    }
                ]
            }
        )

        self.assertEqual(1, result["count"])
        provider = self.service.get_provider("imported")
        self.assertIsNotNone(provider)
        self.assertNotIn("hook", provider)
        current_config = self.config_manager.get_raw_config()
        self.assertNotIn("hook", current_config["providers"][0])

    def test_import_providers_preserves_existing_hook(self) -> None:
        self._write_hook_file("custom/demo_hook.py")

        result = self.service.import_providers(
            {
                "providers": [
                    {
                        "name": "imported",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "sk-imported",
                        "model_list": ["gpt-4.1"],
                        "hook": "custom/demo_hook.py",
                    }
                ]
            }
        )

        self.assertEqual(1, result["count"])
        provider = self.service.get_provider("imported")
        self.assertIsNotNone(provider)
        self.assertEqual("custom/demo_hook.py", provider["hook"])
        current_config = self.config_manager.get_raw_config()
        self.assertEqual("custom/demo_hook.py", current_config["providers"][0]["hook"])

    def test_export_providers_preserves_requested_order(self) -> None:
        self._seed_providers(
            [
                {
                    "name": "provider_a",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-a",
                    "model_list": ["gpt-4.1"],
                },
                {
                    "name": "provider_b",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-b",
                    "model_list": ["gpt-4.1-mini"],
                },
            ]
        )

        exported = self.service.export_providers(["provider_b", "provider_a"])

        self.assertEqual("llm_proxy.providers", exported["kind"])
        self.assertEqual(
            ["provider_b", "provider_a"],
            [provider["name"] for provider in exported["providers"]],
        )

    def test_export_providers_includes_referenced_auth_groups(self) -> None:
        self._write_config(
            {
                "auth_groups": [
                    {
                        "name": "shared-auth",
                        "entries": [
                            {
                                "id": "primary",
                                "headers": {"Authorization": "Bearer sk-auth"},
                            }
                        ],
                    }
                ],
                "providers": [
                    {
                        "name": "provider_a",
                        "api": "https://example.com/v1/chat/completions",
                        "auth_group": "shared-auth",
                        "model_list": ["gpt-4.1"],
                    }
                ],
            }
        )
        self.config_manager.reload()
        self.auth_group_repository.save_entry_runtime_state(
            "shared-auth",
            "primary",
            disabled=True,
            disabled_reason="manual",
            cooldown_until="2026-04-08 10:00:00.000000",
            last_status_code=429,
            last_error_type="rate_limit",
            last_error_message="quota exceeded",
        )
        self.auth_group_repository.increment_request_usage(
            "shared-auth",
            "primary",
            datetime(2026, 4, 8, 9, 15, 0),
        )

        exported = self.service.export_providers(["provider_a"])

        self.assertEqual(["provider_a"], [provider["name"] for provider in exported["providers"]])
        self.assertEqual(["shared-auth"], [auth_group["name"] for auth_group in exported["auth_groups"]])
        self.assertEqual(
            {"Authorization": "Bearer sk-auth"},
            exported["auth_groups"][0]["entries"][0]["headers"],
        )
        self.assertEqual(["shared-auth"], [row["auth_group_name"] for row in exported["auth_entry_runtime_state"]])
        self.assertEqual("primary", exported["auth_entry_runtime_state"][0]["entry_id"])
        self.assertTrue(exported["auth_entry_runtime_state"][0]["disabled"])
        self.assertEqual(
            {"minute", "day"},
            {row["bucket_type"] for row in exported["auth_entry_usage_buckets"]},
        )

    def test_auth_group_repository_import_skips_existing_table_rows(self) -> None:
        self.auth_group_repository.save_entry_runtime_state(
            "shared-auth",
            "existing",
            disabled=False,
            disabled_reason=None,
            cooldown_until=None,
            last_status_code=200,
            last_error_type=None,
            last_error_message=None,
        )
        runtime_result = self.auth_group_repository.import_runtime_states(
            [
                {
                    "auth_group_name": "shared-auth",
                    "entry_id": "existing",
                    "disabled": True,
                    "disabled_reason": "manual",
                    "cooldown_until": "2026-04-08 10:00:00.000000",
                    "last_status_code": 429,
                    "last_error_type": "rate_limit",
                    "last_error_message": "quota exceeded",
                    "updated_at": "2026-04-08 09:00:00.000000",
                }
            ]
        )

        self.assertEqual(
            {"count": 1, "inserted_count": 0, "updated_count": 0, "skipped_count": 1},
            runtime_result,
        )
        runtime_state = self.auth_group_repository.get_entry_runtime_state("shared-auth", "existing")
        self.assertFalse(runtime_state["disabled"])
        self.assertEqual(200, runtime_state["last_status_code"])

        self.auth_group_repository.import_usage_buckets(
            [
                {
                    "auth_group_name": "shared-auth",
                    "entry_id": "existing",
                    "bucket_type": "day",
                    "bucket_start": "2026-04-08",
                    "request_count": 7,
                    "prompt_tokens": 17,
                    "completion_tokens": 19,
                    "total_tokens": 36,
                    "updated_at": "2026-04-08 08:00:00.000000",
                }
            ]
        )
        usage_result = self.auth_group_repository.import_usage_buckets(
            [
                {
                    "auth_group_name": "shared-auth",
                    "entry_id": "existing",
                    "bucket_type": "day",
                    "bucket_start": "2026-04-08",
                    "request_count": 3,
                    "prompt_tokens": 11,
                    "completion_tokens": 13,
                    "total_tokens": 24,
                    "updated_at": "2026-04-08 09:00:00.000000",
                }
            ]
        )

        self.assertEqual(
            {"count": 1, "inserted_count": 0, "updated_count": 0, "skipped_count": 1},
            usage_result,
        )
        usage_rows = self.auth_group_repository.export_usage_buckets(["shared-auth"])
        self.assertEqual(7, usage_rows[0]["request_count"])
        self.assertEqual(36, usage_rows[0]["total_tokens"])

    def test_import_providers_merges_duplicate_auth_group_entries(self) -> None:
        self._write_config(
            {
                "auth_groups": [
                    {
                        "name": "shared-auth",
                        "entries": [
                            {
                                "id": "existing",
                                "headers": {"Authorization": "Bearer sk-existing"},
                            }
                        ],
                    }
                ],
                "providers": [],
            }
        )
        self.config_manager.reload()
        self.auth_group_repository.save_entry_runtime_state(
            "shared-auth",
            "existing",
            disabled=False,
            disabled_reason=None,
            cooldown_until=None,
            last_status_code=200,
            last_error_type=None,
            last_error_message=None,
        )
        self.auth_group_repository.import_usage_buckets(
            [
                {
                    "auth_group_name": "shared-auth",
                    "entry_id": "existing",
                    "bucket_type": "day",
                    "bucket_start": "2026-04-08",
                    "request_count": 7,
                    "prompt_tokens": 17,
                    "completion_tokens": 19,
                    "total_tokens": 36,
                    "updated_at": "2026-04-08 08:00:00.000000",
                }
            ]
        )

        result = self.service.import_providers(
            {
                "auth_groups": [
                    {
                        "name": "shared-auth",
                        "entries": [
                            {
                                "id": "existing",
                                "headers": {"Authorization": "Bearer sk-overwrite"},
                            },
                            {
                                "id": "imported",
                                "headers": {"Authorization": "Bearer sk-imported"},
                            },
                        ],
                    }
                ],
                "auth_entry_runtime_state": [
                    {
                        "auth_group_name": "shared-auth",
                        "entry_id": "existing",
                        "disabled": True,
                        "disabled_reason": "manual",
                        "cooldown_until": "2026-04-08 10:00:00.000000",
                        "last_status_code": 429,
                        "last_error_type": "rate_limit",
                        "last_error_message": "quota exceeded",
                        "updated_at": "2026-04-08 09:00:00.000000",
                    },
                    {
                        "auth_group_name": "shared-auth",
                        "entry_id": "imported",
                        "disabled": True,
                        "disabled_reason": "manual",
                        "cooldown_until": "2026-04-08 10:00:00.000000",
                        "last_status_code": 429,
                        "last_error_type": "rate_limit",
                        "last_error_message": "quota exceeded",
                        "updated_at": "2026-04-08 09:00:00.000000",
                    },
                ],
                "auth_entry_usage_buckets": [
                    {
                        "auth_group_name": "shared-auth",
                        "entry_id": "existing",
                        "bucket_type": "day",
                        "bucket_start": "2026-04-08",
                        "request_count": 3,
                        "prompt_tokens": 11,
                        "completion_tokens": 13,
                        "total_tokens": 24,
                        "updated_at": "2026-04-08 09:00:00.000000",
                    },
                    {
                        "auth_group_name": "shared-auth",
                        "entry_id": "imported",
                        "bucket_type": "day",
                        "bucket_start": "2026-04-08",
                        "request_count": 3,
                        "prompt_tokens": 11,
                        "completion_tokens": 13,
                        "total_tokens": 24,
                        "updated_at": "2026-04-08 09:00:00.000000",
                    },
                ],
                "providers": [
                    {
                        "name": "provider_a",
                        "api": "https://example.com/v1/chat/completions",
                        "auth_group": "shared-auth",
                        "model_list": ["gpt-4.1"],
                    }
                ],
            }
        )

        self.assertEqual(1, result["auth_groups_count"])
        self.assertEqual([], result["auth_groups_renamed"])
        self.assertEqual(2, result["auth_entry_runtime_state_count"])
        self.assertEqual(1, result["auth_entry_runtime_state_inserted_count"])
        self.assertEqual(1, result["auth_entry_runtime_state_skipped_count"])
        self.assertEqual(0, result["auth_entry_runtime_state_updated_count"])
        self.assertEqual(2, result["auth_entry_usage_buckets_count"])
        self.assertEqual(1, result["auth_entry_usage_buckets_inserted_count"])
        self.assertEqual(1, result["auth_entry_usage_buckets_skipped_count"])
        self.assertEqual(0, result["auth_entry_usage_buckets_updated_count"])
        current_config = self.config_manager.get_raw_config()
        self.assertEqual(["shared-auth"], self._current_auth_group_names())
        self.assertEqual("shared-auth", current_config["providers"][0]["auth_group"])
        entries = {entry["id"]: entry for entry in current_config["auth_groups"][0]["entries"]}
        self.assertEqual({"Authorization": "Bearer sk-existing"}, entries["existing"]["headers"])
        self.assertEqual({"Authorization": "Bearer sk-imported"}, entries["imported"]["headers"])

        existing_runtime_state = self.auth_group_repository.get_entry_runtime_state("shared-auth", "existing")
        self.assertFalse(existing_runtime_state["disabled"])
        self.assertEqual(200, existing_runtime_state["last_status_code"])
        imported_runtime_state = self.auth_group_repository.get_entry_runtime_state("shared-auth", "imported")
        self.assertTrue(imported_runtime_state["disabled"])
        self.assertEqual("manual", imported_runtime_state["disabled_reason"])
        usage_by_entry = {
            row["entry_id"]: row for row in self.auth_group_repository.export_usage_buckets(["shared-auth"])
        }
        self.assertEqual(7, usage_by_entry["existing"]["request_count"])
        self.assertEqual(36, usage_by_entry["existing"]["total_tokens"])
        self.assertEqual(3, usage_by_entry["imported"]["request_count"])
        self.assertEqual(24, usage_by_entry["imported"]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
