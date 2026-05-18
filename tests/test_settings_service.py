from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.config_manager import ConfigManager
from src.services.settings_service import SettingsService


class FakeLogger:
    def info(self, msg: str, *args: object) -> None:
        del msg, args

    def warning(self, msg: str, *args: object) -> None:
        del msg, args

    def error(self, msg: str, *args: object) -> None:
        del msg, args

    def debug(self, msg: str, *args: object) -> None:
        del msg, args


class SettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = Path(__file__).resolve().parents[1] / "data" / "_test_runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=runtime_root)
        self.root_path = Path(self.temp_dir.name)
        self.config_path = self.root_path / "config.yaml"
        self._write_config(
            {
                "server": {"host": "127.0.0.1", "port": 8080},
                "admin": {"username": "", "password": ""},
                "logging": {
                    "path": str(self.root_path / "logs"),
                    "level": "INFO",
                    "llm_request_debug_enabled": False,
                },
                "providers": [],
                "auth_groups": [],
            }
        )
        self.config_manager = ConfigManager(self.config_path, self.root_path)
        self.ctx = AppContext(
            logger=FakeLogger(),
            config_manager=self.config_manager,
            root_path=self.root_path,
            flask_app=Flask(__name__),
        )
        self.service = SettingsService(self.ctx)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, payload: dict) -> None:
        with self.config_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)

    def test_oauth_settings_default_to_direct_connection_without_ssl_verify(self) -> None:
        settings = self.service.get_system_settings()

        self.assertFalse(settings["oauth"]["enabled"])
        self.assertEqual("", settings["oauth"]["proxy"])
        self.assertFalse(settings["oauth"]["verify_ssl"])
        self.assertFalse(self.config_manager.is_oauth_enabled())
        self.assertIsNone(self.config_manager.get_oauth_proxy())
        self.assertFalse(self.config_manager.is_oauth_verify_ssl_enabled())

    def test_update_oauth_settings_persists_network_flags(self) -> None:
        result = self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy": " http://127.0.0.1:7890 ",
                    "verify_ssl": True,
                }
            }
        )

        self.assertTrue(result["settings"]["oauth"]["enabled"])
        self.assertEqual("http://127.0.0.1:7890", result["settings"]["oauth"]["proxy"])
        self.assertTrue(result["settings"]["oauth"]["verify_ssl"])
        self.assertTrue(self.config_manager.is_oauth_enabled())
        self.assertEqual("http://127.0.0.1:7890", self.config_manager.get_oauth_proxy())
        self.assertTrue(self.config_manager.is_oauth_verify_ssl_enabled())

        with self.config_path.open("r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertTrue(persisted["oauth"]["enabled"])
        self.assertEqual("http://127.0.0.1:7890", persisted["oauth"]["proxy"])
        self.assertTrue(persisted["oauth"]["verify_ssl"])

    def test_update_oauth_settings_rejects_invalid_enabled_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected a boolean value"):
            self.service.update_oauth_settings(
                {
                    "oauth": {
                        "enabled": "sometimes",
                        "proxy": "",
                        "verify_ssl": False,
                    }
                }
            )

    def test_update_oauth_settings_rejects_relative_proxy_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "OAuth proxy must be a valid absolute URL"):
            self.service.update_oauth_settings(
                {
                    "oauth": {
                        "proxy": "127.0.0.1:7890",
                        "verify_ssl": False,
                    }
                }
            )

    def test_update_system_settings_writes_once_after_full_validation(self) -> None:
        write_calls: list[dict] = []
        reload_calls: list[bool] = []
        original_write_raw_config = self.config_manager.write_raw_config

        def record_write(config: dict) -> None:
            write_calls.append(config)
            original_write_raw_config(config)

        self.config_manager.write_raw_config = record_write  # type: ignore[method-assign]
        service = SettingsService(
            self.ctx,
            reload_logging_callback=lambda: reload_calls.append(True),
        )

        result = service.update_system_settings(
            {
                "server": {"host": "0.0.0.0", "port": 9090},
                "admin": {"username": "admin", "password": "secret"},
                "logging": {
                    "path": str(self.root_path / "new-logs"),
                    "level": "DEBUG",
                    "llm_request_debug_enabled": True,
                },
                "oauth": {
                    "enabled": True,
                    "proxy": "http://127.0.0.1:7890",
                    "verify_ssl": True,
                },
            }
        )

        self.assertEqual(1, len(write_calls))
        self.assertEqual([True], reload_calls)
        self.assertTrue(result["auth_config_changed"])
        self.assertTrue(result["server_restart_required"])
        self.assertTrue(result["settings"]["logging"]["llm_request_debug_enabled"])
        self.assertTrue(result["settings"]["oauth"]["enabled"])

    def test_update_system_settings_rejects_invalid_debug_without_partial_write(self) -> None:
        with self.config_path.open("r", encoding="utf-8") as handle:
            before = yaml.safe_load(handle)

        with self.assertRaisesRegex(ValueError, "Log path is required"):
            self.service.update_system_settings(
                {
                    "server": {"host": "0.0.0.0", "port": 9090},
                    "admin": {"username": "admin", "password": "secret"},
                    "logging": {
                        "path": "",
                        "level": "DEBUG",
                        "llm_request_debug_enabled": True,
                    },
                }
            )

        with self.config_path.open("r", encoding="utf-8") as handle:
            after = yaml.safe_load(handle)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
