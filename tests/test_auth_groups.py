import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config import build_auth_group_schemas, build_provider_schemas
from src.config.auth_group_manager import AuthGroupManager, AuthGroupSelectionError
from src.config.provider_config import AuthGroupSchema, ProviderConfigSchema
from src.config.config_manager import ConfigManager
from src.repositories import AuthGroupRepository
from src.services.auth_group_service import AuthGroupService
from src.utils.database import create_connection_factory
from src.utils.local_time import parse_local_datetime


class FakeLogger:
    def info(self, msg: str, *args) -> None:
        del msg, args

    def warning(self, msg: str, *args) -> None:
        del msg, args

    def error(self, msg: str, *args) -> None:
        del msg, args

    def debug(self, msg: str, *args) -> None:
        del msg, args


class AuthGroupConfigTests(unittest.TestCase):
    def test_provider_rejects_multiple_auth_bindings(self) -> None:
        with self.assertRaisesRegex(ValueError, "either auth_group or api_key, not both"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "auth_group": "pool-a",
                    "api_key": "sk-demo",
                    "model_list": ["gpt-4.1"],
                }
            )

    def test_provider_allows_empty_auth_binding_for_hook_or_public_upstream(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertIsNone(schema.auth_group)
        self.assertIsNone(schema.api_key)

    def test_build_provider_schemas_rejects_unknown_auth_group_even_when_no_groups_exist(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown auth_group: pool-a"):
            build_provider_schemas(
                [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "auth_group": "pool-a",
                        "model_list": ["gpt-4.1"],
                    }
                ],
                available_auth_group_names=set(),
            )


class AuthGroupManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "auth-groups.db"
        self.ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(self.temp_dir.name),
            flask_app=Flask(__name__),
        )
        self.repository = AuthGroupRepository(create_connection_factory(db_path))
        self.manager = AuthGroupManager(self.ctx, self.repository)
        self.group = AuthGroupSchema.from_mapping(
            {
                "name": "pool-a",
                "strategy": "least_inflight",
                "cooldown_seconds_on_429": 30,
                "entries": [
                    {
                        "id": "key-a",
                        "headers": {"Authorization": "Bearer sk-a"},
                        "max_concurrency": 2,
                        "cooldown_seconds_on_429": 45,
                    },
                    {
                        "id": "key-b",
                        "headers": {"Authorization": "Bearer sk-b"},
                        "max_concurrency": 2,
                    },
                ],
            }
        )
        self.manager.load_auth_groups((self.group,))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_least_inflight_selection_uses_rotation_to_spread_equal_load(self) -> None:
        first = self.manager.acquire("pool-a")
        second = self.manager.acquire("pool-a")

        self.assertEqual("key-a", first.entry_id)
        self.assertEqual("key-b", second.entry_id)

    def test_finish_success_persists_request_and_token_usage(self) -> None:
        selection = self.manager.acquire("pool-a")
        self.manager.mark_request_dispatched(selection)
        self.manager.finish(
            selection,
            status_code=200,
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
        )

        runtime = self.manager.get_auth_group_runtime("pool-a")
        entry = next(item for item in runtime["entries"] if item["id"] == "key-a")

        self.assertEqual("available", entry["status"])
        self.assertEqual(1, entry["minute_request_count"])
        self.assertEqual(20, entry["minute_total_tokens"])

    def test_429_cools_down_only_current_entry_using_retry_after_priority(self) -> None:
        selection = self.manager.acquire("pool-a")
        self.manager.mark_request_dispatched(selection)
        self.manager.finish(
            selection,
            status_code=429,
            response_headers={"Retry-After": "120"},
            error_message="rate limited",
        )

        runtime = self.manager.get_auth_group_runtime("pool-a")
        cooled_entry = next(item for item in runtime["entries"] if item["id"] == "key-a")
        other_entry = next(item for item in runtime["entries"] if item["id"] == "key-b")

        self.assertEqual("cooldown", cooled_entry["status"])
        cooldown_until = parse_local_datetime(cooled_entry["cooldown_until"])
        self.assertIsNotNone(cooldown_until)
        self.assertEqual("available", other_entry["status"])

        next_selection = self.manager.acquire("pool-a")
        self.assertEqual("key-b", next_selection.entry_id)

    def test_clear_entry_cooldown_restores_entry_to_available(self) -> None:
        selection = self.manager.acquire("pool-a")
        self.manager.mark_request_dispatched(selection)
        self.manager.finish(
            selection,
            status_code=429,
            response_headers={"Retry-After": "120"},
            error_message="rate limited",
        )

        self.manager.clear_entry_cooldown("pool-a", "key-a")
        runtime = self.manager.get_auth_group_runtime("pool-a")
        entry = next(item for item in runtime["entries"] if item["id"] == "key-a")

        self.assertEqual("available", entry["status"])
        self.assertIsNone(entry["cooldown_until"])

    def test_401_disables_entry_until_restore(self) -> None:
        selection = self.manager.acquire("pool-a")
        self.manager.mark_request_dispatched(selection)
        self.manager.finish(selection, status_code=401, error_message="bad key")

        runtime = self.manager.get_auth_group_runtime("pool-a")
        disabled_entry = next(item for item in runtime["entries"] if item["id"] == "key-a")
        self.assertEqual("disabled", disabled_entry["status"])

        next_selection = self.manager.acquire("pool-a")
        self.assertEqual("key-b", next_selection.entry_id)

        self.manager.restore_entry("pool-a", "key-a")
        restored_runtime = self.manager.get_auth_group_runtime("pool-a")
        restored_entry = next(item for item in restored_runtime["entries"] if item["id"] == "key-a")
        self.assertEqual("available", restored_entry["status"])

    def test_manual_disable_and_enable_entry_updates_runtime_status(self) -> None:
        self.manager.set_entry_disabled("pool-a", "key-a", disabled=True)
        disabled_runtime = self.manager.get_auth_group_runtime("pool-a")
        disabled_entry = next(item for item in disabled_runtime["entries"] if item["id"] == "key-a")

        self.assertEqual("disabled", disabled_entry["status"])
        self.assertEqual("manual_disabled", disabled_entry["disabled_reason"])

        self.manager.set_entry_disabled("pool-a", "key-a", disabled=False)
        enabled_runtime = self.manager.get_auth_group_runtime("pool-a")
        enabled_entry = next(item for item in enabled_runtime["entries"] if item["id"] == "key-a")

        self.assertEqual("available", enabled_entry["status"])
        self.assertIsNone(enabled_entry["disabled_reason"])

    def test_reset_entry_minute_usage_clears_only_current_minute_bucket(self) -> None:
        selection = self.manager.acquire("pool-a")
        self.manager.mark_request_dispatched(selection)
        self.manager.finish(
            selection,
            status_code=200,
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        )

        before_reset = self.manager.get_auth_group_runtime("pool-a")
        before_entry = next(item for item in before_reset["entries"] if item["id"] == "key-a")
        self.assertEqual(1, before_entry["minute_request_count"])
        self.assertEqual(15, before_entry["minute_total_tokens"])
        self.assertEqual(1, before_entry["day_request_count"])
        self.assertEqual(15, before_entry["day_total_tokens"])

        self.manager.reset_entry_minute_usage("pool-a", "key-a")
        after_reset = self.manager.get_auth_group_runtime("pool-a")
        after_entry = next(item for item in after_reset["entries"] if item["id"] == "key-a")

        self.assertEqual(0, after_entry["minute_request_count"])
        self.assertEqual(0, after_entry["minute_total_tokens"])
        self.assertEqual(1, after_entry["day_request_count"])
        self.assertEqual(15, after_entry["day_total_tokens"])

    def test_reset_entry_runtime_clears_runtime_flags_and_current_usage(self) -> None:
        selection = self.manager.acquire("pool-a")
        self.manager.mark_request_dispatched(selection)
        self.manager.finish(
            selection,
            status_code=429,
            response_headers={"Retry-After": "120"},
            error_message="rate limited",
        )
        self.manager.set_entry_disabled("pool-a", "key-a", disabled=True)

        before_reset = self.manager.get_auth_group_runtime("pool-a")
        before_entry = next(item for item in before_reset["entries"] if item["id"] == "key-a")
        self.assertEqual("disabled", before_entry["status"])
        self.assertEqual("manual_disabled", before_entry["disabled_reason"])
        self.assertIsNotNone(before_entry["cooldown_until"])
        self.assertEqual(429, before_entry["last_status_code"])
        self.assertEqual("rate limited", before_entry["last_error_message"])
        self.assertEqual(1, before_entry["minute_request_count"])
        self.assertEqual(1, before_entry["day_request_count"])

        self.manager.reset_entry_runtime("pool-a", "key-a")

        after_reset = self.manager.get_auth_group_runtime("pool-a")
        after_entry = next(item for item in after_reset["entries"] if item["id"] == "key-a")
        self.assertEqual("available", after_entry["status"])
        self.assertFalse(after_entry["disabled"])
        self.assertIsNone(after_entry["disabled_reason"])
        self.assertIsNone(after_entry["cooldown_until"])
        self.assertIsNone(after_entry["last_status_code"])
        self.assertIsNone(after_entry["last_error_type"])
        self.assertIsNone(after_entry["last_error_message"])
        self.assertEqual(0, after_entry["minute_request_count"])
        self.assertEqual(0, after_entry["day_request_count"])
        self.assertEqual(0, after_entry["minute_total_tokens"])
        self.assertEqual(0, after_entry["day_total_tokens"])


class AuthGroupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.config_path = self.root_path / "config.yaml"
        self.db_path = self.root_path / "requests.db"
        self._write_config({"auth_groups": [], "providers": []})
        self.config_manager = ConfigManager(self.config_path, self.root_path)
        self.ctx = AppContext(
            logger=FakeLogger(),
            config_manager=self.config_manager,
            root_path=self.root_path,
            flask_app=Flask(__name__),
        )
        self.repository = AuthGroupRepository(create_connection_factory(self.db_path))
        self.manager = AuthGroupManager(self.ctx, self.repository)
        self.reload_count = 0

        def reload_callback() -> None:
            self.reload_count += 1
            self.config_manager.reload()
            raw = self.config_manager.get_raw_config()
            self.manager.load_auth_groups(build_auth_group_schemas(raw.get("auth_groups", []) or []))

        self.reload_callback = reload_callback
        self.service = AuthGroupService(self.ctx, self.reload_callback, self.manager)
        self.reload_callback()
        self.reload_count = 0

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, payload) -> None:
        with open(self.config_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)

    def test_create_auth_group_persists_and_lists_summary(self) -> None:
        created = self.service.create_auth_group(
            {
                "name": "pool-a",
                "strategy": "least_inflight",
                "cooldown_seconds_on_429": 60,
                "entries": [
                    {
                        "id": "key-a",
                        "headers": {"Authorization": "Bearer sk-a"},
                    }
                ],
            }
        )

        self.assertEqual("pool-a", created["name"])
        self.assertEqual(1, self.reload_count)
        listed = self.service.list_auth_groups()
        self.assertEqual(["pool-a"], [item["name"] for item in listed])

    def test_import_auth_entries_accepts_yaml_list(self) -> None:
        entries = self.service.import_auth_entries(
            """
- id: key-a
  headers:
    Authorization: Bearer sk-a
  max_concurrency: 3
- id: key-b
  headers:
    x-api-key: abc
"""
        )

        self.assertEqual(2, len(entries))
        self.assertEqual("key-a", entries[0]["id"])
        self.assertEqual("Bearer sk-a", entries[0]["headers"]["Authorization"])
        self.assertEqual("key-b", entries[1]["id"])

    def test_import_auth_entries_accepts_entries_wrapper(self) -> None:
        entries = self.service.import_auth_entries(
            """
entries:
  - id: key-a
    headers:
      Authorization: Bearer sk-a
"""
        )

        self.assertEqual(1, len(entries))
        self.assertEqual("key-a", entries[0]["id"])

    def test_import_auth_entries_rejects_invalid_yaml_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "entries 必须是列表"):
            self.service.import_auth_entries(
                """
entries:
  id: key-a
  headers:
    Authorization: Bearer sk-a
"""
            )

    def test_import_auth_entries_rejects_duplicate_entry_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "检测到重复的 Auth Entry ID: key-a"):
            self.service.import_auth_entries(
                """
entries:
  - id: key-a
    headers:
      Authorization: Bearer sk-a
  - id: key-a
    headers:
      Authorization: Bearer sk-b
"""
            )

    def test_update_auth_group_renames_provider_reference(self) -> None:
        self._write_config(
            {
                "auth_groups": [
                    {
                        "name": "pool-a",
                        "strategy": "least_inflight",
                        "entries": [{"id": "key-a", "headers": {"Authorization": "Bearer sk-a"}}],
                    }
                ],
                "providers": [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "auth_group": "pool-a",
                        "model_list": ["gpt-4.1"],
                    }
                ],
            }
        )
        self.reload_callback()

        updated = self.service.update_auth_group(
            "pool-a",
            {
                "name": "pool-b",
                "strategy": "least_inflight",
                "entries": [{"id": "key-a", "headers": {"Authorization": "Bearer sk-a"}}],
            },
        )

        self.assertEqual("pool-b", updated["name"])
        current_config = self.config_manager.get_raw_config()
        self.assertEqual("pool-b", current_config["providers"][0]["auth_group"])

    def test_delete_referenced_auth_group_is_rejected(self) -> None:
        self._write_config(
            {
                "auth_groups": [
                    {
                        "name": "pool-a",
                        "strategy": "least_inflight",
                        "entries": [{"id": "key-a", "headers": {"Authorization": "Bearer sk-a"}}],
                    }
                ],
                "providers": [
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "auth_group": "pool-a",
                        "model_list": ["gpt-4.1"],
                    }
                ],
            }
        )
        self.reload_callback()

        with self.assertRaisesRegex(ValueError, "still referenced"):
            self.service.delete_auth_group("pool-a")


if __name__ == "__main__":
    unittest.main()
