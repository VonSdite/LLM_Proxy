from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import yaml
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.config_manager import ConfigManager
from src.services.claude_proxy_service import ClaudeProxyService
from src.services.codex_proxy_service import CodexProxyService
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
        runtime_root = Path(__file__).resolve().parents[1] / "tmp" / "settings-tests"
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
        self.assertEqual("direct", settings["oauth"]["proxy_mode"])
        self.assertEqual("", settings["oauth"]["proxy"])
        self.assertFalse(settings["oauth"]["verify_ssl"])
        self.assertFalse(settings["client_ip"]["real_ip_enabled"])
        self.assertEqual("X-Forwarded-For", settings["client_ip"]["real_ip_header"])
        self.assertFalse(self.config_manager.is_oauth_enabled())
        self.assertEqual("direct", self.config_manager.get_oauth_proxy_mode())
        self.assertIsNone(self.config_manager.get_oauth_proxy())
        self.assertFalse(self.config_manager.is_oauth_verify_ssl_enabled())
        self.assertFalse(self.config_manager.is_real_client_ip_enabled())
        self.assertEqual("X-Forwarded-For", self.config_manager.get_real_client_ip_header())
        self.assertFalse(settings["api_keys"]["enabled"])
        self.assertFalse(self.config_manager.is_api_key_management_enabled())

    def test_oauth_switch_controls_data_plane_without_removing_management_catalog(self) -> None:
        catalog = Mock()
        catalog.list_model_names.return_value = ("text-model",)
        catalog.list_image_model_names.return_value = ("image-model",)
        catalog.has_model.return_value = True
        catalog.has_image_model.return_value = True
        codex = CodexProxyService(self.ctx, catalog)
        claude = ClaudeProxyService(self.ctx, catalog)
        for enabled in (False, True, False):
            with self.subTest(enabled=enabled):
                self.service.update_oauth_settings(
                    {"oauth": {"enabled": enabled, "proxy_mode": "direct", "proxy": "", "verify_ssl": False}}
                )
                for proxy in (codex, claude):
                    self.assertEqual(proxy.has_model("text-model"), enabled)
                    self.assertEqual(proxy.list_model_names(), ("text-model",) if enabled else ())
                    if not enabled:
                        _, status, failure = proxy.proxy_request({"model": "text-model"}, {})
                        self.assertEqual(status, 503)
                        self.assertEqual(failure.error_code, "oauth_disabled")
                self.assertEqual(codex.has_image_model("image-model"), enabled)
                self.assertEqual(codex.list_image_model_names(), ("image-model",) if enabled else ())
                if not enabled:
                    _, status, failure = codex.proxy_image_request({"model": "image-model"}, {}, action="generations")
                    self.assertEqual(status, 503)
                    self.assertEqual(failure.error_code, "oauth_disabled")
                self.assertEqual(catalog.list_model_names(), ("text-model",))
        catalog.list_auth_files.assert_not_called()

    def test_model_mapping_is_always_enabled_with_legacy_config(self) -> None:
        self.assertTrue(self.config_manager.is_model_mapping_enabled())
        config = self.config_manager.get_raw_config()
        config["model_mapping"] = {"enabled": False}
        self.config_manager.write_raw_config(config)
        self.assertTrue(self.config_manager.is_model_mapping_enabled())
        self.assertTrue(self.service.get_system_settings()["model_mapping"]["enabled"])
        result = self.service.update_model_mapping_settings({"model_mapping": {"enabled": False}})
        self.assertTrue(result["settings"]["model_mapping"]["enabled"])
        self.assertTrue(self.config_manager.get_raw_config()["model_mapping"]["enabled"])

    def test_update_api_key_settings_persists_enabled_flag(self) -> None:
        result = self.service.update_api_key_settings(
            {
                "api_keys": {
                    "enabled": True,
                },
            }
        )

        self.assertTrue(result["settings"]["api_keys"]["enabled"])
        self.assertTrue(self.config_manager.is_api_key_management_enabled())

        with self.config_path.open("r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertTrue(persisted["api_keys"]["enabled"])

    def test_update_client_ip_settings_persists_real_client_ip_settings(self) -> None:
        result = self.service.update_client_ip_settings(
            {
                "client_ip": {
                    "real_ip_enabled": True,
                    "real_ip_header": " X-Real-IP ",
                },
            }
        )

        self.assertTrue(result["settings"]["client_ip"]["real_ip_enabled"])
        self.assertEqual("X-Real-IP", result["settings"]["client_ip"]["real_ip_header"])
        self.assertTrue(self.config_manager.is_real_client_ip_enabled())
        self.assertEqual("X-Real-IP", self.config_manager.get_real_client_ip_header())

        with self.config_path.open("r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertTrue(persisted["client_ip"]["real_ip_enabled"])
        self.assertEqual("X-Real-IP", persisted["client_ip"]["real_ip_header"])

    def test_update_client_ip_settings_rejects_invalid_real_client_ip_header(self) -> None:
        with self.assertRaisesRegex(ValueError, "Real client IP header must be a valid HTTP header name"):
            self.service.update_client_ip_settings(
                {
                    "client_ip": {
                        "real_ip_enabled": True,
                        "real_ip_header": "X-Real-IP: nope",
                    },
                }
            )

    def test_basic_settings_updates_admin_and_listener_independently(self) -> None:
        result = self.service.update_basic_settings({"admin": {"username": "test", "password": "secret"}})
        self.assertEqual({"host": "127.0.0.1", "port": 8080}, result["settings"]["server"])
        self.assertTrue(result["auth_config_changed"])
        self.assertFalse(result["server_restart_required"])
        result = self.service.update_basic_settings({"server": {"host": "0.0.0.0", "port": 9000}})
        self.assertEqual({"username": "test", "password": "secret"}, result["settings"]["admin"])
        self.assertFalse(result["auth_config_changed"])
        self.assertTrue(result["server_restart_required"])
        for payload in ({}, {"server": None}, {"admin": None}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.update_basic_settings(payload)

    def test_update_basic_settings_does_not_change_client_ip_settings(self) -> None:
        self.service.update_client_ip_settings(
            {
                "client_ip": {
                    "real_ip_enabled": True,
                    "real_ip_header": "X-Real-IP",
                }
            }
        )

        result = self.service.update_basic_settings(
            {
                "server": {"host": "127.0.0.1", "port": 8080},
                "admin": {"username": "", "password": ""},
                "client_ip": {
                    "real_ip_enabled": False,
                    "real_ip_header": "CF-Connecting-IP",
                },
            }
        )

        self.assertTrue(result["settings"]["client_ip"]["real_ip_enabled"])
        self.assertEqual("X-Real-IP", result["settings"]["client_ip"]["real_ip_header"])

    def test_update_oauth_settings_persists_network_flags(self) -> None:
        result = self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "custom",
                    "proxy": " http://127.0.0.1:7890 ",
                    "verify_ssl": True,
                }
            }
        )

        self.assertTrue(result["settings"]["oauth"]["enabled"])
        self.assertEqual("custom", result["settings"]["oauth"]["proxy_mode"])
        self.assertEqual("http://127.0.0.1:7890", result["settings"]["oauth"]["proxy"])
        self.assertTrue(result["settings"]["oauth"]["verify_ssl"])
        self.assertTrue(self.config_manager.is_oauth_enabled())
        self.assertEqual("custom", self.config_manager.get_oauth_proxy_mode())
        self.assertEqual("http://127.0.0.1:7890", self.config_manager.get_oauth_proxy())
        self.assertTrue(self.config_manager.is_oauth_verify_ssl_enabled())

        with self.config_path.open("r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertTrue(persisted["oauth"]["enabled"])
        self.assertEqual("custom", persisted["oauth"]["proxy_mode"])
        self.assertEqual("http://127.0.0.1:7890", persisted["oauth"]["proxy"])
        self.assertTrue(persisted["oauth"]["verify_ssl"])

    def test_update_oauth_settings_persists_safe_desensitization_flag(self) -> None:
        self.assertFalse(self.service.get_system_settings()["oauth"]["safe_desensitization_enabled"])

        result = self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "direct",
                    "proxy": "",
                    "verify_ssl": False,
                    "safe_desensitization_enabled": True,
                }
            }
        )

        self.assertTrue(result["settings"]["oauth"]["safe_desensitization_enabled"])
        self.assertTrue(self.config_manager.is_oauth_safe_desensitization_enabled())
        with self.config_path.open("r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertTrue(persisted["oauth"]["safe_desensitization_enabled"])

        self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "direct",
                    "proxy": "",
                    "verify_ssl": False,
                    "safe_desensitization_enabled": False,
                }
            }
        )
        self.assertFalse(self.service.get_system_settings()["oauth"]["safe_desensitization_enabled"])

    def test_update_oauth_settings_supports_system_proxy_mode(self) -> None:
        result = self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "system",
                    "proxy": "http://127.0.0.1:7890",
                    "verify_ssl": False,
                }
            }
        )

        self.assertEqual("system", result["settings"]["oauth"]["proxy_mode"])
        self.assertEqual("", result["settings"]["oauth"]["proxy"])
        self.assertEqual("system", self.config_manager.get_oauth_proxy_mode())
        self.assertIsNone(self.config_manager.get_oauth_proxy())

        with self.config_path.open("r", encoding="utf-8") as handle:
            persisted = yaml.safe_load(handle)
        self.assertEqual("system", persisted["oauth"]["proxy_mode"])
        self.assertEqual("", persisted["oauth"]["proxy"])

    def test_update_oauth_settings_encodes_custom_proxy_credentials(self) -> None:
        result = self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "custom",
                    "proxy": "http://user:p@ss#word@127.0.0.1:7890",
                    "verify_ssl": False,
                }
            }
        )

        self.assertEqual(
            "http://user:p%40ss%23word@127.0.0.1:7890",
            result["settings"]["oauth"]["proxy"],
        )

    def test_update_oauth_settings_allows_custom_proxy_without_url(self) -> None:
        result = self.service.update_oauth_settings(
            {
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "custom",
                    "proxy": "",
                    "verify_ssl": False,
                }
            }
        )

        self.assertTrue(result["settings"]["oauth"]["enabled"])
        self.assertEqual("custom", result["settings"]["oauth"]["proxy_mode"])
        self.assertEqual("", result["settings"]["oauth"]["proxy"])

    def test_update_oauth_settings_rejects_invalid_enabled_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected a boolean value"):
            self.service.update_oauth_settings(
                {
                    "oauth": {
                        "enabled": "sometimes",
                        "proxy_mode": "direct",
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
                        "enabled": True,
                        "proxy_mode": "custom",
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
                "client_ip": {
                    "real_ip_enabled": True,
                    "real_ip_header": "CF-Connecting-IP",
                },
                "logging": {
                    "path": str(self.root_path / "new-logs"),
                    "level": "DEBUG",
                    "llm_request_debug_enabled": True,
                },
                "oauth": {
                    "enabled": True,
                    "proxy_mode": "custom",
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
        self.assertTrue(result["settings"]["client_ip"]["real_ip_enabled"])
        self.assertEqual("CF-Connecting-IP", result["settings"]["client_ip"]["real_ip_header"])

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
