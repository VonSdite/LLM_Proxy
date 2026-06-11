from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from flask import Flask, render_template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.auth_group_manager import AuthGroupManager
from src.config.provider_config import (
    AuthGroupSchema,
    ProviderConfigSchema,
    RuntimeProviderSpec,
)
from src.config.provider_manager import ProviderManager
from src.executors import HttpExecutor
from src.external.stream_probe import probe_stream_response
from src.proxy_core import resolve_stream_format
from src.presentation.app_factory import create_flask_app
from src.repositories import AuthGroupRepository
from src.services.model_discovery_service import ModelDiscoveryService
from src.utils.app_version import get_app_version
from src.utils.database import create_connection_factory
from src.utils.local_time import now_local_datetime
from src.utils.net import (
    build_requests_proxy_settings,
    build_requests_request_proxies,
    is_valid_ip,
    normalize_ip,
    resolve_client_ip,
)


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, msg: str, *args) -> None:
        rendered = msg % args if args else msg
        self.records.append((level, rendered))

    def info(self, msg: str, *args) -> None:
        self._log("info", msg, *args)

    def warning(self, msg: str, *args) -> None:
        self._log("warning", msg, *args)

    def error(self, msg: str, *args) -> None:
        self._log("error", msg, *args)

    def debug(self, msg: str, *args) -> None:
        self._log("debug", msg, *args)


class ProviderTransportTests(unittest.TestCase):
    def test_http_executor_clears_session_cookies_before_request(self) -> None:
        logger = FakeLogger()
        executor = HttpExecutor(logger=logger)
        provider = type(
            "Provider",
            (),
            {
                "api": "https://example.com/v1/chat/completions",
                "transport": "http",
                "name": "demo",
            },
        )()

        class FakeCookies:
            def __init__(self) -> None:
                self.clear_calls = 0

            def clear(self) -> None:
                self.clear_calls += 1

        class FakeResponse:
            status_code = 200
            headers = {"Content-Type": "application/json"}

        class FakeSession:
            def __init__(self) -> None:
                self.cookies = FakeCookies()
                self.post_calls: list[dict[str, object]] = []

            def post(self, *args, **kwargs):
                self.post_calls.append(dict(kwargs))
                return FakeResponse()

        fake_session = FakeSession()
        executor._http_local.session = fake_session  # type: ignore[attr-defined]

        executor.execute(
            provider,  # type: ignore[arg-type]
            headers={"authorization": "Bearer sk-demo"},
            body={"model": "demo/model"},
            requested_stream=False,
            timeout_seconds=30,
            verify_ssl=False,
            request_proxies=None,
        )

        self.assertEqual(1, fake_session.cookies.clear_calls)
        self.assertEqual(1, len(fake_session.post_calls))

    def test_http_executor_direct_mode_passes_disabled_proxy_mapping(self) -> None:
        logger = FakeLogger()
        executor = HttpExecutor(logger=logger)
        provider = type(
            "Provider",
            (),
            {
                "api": "https://example.com/v1/chat/completions",
                "transport": "http",
                "name": "demo",
                "proxy_mode": "direct",
                "proxy": None,
            },
        )()

        class FakeCookies:
            def clear(self) -> None:
                pass

        class FakeResponse:
            status_code = 200
            headers = {"Content-Type": "application/json"}

        class FakeSession:
            def __init__(self) -> None:
                self.cookies = FakeCookies()
                self.trust_env = True
                self.post_calls: list[dict[str, object]] = []

            def post(self, *args, **kwargs):
                del args
                self.post_calls.append(dict(kwargs))
                return FakeResponse()

        fake_session = FakeSession()
        executor._http_local.session = fake_session  # type: ignore[attr-defined]

        executor.execute(
            provider,  # type: ignore[arg-type]
            headers={"authorization": "Bearer sk-demo"},
            body={"model": "demo/model"},
            requested_stream=False,
            timeout_seconds=30,
            verify_ssl=False,
            request_proxies=None,
        )

        self.assertFalse(fake_session.trust_env)
        self.assertEqual(
            {"http": None, "https": None, "all": None},
            fake_session.post_calls[0]["proxies"],
        )

    def test_http_executor_direct_mode_ignores_process_proxy_environment(self) -> None:
        logger = FakeLogger()
        target_calls: list[str] = []
        proxy_calls: list[str] = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                target_calls.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args) -> None:
                del args

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                proxy_calls.append(self.path)
                self.send_response(599)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"proxy used")

            def log_message(self, *args) -> None:
                del args

        target_server = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        proxy_server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
        target_thread = Thread(target=target_server.serve_forever, daemon=True)
        proxy_thread = Thread(target=proxy_server.serve_forever, daemon=True)
        old_env = {key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
        old_no_proxy = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")}
        try:
            target_thread.start()
            proxy_thread.start()
            proxy_url = f"http://127.0.0.1:{proxy_server.server_address[1]}"
            for key in old_env:
                os.environ[key] = proxy_url
            for key in old_no_proxy:
                os.environ[key] = ""

            provider = type(
                "Provider",
                (),
                {
                    "api": f"http://127.0.0.1:{target_server.server_address[1]}/v1/chat/completions",
                    "transport": "http",
                    "name": "demo",
                    "proxy_mode": "direct",
                    "proxy": None,
                },
            )()
            executor = HttpExecutor(logger=logger)
            opened = executor.execute(
                provider,  # type: ignore[arg-type]
                headers={"authorization": "Bearer sk-demo"},
                body={"model": "demo/model"},
                requested_stream=False,
                timeout_seconds=30,
                verify_ssl=False,
                request_proxies=None,
            )

            self.assertEqual(200, opened.status_code)
            self.assertEqual(["/v1/chat/completions"], target_calls)
            self.assertEqual([], proxy_calls)
        finally:
            target_server.shutdown()
            proxy_server.shutdown()
            target_server.server_close()
            proxy_server.server_close()
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for key, value in old_no_proxy.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_http_executor_auto_confirms_proxy_warning_and_retries_once(self) -> None:
        logger = FakeLogger()
        executor = HttpExecutor(logger=logger)
        provider = type(
            "Provider",
            (),
            {
                "api": "https://example.com/v1/chat/completions",
                "transport": "http",
                "name": "demo",
            },
        )()
        confirmation_url = (
            "http://114.114.114.114:9421/proxycontrolwarn/httpwarning_3355.html?ori_url=aHR0cHM6Ly9leGFtcGxlLmNvbS8="
        )

        class FakeCookies:
            def clear(self) -> None:
                pass

        class FakeResponse:
            def __init__(
                self,
                status_code: int,
                *,
                headers: dict[str, str] | None = None,
                text: str = "",
            ) -> None:
                self.status_code = status_code
                self.headers = headers or {"Content-Type": "application/json"}
                self.text = text
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeSession:
            def __init__(self) -> None:
                self.cookies = FakeCookies()
                self.post_calls: list[dict[str, object]] = []
                self.get_calls: list[tuple[str, dict[str, object]]] = []

            def post(self, *args, **kwargs):
                del args
                self.post_calls.append(dict(kwargs))
                if len(self.post_calls) == 1:
                    return FakeResponse(
                        302,
                        headers={"Location": confirmation_url},
                    )
                return FakeResponse(200)

            def get(self, url, **kwargs):
                self.get_calls.append((url, dict(kwargs)))
                if len(self.get_calls) == 1:
                    return FakeResponse(
                        200,
                        text="""
                            <input id="sessionid" value="session-123" />
                            <input id="pid" value="3355" />
                            <input id="uid" value="0" />
                        """,
                    )
                return FakeResponse(200)

        fake_session = FakeSession()
        executor._http_local.session = fake_session  # type: ignore[attr-defined]

        opened = executor.execute(
            provider,  # type: ignore[arg-type]
            headers={"authorization": "Bearer sk-demo"},
            body={"model": "demo/model"},
            requested_stream=False,
            timeout_seconds=30,
            verify_ssl=False,
            request_proxies=None,
        )

        self.assertEqual(200, opened.status_code)
        self.assertEqual(2, len(fake_session.post_calls))
        self.assertEqual(2, len(fake_session.get_calls))
        self.assertFalse(fake_session.post_calls[0]["allow_redirects"])
        self.assertEqual(confirmation_url, fake_session.get_calls[0][0])
        self.assertTrue(fake_session.get_calls[1][0].startswith("http://114.114.114.114:9421/proxycontrolwarn/check?"))

    def test_provider_enabled_defaults_to_true(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "codex",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertTrue(schema.enabled)
        runtime = RuntimeProviderSpec.from_schema(schema)
        self.assertTrue(runtime.enabled)

    def test_provider_schema_accepts_safe_provider_name_payload(self) -> None:
        schema = ProviderConfigSchema.from_payload(
            {
                "name": " openai_1 ",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("openai_1", schema.name)

    def test_provider_schema_rejects_invalid_provider_name_payloads(self) -> None:
        cases = [
            ("1provider", "Provider name must start with a letter"),
            ("bad-provider", "Provider name must start with a letter"),
            ("bad.provider", "Provider name must start with a letter"),
            ("bad provider", "Provider name must start with a letter"),
            ("中文", "Provider name must start with a letter"),
            ("a" * 65, "Provider name must be 64 characters or less"),
        ]

        for name, error_pattern in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, error_pattern):
                    ProviderConfigSchema.from_payload(
                        {
                            "name": name,
                            "api": "https://example.com/v1/chat/completions",
                            "api_key": "demo-key",
                            "model_list": ["gpt-4.1"],
                        }
                    )

    def test_provider_schema_keeps_legacy_provider_names_from_mapping(self) -> None:
        cases = [
            "1provider",
            "bad/provider",
            "bad_provider",
            "bad-provider",
            "bad.provider",
            "bad provider",
            "中文",
            "a" * 65,
        ]

        for name in cases:
            with self.subTest(name=name):
                schema = ProviderConfigSchema.from_mapping(
                    {
                        "name": name,
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "demo-key",
                        "model_list": ["gpt-4.1"],
                    }
                )

                self.assertEqual(name, schema.name)

    def test_provider_timeout_defaults_to_1200_seconds(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "codex",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertIsNone(schema.timeout_seconds)
        runtime = RuntimeProviderSpec.from_schema(schema)
        self.assertEqual(1200, runtime.timeout_seconds)

    def test_provider_schema_rejects_transport_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported provider field\\(s\\): transport"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "bad-provider",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "transport": "http",
                    "model_list": ["demo"],
                }
            )

    def test_provider_rejects_websocket_api_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provider api must use http:// or https://"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "bad-provider",
                    "api": "wss://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "model_list": ["demo"],
                }
            )

    def test_provider_defaults_source_and_internal_target_formats(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("openai_chat", schema.source_format)
        self.assertEqual("openai_chat", schema.target_format)
        self.assertEqual(("openai_chat", "openai_responses", "claude_chat"), schema.target_formats)
        runtime = RuntimeProviderSpec.from_schema(schema)
        self.assertEqual("openai_chat", runtime.source_format)
        self.assertEqual("openai_chat", runtime.primary_target_format)
        self.assertEqual(("openai_chat", "openai_responses", "claude_chat"), runtime.target_formats)

    def test_provider_proxy_mode_defaults_to_direct_and_supports_system(self) -> None:
        direct_schema = ProviderConfigSchema.from_mapping(
            {
                "name": "direct-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["demo"],
            }
        )
        system_schema = ProviderConfigSchema.from_mapping(
            {
                "name": "system-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "proxy_mode": "system",
                "proxy": "http://127.0.0.1:7890",
                "model_list": ["demo"],
            }
        )

        self.assertEqual("direct", direct_schema.proxy_mode)
        self.assertIsNone(direct_schema.proxy)
        self.assertNotIn("proxy_mode", direct_schema.to_mapping())
        self.assertEqual("system", system_schema.proxy_mode)
        self.assertIsNone(system_schema.proxy)
        self.assertEqual("system", system_schema.to_mapping()["proxy_mode"])
        self.assertNotIn("proxy", system_schema.to_mapping())

    def test_provider_custom_proxy_encodes_credentials(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "custom-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "proxy_mode": "custom",
                "proxy": "http://user:p@ss#word@127.0.0.1:7890",
                "model_list": ["demo"],
            }
        )

        self.assertEqual("custom", schema.proxy_mode)
        self.assertEqual("http://user:p%40ss%23word@127.0.0.1:7890", schema.proxy)

    def test_provider_custom_proxy_encodes_url_delimiters_in_credentials(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "custom-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "proxy_mode": "custom",
                "proxy": "http://user:123/pa?ss#word@127.0.0.1:7890",
                "model_list": ["demo"],
            }
        )

        self.assertEqual("http://user:123%2Fpa%3Fss%23word@127.0.0.1:7890", schema.proxy)

    def test_provider_custom_proxy_allows_empty_url(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "custom-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "proxy_mode": "custom",
                "proxy": "",
                "model_list": ["demo"],
            }
        )

        self.assertEqual("custom", schema.proxy_mode)
        self.assertIsNone(schema.proxy)
        self.assertEqual("custom", schema.to_mapping()["proxy_mode"])
        self.assertNotIn("proxy", schema.to_mapping())

    def test_provider_schema_rejects_target_formats_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported provider field\\(s\\): target_formats"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "target_formats": ["openai_chat", "claude_chat"],
                    "model_list": ["gpt-4.1"],
                }
            )

    def test_provider_schema_rejects_removed_codex_protocol(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "Provider source_format must be one of: claude_chat, openai_chat, openai_responses"
        ):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/responses",
                    "api_key": "demo-key",
                    "source_format": "codex",
                    "model_list": ["gpt-5-codex"],
                }
            )

    def test_provider_schema_rejects_legacy_target_format_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported provider field\\(s\\): target_format"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "target_format": "claude_chat",
                    "model_list": ["gpt-4.1"],
                }
            )

    def test_provider_schema_rejects_removed_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported provider field\\(s\\): format"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "format": "openai_chat",
                    "model_list": ["gpt-4.1"],
                }
            )

        with self.assertRaisesRegex(ValueError, "Unsupported provider field\\(s\\): stream_format"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "stream_format": "sse_json",
                    "model_list": ["gpt-4.1"],
                }
            )

    def test_internal_stream_detection_uses_transport_and_content_type(self) -> None:
        self.assertEqual(
            "sse_json",
            resolve_stream_format(None, "text/event-stream; charset=utf-8", "http"),
        )
        self.assertEqual("ndjson", resolve_stream_format(None, "application/x-ndjson", "http"))
        self.assertEqual("nonstream", resolve_stream_format(None, "application/json", "http"))

    def test_stream_probe_detects_sse_when_content_type_is_wrong(self) -> None:
        class FakeResponse:
            def __init__(self) -> None:
                self.status_code = 200
                self.headers = {"Content-Type": "application/json"}
                self.closed = False
                self._chunks = iter(
                    [
                        b'data: {"ok":true}\n\n',
                        b"data: [DONE]\n\n",
                    ]
                )

            def iter_content(self, chunk_size=None):
                del chunk_size
                yield from self._chunks

            def close(self) -> None:
                self.closed = True

        response, is_stream = probe_stream_response(FakeResponse())

        self.assertTrue(is_stream)
        self.assertEqual(
            [b'data: {"ok":true}\n\n', b"data: [DONE]\n\n"],
            list(response.iter_content()),
        )


class ProviderManagerEnabledTests(unittest.TestCase):
    def test_disabled_provider_is_not_registered_at_runtime(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        manager = ProviderManager(ctx)

        manager.load_providers(
            (
                ProviderConfigSchema.from_mapping(
                    {
                        "name": "disabled-provider",
                        "enabled": False,
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "disabled-key",
                        "model_list": ["gpt-4.1"],
                    }
                ),
                ProviderConfigSchema.from_mapping(
                    {
                        "name": "enabled-provider",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "enabled-key",
                        "model_list": ["gpt-4.1-mini"],
                    }
                ),
            )
        )

        self.assertEqual(("enabled-provider/gpt-4.1-mini",), manager.list_model_names())
        self.assertIsNone(manager.get_provider_view("disabled-provider"))
        provider_view = manager.get_provider_view("enabled-provider")
        self.assertIsNotNone(provider_view)
        assert provider_view is not None
        self.assertTrue(provider_view.legacy_api_key)
        self.assertIsNone(provider_view.auth_group)
        runtime_provider = manager.get_provider_for_model("enabled-provider/gpt-4.1-mini")
        self.assertIsNotNone(runtime_provider)
        assert runtime_provider is not None
        self.assertIsNone(runtime_provider.auth_group)
        self.assertTrue(
            any(
                "disabled-provider" in message and "skipped runtime registration" in message
                for _, message in logger.records
            )
        )


class AuthGroupLegacyCleanupTests(unittest.TestCase):
    def test_load_auth_groups_purges_hidden_legacy_runtime_rows(self) -> None:
        root_path = Path(__file__).resolve().parents[1]
        runtime_dir = root_path / "data" / "_test_runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        db_path = runtime_dir / f"legacy-cleanup-{uuid4().hex}.db"

        try:
            logger = FakeLogger()
            ctx = AppContext(
                logger=logger,
                config_manager=None,  # type: ignore[arg-type]
                root_path=root_path,
                flask_app=Flask(__name__),
            )
            repository = AuthGroupRepository(create_connection_factory(db_path))
            repository.save_entry_runtime_state(
                "__legacy_provider__/volc_code",
                "legacy",
                disabled=True,
                disabled_reason="http_401",
                cooldown_until=None,
                last_status_code=401,
                last_error_type=None,
                last_error_message="bad key",
            )
            repository.increment_request_usage(
                "__legacy_provider__/volc_code",
                "legacy",
                now_local_datetime(),
            )

            manager = AuthGroupManager(ctx, repository)
            manager.load_auth_groups(
                (
                    AuthGroupSchema.from_mapping(
                        {
                            "name": "pool-a",
                            "entries": [
                                {
                                    "id": "key-a",
                                    "headers": {"Authorization": "Bearer sk-a"},
                                }
                            ],
                        }
                    ),
                )
            )

            self.assertEqual(
                {},
                repository.list_group_runtime_states("__legacy_provider__/volc_code"),
            )
            usage = repository.list_current_usage(
                "__legacy_provider__/volc_code",
                ("legacy",),
                now_local_datetime(),
            )
            self.assertEqual(0, usage["legacy"]["minute_request_count"])
            self.assertEqual(0, usage["legacy"]["day_request_count"])
            self.assertTrue(
                any("Purged legacy provider runtime state rows" in message for _, message in logger.records)
            )
        finally:
            if db_path.exists():
                db_path.unlink()


class NetUtilsTests(unittest.TestCase):
    def test_normalize_ip_strips_ipv6_mapped_prefix(self) -> None:
        self.assertEqual("127.0.0.1", normalize_ip("::ffff:127.0.0.1"))

    def test_normalize_ip_preserves_unparseable_input(self) -> None:
        self.assertEqual("not-an-ip", normalize_ip(" not-an-ip "))

    def test_is_valid_ip_accepts_ipv6_mapped_ipv4(self) -> None:
        self.assertTrue(is_valid_ip("::ffff:127.0.0.1"))

    def test_resolve_client_ip_uses_remote_addr_when_real_ip_disabled(self) -> None:
        self.assertEqual(
            "127.0.0.1",
            resolve_client_ip(
                {"X-Forwarded-For": "203.0.113.10"},
                "127.0.0.1",
                real_ip_enabled=False,
                real_ip_header="X-Forwarded-For",
            ),
        )

    def test_resolve_client_ip_uses_first_valid_real_ip_header_value(self) -> None:
        self.assertEqual(
            "203.0.113.10",
            resolve_client_ip(
                {"x-forwarded-for": "203.0.113.10, 10.0.0.1"},
                "127.0.0.1",
                real_ip_enabled=True,
                real_ip_header="X-Forwarded-For",
            ),
        )

    def test_resolve_client_ip_falls_back_for_invalid_real_ip_header(self) -> None:
        self.assertEqual(
            "127.0.0.1",
            resolve_client_ip(
                {"X-Forwarded-For": "not-an-ip"},
                "127.0.0.1",
                real_ip_enabled=True,
                real_ip_header="X-Forwarded-For",
            ),
        )

    def test_custom_proxy_mode_without_url_behaves_as_direct(self) -> None:
        settings = build_requests_proxy_settings("custom", "")

        self.assertEqual("custom", settings.mode)
        self.assertIsNone(settings.proxy_url)
        self.assertIsNone(settings.proxies)
        self.assertFalse(settings.trust_env)
        self.assertEqual(
            {"http": None, "https": None, "all": None},
            build_requests_request_proxies(settings),
        )


class ModelDiscoveryCandidateTests(unittest.TestCase):
    def test_model_discovery_rejects_websocket_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provider api must use http:// or https://"):
            ModelDiscoveryService._build_model_endpoint_candidates("wss://example.com/v1/chat/completions")

    def test_model_discovery_uses_only_base_candidates(self) -> None:
        candidates = ModelDiscoveryService._build_model_endpoint_candidates(
            "https://example.com/proxy/v1/chat/completions"
        )

        self.assertEqual(
            [
                "https://example.com/proxy/v1/models",
                "https://example.com/proxy/models",
            ],
            candidates,
        )

    def test_model_discovery_trims_responses_endpoint_to_base_path(self) -> None:
        candidates = ModelDiscoveryService._build_model_endpoint_candidates("https://example.com/gateway/v1/responses")

        self.assertEqual(
            [
                "https://example.com/gateway/v1/models",
                "https://example.com/gateway/models",
            ],
            candidates,
        )

    def test_model_discovery_trims_claude_messages_endpoint_to_base_path(self) -> None:
        candidates = ModelDiscoveryService._build_model_endpoint_candidates("https://example.com/anthropic/v1/messages")

        self.assertEqual(
            [
                "https://example.com/anthropic/v1/models",
                "https://example.com/anthropic/models",
            ],
            candidates,
        )

    def test_fetch_models_preview_merges_request_headers(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        service = ModelDiscoveryService(ctx)
        captured: dict[str, object] = {}

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json() -> dict[str, object]:
                return {"data": [{"id": "demo-model"}]}

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None, **kwargs):
                captured["url"] = url
                captured["headers"] = dict(headers or {})
                captured["proxies"] = proxies
                captured["timeout"] = timeout
                captured["verify"] = verify
                captured["allow_redirects"] = kwargs.get("allow_redirects")
                return FakeResponse()

        with patch("src.services.model_discovery_service.requests.Session", return_value=FakeSession()):
            result = service.fetch_models_preview(
                api="https://example.com/v1/chat/completions",
                request_headers={
                    "Authorization": "Bearer sk-a",
                    "x-org": "team-a",
                },
                timeout_seconds=12,
                verify_ssl=True,
            )

        self.assertEqual(["demo-model"], result["fetched_models"])
        self.assertEqual("https://example.com/v1/models", captured["url"])
        self.assertEqual(
            {
                "accept": "application/json",
                "Authorization": "Bearer sk-a",
                "x-org": "team-a",
            },
            captured["headers"],
        )
        self.assertEqual(12, captured["timeout"])
        self.assertTrue(captured["verify"])
        self.assertFalse(captured["allow_redirects"])

    def test_fetch_models_preview_defaults_to_ten_second_timeout(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        service = ModelDiscoveryService(ctx)
        captured: dict[str, object] = {}

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json() -> dict[str, object]:
                return {"data": [{"id": "demo-model"}]}

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None, **kwargs):
                del url, headers, proxies, verify, kwargs
                captured["timeout"] = timeout
                return FakeResponse()

        with patch("src.services.model_discovery_service.requests.Session", return_value=FakeSession()):
            service.fetch_models_preview(api="https://example.com/v1/chat/completions")

        self.assertEqual(10, captured["timeout"])

    def test_fetch_models_preview_closes_candidate_responses(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        service = ModelDiscoveryService(ctx)

        class FakeResponse:
            def __init__(self, status_code: int, payload: dict[str, object]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.closed = False

            def json(self) -> dict[str, object]:
                return self._payload

            def close(self) -> None:
                self.closed = True

        all_responses = [
            FakeResponse(500, {}),
            FakeResponse(200, {"data": [{"id": "demo-model"}]}),
        ]
        pending_responses = list(all_responses)

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None, **kwargs):
                del url, headers, proxies, timeout, verify, kwargs
                return pending_responses.pop(0)

        with patch("src.services.model_discovery_service.requests.Session", return_value=FakeSession()):
            result = service.fetch_models_preview(api="https://example.com/v1/chat/completions")

        self.assertEqual(["demo-model"], result["fetched_models"])
        self.assertTrue(all(response.closed for response in all_responses))

    def test_fetch_models_preview_reports_all_candidate_failures(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        service = ModelDiscoveryService(ctx)

        class FakeResponse:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        pending_responses = [FakeResponse(401), FakeResponse(404)]

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None, **kwargs):
                del url, headers, proxies, timeout, verify, kwargs
                return pending_responses.pop(0)

        with patch("src.services.model_discovery_service.requests.Session", return_value=FakeSession()):
            with self.assertRaises(ValueError) as raised:
                service.fetch_models_preview(api="https://example.com/v1/chat/completions")

        self.assertEqual(
            "https://example.com/v1/models returned 401; https://example.com/models returned 404",
            str(raised.exception),
        )

    def test_fetch_models_preview_replaces_case_insensitive_header_duplicates(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        service = ModelDiscoveryService(ctx)
        captured: dict[str, object] = {}

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json() -> dict[str, object]:
                return {"data": [{"id": "demo-model"}]}

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None, **kwargs):
                del url, proxies, timeout, verify, kwargs
                captured["headers"] = dict(headers or {})
                return FakeResponse()

        with patch("src.services.model_discovery_service.requests.Session", return_value=FakeSession()):
            service.fetch_models_preview(
                api="https://example.com/v1/chat/completions",
                api_key="sk-legacy",
                request_headers={"Authorization": "Bearer sk-auth-group"},
            )

        self.assertEqual(
            {
                "accept": "application/json",
                "Authorization": "Bearer sk-auth-group",
            },
            captured["headers"],
        )


class ProviderTemplateTransportTests(unittest.TestCase):
    def test_provider_template_contains_clean_provider_fields_and_help(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")
        users_template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "users.html"
        users_html = users_template_path.read_text(encoding="utf-8")
        css_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "static" / "css" / "providers.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertRegex(
            html,
            r"/static/css/providers\.css\?v=\d{8}-\d+",
        )
        self.assertNotIn('id="providerTransport"', html)
        self.assertIn('id="providerSourceFormat"', html)
        self.assertNotIn('id="providerTargetFormat"', html)
        self.assertNotIn('data-multi-select-badge="多选"', html)
        self.assertNotIn('data-multi-select-hint="可多选，点击已选项可取消；互斥项会自动替换"', html)
        self.assertNotIn('class="field-mode-badge">多选</span>', html)
        self.assertNotIn(
            'class="field-inline-note">可多选；点击已选项可取消，互斥项会自动替换。</div>',
            html,
        )
        self.assertIn('id="providerAuthMode"', html)
        self.assertIn('id="providerAuthGroup"', html)
        self.assertIn('id="providerName"', html)
        self.assertIn('maxlength="64"', html)
        self.assertIn('pattern="[A-Za-z][A-Za-z0-9_]*"', html)
        self.assertIn("function getProviderNameValidationError(name)", html)
        self.assertIn("Provider 名称必须英文开头，且只能包含英文、数字和下划线", html)
        self.assertIn('id="providerProxyMode"', html)
        self.assertIn('id="providerProxyRow"', html)
        self.assertIn('id="providerProxyCustomField" hidden', html)
        self.assertIn('class="provider-proxy-custom-field" id="providerProxyCustomField" hidden', html)
        self.assertIn('id="providerProxy"', html)
        self.assertIn('data-provider-help-topic="proxy"', html)
        self.assertIn("上游出站代理模式", html)
        self.assertIn('aria-label="自定义上游出站代理地址"', html)
        self.assertIn(
            'type="text"\n                                    class="form-control sensitive-input-control"\n                                    id="providerApiKey"',
            html,
        )
        self.assertNotIn('data-sensitive-masked-type="password"', html)
        self.assertIn('data-sensitive-toggle-for="providerProxy"', html)
        self.assertIn("function syncProviderProxyFields()", html)
        self.assertIn("setupCustomSelect('providerProxyMode');", html)
        self.assertIn("proxy_mode: proxyMode", html)
        self.assertIn("不是下游客户端访问本服务的入口代理", html)
        self.assertIn("HTTP_PROXY", html)
        self.assertIn("它不保证读取操作系统桌面代理设置", html)
        self.assertIn("留空时等同直连", html)
        self.assertIn("用户名里包含冒号需要手动写成 <code>%3A</code>", html)
        self.assertNotIn("请输入自定义上游出站代理地址", html)
        self.assertIn("function handleSensitiveInputCopy(event)", html)
        self.assertIn("input?.addEventListener('copy', handleSensitiveInputCopy);", html)
        self.assertNotIn("providerTransportAutoSyncEnabled", html)
        self.assertNotIn("syncProviderTransportWithApi", html)
        self.assertIn(
            "document.getElementById('providerApi').addEventListener('input', handleProviderApiInput);",
            html,
        )
        self.assertNotIn("handleProviderTransportChange", html)
        self.assertIn('id="authGroupsContainer"', html)
        self.assertIn('id="authGroupModal"', html)
        self.assertIn('id="authEntryImportModal"', html)
        self.assertIn('id="authGroupRuntimeModal"', html)
        self.assertIn('id="authGroupDeletePopover"', html)
        self.assertIn(
            'id="providerModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"',
            html,
        )
        self.assertIn('class="provider-form-grid"', html)
        self.assertIn('class="mb-3 provider-model-list-section"', html)
        self.assertNotIn('id="providerModalTabBtn_basic"', html)
        self.assertNotIn('id="providerModalTabBtn_models"', html)
        self.assertNotIn('id="providerModalTabPanel_basic"', html)
        self.assertNotIn('id="providerModalTabPanel_models"', html)
        self.assertNotIn("function switchProviderModalTab(tabName)", html)
        self.assertNotIn("function syncProviderModalTabUi()", html)
        self.assertIn(
            'id="authGroupModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"',
            html,
        )
        self.assertIn(
            'id="authEntryImportModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"',
            html,
        )
        self.assertNotIn('id="providerFormat"', html)
        self.assertNotIn('id="providerStreamFormat"', html)

        for value in ("openai_chat", "openai_responses", "claude_chat"):
            self.assertIn(f'value="{value}"', html)
        self.assertNotIn('value="codex"', html)
        for removed_value in ("gemini_chat", "gemini_cli", "antigravity"):
            self.assertNotIn(f'value="{removed_value}"', html)

        self.assertNotIn('data-provider-help-topic="transport"', html)
        self.assertIn('data-provider-help-topic="source_format"', html)
        self.assertNotIn('data-provider-help-topic="target_format"', html)
        self.assertIn('data-provider-help-topic="auth_group_field"', html)
        self.assertIn('data-provider-help-topic="auth_groups_overview"', html)
        self.assertIn('data-provider-help-topic="auth_group_strategy"', html)
        self.assertIn('data-provider-help-topic="auth_entries_editor"', html)
        self.assertIn('data-provider-help-topic="fetch_models"', html)
        self.assertIn("所选 Auth Group 的第一个 entry", html)
        self.assertIn('id="providerHelpPopover"', html)
        self.assertIn("function toggleProviderHelp(", html)
        self.assertIn("function showProviderHelp(", html)
        self.assertIn("function syncProviderHelpPopover()", html)
        self.assertIn("function initProviderHelpInteractions()", html)
        self.assertIn("scheduleProviderHelpPopoverHide()", html)
        self.assertIn("function renderAuthGroups()", html)
        self.assertIn("function saveAuthGroup()", html)
        self.assertIn("function saveAuthEntriesFromYaml()", html)
        self.assertIn("function disableAuthGroupEntry(", html)
        self.assertIn("function enableAuthGroupEntry(", html)
        self.assertIn("function resetAuthGroupEntryRuntime(", html)
        self.assertIn("function openAuthEntryErrorModal(", html)
        self.assertIn("function updateProviderAuthModeFields()", html)
        self.assertIn("function getDefaultAuthEntryHeaders()", html)
        self.assertIn("/api/auth-groups", html)
        self.assertIn("/api/auth-groups/import-entries", html)
        self.assertIn("'disable'", html)
        self.assertIn("'enable'", html)
        self.assertIn("'reset'", html)
        self.assertIn("setupCustomSelect('providerAuthMode');", html)
        self.assertIn("setupCustomSelect('providerAuthGroup');", html)
        self.assertIn("renderCustomSelectOptions('providerAuthGroup');", html)
        self.assertIn(
            "document.getElementById('providerAuthGroup').addEventListener('change', function() {",
            html,
        )
        self.assertIn("setupCustomSelect('authGroupStrategy');", html)
        self.assertNotIn("setupCustomSelect('providerTransport');", html)
        self.assertIn("setupCustomSelect('providerSourceFormat');", html)
        self.assertNotIn("setupCustomSelect('providerTargetFormat');", html)
        self.assertIn("setupCustomSelect('providerVerifySsl');", html)
        self.assertIn("function buildMultiSelectTriggerMarkup(", html)
        self.assertNotIn("custom-select-trigger-badge", html)
        self.assertNotIn("已选 ${selectedLabels.length} 项", html)
        self.assertIn("custom-select-option custom-select-option-multi", html)
        self.assertIn("custom-select-menu-hint", html)
        self.assertNotIn(
            "const defaultNewProviderTargetFormats = ['openai_chat', 'openai_responses', 'claude_chat'];",
            html,
        )
        self.assertNotIn("const providerTargetFormatConflictGroups = [];", html)
        self.assertNotIn("function setProviderTargetFormatValues(", html)
        self.assertNotIn("target_formats:", html)
        self.assertNotIn("switchProviderModalTab('basic');", html)
        self.assertNotIn('id="selectedProviderCount"', html)
        self.assertNotIn('id="enableSelectedProvidersBtn"', html)
        self.assertNotIn('id="disableSelectedProvidersBtn"', html)
        self.assertNotIn('id="deleteSelectedProvidersBtn"', html)
        self.assertNotIn('id="clearSelectedProvidersBtn"', html)
        self.assertIn("let providerOrderActionInFlight = false;", html)
        self.assertIn("function getProviderGroupNames(groupKey)", html)
        self.assertIn("function getSelectedProviderNamesForGroup(groupKey)", html)
        self.assertIn("function toggleProviderGroupSelection(groupKey, checked)", html)
        self.assertIn("function hasProviderMutationInFlight()", html)
        self.assertIn("function buildProviderTable(groupKey, title, providerList, emptyText)", html)
        self.assertIn("function saveProviderOrder(names)", html)
        self.assertIn("function updateProviderSelectionUi()", html)
        self.assertIn("function toggleProviderSelection(name, checked)", html)
        self.assertIn("function toggleAllProvidersSelection(checked)", html)
        self.assertIn("function clearSelectedProviders()", html)
        self.assertIn("function setProviderEnabled(name, enabled)", html)
        self.assertIn("function runProviderBatchAction(action, groupKey = '')", html)
        self.assertIn("/api/providers/batch", html)
        self.assertIn("/api/providers/order", html)
        self.assertIn(
            "/api/providers/${encodeURIComponent(normalizedName)}/${enabled ? 'enable' : 'disable'}",
            html,
        )
        self.assertNotIn('id="providerSelectAllCheckbox"', html)
        self.assertIn('data-provider-group-select-checkbox="${normalizedGroupKey}"', html)
        self.assertIn('class="drag-handle-placeholder" aria-hidden="true"', html)
        self.assertIn('id="${batchActionMeta.buttonId}"', html)
        self.assertIn('class="btn btn-toolbar-secondary provider-group-batch-btn"', html)
        self.assertIn("onclick=\"runProviderBatchAction('${batchActionMeta.action}', '${normalizedGroupKey}')\"", html)
        self.assertIn("buttonId: 'disableEnabledProvidersBtn'", html)
        self.assertIn("buttonId: 'enableDisabledProvidersBtn'", html)
        self.assertIn("buttonLabel: '禁用'", html)
        self.assertIn("buttonLabel: '启用'", html)
        self.assertIn("已启用", html)
        self.assertIn("已禁用", html)
        self.assertIn("if (!normalizedName || hasProviderMutationInFlight())", html)
        self.assertIn("groupLabel = groupKey === 'enabled'", html)
        self.assertIn('data-provider-row-checkbox="${encodedProviderName}"', html)
        self.assertIn('data-auth-group-delete-trigger="${encodedGroupName}"', html)
        self.assertIn('class="drag-handle-button provider-drag-handle"', html)
        self.assertNotIn("provider-chip ${isEnabled ? 'provider-chip-hook' : 'provider-chip-muted'}", html)
        self.assertIn("class=\"btn-action ${isEnabled ? 'btn-delete' : 'btn-edit'}\"", html)
        self.assertNotIn(
            "window.confirm(`确认批量删除已选中的 ${selectedNames.length} 个 Provider？`)",
            html,
        )
        self.assertNotIn("window.confirm(", html)
        self.assertNotIn("setupCustomSelect('providerFormat');", html)
        self.assertNotIn("setupCustomSelect('providerStreamFormat');", html)
        self.assertNotIn('id="providerFormatMatrix"', html)
        self.assertNotIn("49 pairs", html)
        self.assertNotIn('data-bs-toggle="tooltip"', html)

        self.assertIn('id="fetchModelSelectAllCheckbox"', html)
        self.assertIn("toggleFilteredFetchedModels(this.checked)", html)
        self.assertIn("if (payload.auth_group) params.set('auth_group', payload.auth_group);", html)
        self.assertIn('id="modelTestSelectAllCheckbox"', html)
        self.assertIn('id="runSelectedModelTestsBtn"', html)
        self.assertIn('id="deleteSelectedModelTestsBtn"', html)
        self.assertIn('id="modelTestAuthEntry"', html)
        self.assertIn('id="modelTestSummary"', html)
        self.assertIn('id="modelTestTableContainer"', html)
        self.assertIn("setupCustomSelect('modelTestAuthEntry');", html)
        self.assertIn("/api/providers/test-models", html)
        self.assertIn("function setModelTestRowsFromModels(", html)
        self.assertIn("function runModelTestsForRows(", html)
        self.assertNotIn('id="providerModelList"', html)
        self.assertIn('class="provider-model-cell"', html)
        self.assertIn('class="provider-meta-line"', html)
        self.assertIn('class="providers-table-shell auth-groups-table-shell"', html)
        self.assertIn("<th>Entry 数</th>", html)
        self.assertIn('placeholder="例如 openai-shared，供 Provider 绑定引用"', html)
        self.assertIn('placeholder="例如 60，表示该组默认遇到 429 冷却 60 秒"', html)
        self.assertIn("YAML 编辑", html)
        self.assertIn('id="authEntryImportYaml"', html)
        self.assertIn('Authorization: "Bearer "', html)
        self.assertIn("这里编辑的是当前 Auth Entries 的完整 YAML", html)
        self.assertIn("插入 Entry 模板", html)
        self.assertIn("必填：Entry 唯一 ID", html)
        self.assertIn("可选：每分钟请求数上限", html)
        self.assertIn("新增 Entry", html)
        self.assertNotIn("新 Entry", html)
        self.assertIn("<span>Header 数</span>", html)
        self.assertIn("<span>限制概览</span>", html)
        self.assertIn('class="auth-entry-table-toggle-column"', html)
        self.assertIn("function toggleAuthEntryCard(", html)
        self.assertIn('id="authEntryDeletePopover"', html)
        self.assertIn("function removeAuthEntryCard(source) {", html)
        self.assertIn("function toggleDeleteAuthEntryConfirm(entryKey, event) {", html)
        self.assertIn("function confirmDeleteAuthEntry(event) {", html)
        self.assertIn("function closeDeleteAuthEntryConfirm(shouldSync = true) {", html)
        self.assertIn("function syncDeleteAuthEntryPopover() {", html)
        self.assertIn('data-auth-entry-delete-trigger="${entryKey}"', html)
        self.assertIn("onclick=\"toggleDeleteAuthEntryConfirm('${entryKey}', event)\"", html)
        self.assertIn('class="btn-action btn-edit auth-entry-toggle-btn"', html)
        self.assertIn("function handleAuthEntrySummaryKeydown(", html)
        self.assertIn('onclick="toggleAuthEntryCard(this)"', html)
        self.assertNotIn("expandAuthEntryCard(nextCard);", html)
        self.assertNotIn('id="authEntryImportTemplate"', html)
        self.assertNotIn("复制模板", html)
        self.assertNotIn("填入模板", html)
        self.assertIn("function buildAuthEntriesYamlText(", html)
        self.assertIn("function insertAuthEntryYamlTemplate(", html)
        self.assertIn("function getSingleAuthEntryYamlTemplate(", html)
        self.assertIn("function saveAuthEntriesFromYaml(", html)
        self.assertIn("function toggleDeleteAuthGroupConfirm(", html)
        self.assertIn("function confirmDeleteAuthGroup(", html)
        self.assertIn("function closeDeleteAuthGroupConfirm(", html)
        self.assertIn("function replaceAuthEntryCards(entries, options = {}) {", html)
        self.assertIn("const { expandFirst = false } = options || {};", html)
        self.assertIn(
            "entries.forEach((entry, index) => addAuthEntryCard(entry, { expand: expandFirst && index === 0 }));",
            html,
        )
        self.assertIn("replaceAuthEntryCards(entries);", html)
        self.assertIn("/static/vendor/ace/ace.js?v=1.43.6", html)
        self.assertIn("/static/vendor/ace/mode-yaml.js?v=1.43.6", html)
        self.assertIn("/static/vendor/ace/theme-textmate.js?v=1.43.6", html)
        self.assertIn("/static/vendor/ace/theme-tomorrow_night.js?v=1.43.6", html)
        self.assertIn('id="authEntryImportYamlEditor"', html)
        self.assertIn('id="authEntryImportEditorShell"', html)
        self.assertIn("function initializeAuthEntryYamlEditor(", html)
        self.assertIn("function getAuthEntryYamlValue(", html)
        self.assertIn("function setAuthEntryYamlValue(", html)
        self.assertIn("function replaceAuthEntryYamlRange(", html)
        self.assertIn("function syncAuthEntryYamlEditorTheme(", html)
        self.assertIn("function buildAuthEntryYamlWithInsertedTemplate(", html)
        self.assertIn("function focusInsertedAuthEntryTemplateId(", html)
        self.assertIn("function suggestNextAuthEntryTemplateId(", html)
        self.assertIn("ace.config.set('basePath', '/static/vendor/ace');", html)
        self.assertIn("authEntryYamlEditor.session.setMode('ace/mode/yaml');", html)
        self.assertIn("authEntryYamlEditor.renderer.setShowGutter(true);", html)
        self.assertIn("authEntryYamlEditor.renderer.setShowInvisibles('tab space');", html)
        self.assertIn("authEntryYamlEditor.session.replace(", html)
        self.assertIn("authEntryYamlEditor.session.setUseSoftTabs(true);", html)
        self.assertIn("authEntryYamlEditor.session.setTabSize(2);", html)
        self.assertIn("bindKey: { win: 'Ctrl-/', mac: 'Command-/' }", html)
        self.assertNotIn("key-a", html)
        self.assertIn('id="authEntryErrorModal"', html)
        self.assertIn("showActionError('保存 Provider'", html)
        self.assertIn("showActionError('删除 Provider'", html)
        self.assertIn("showActionError('拉取模型'", html)

        self.assertIn(".providers-page .provider-help-popover {", css)
        self.assertIn(".providers-page .provider-batch-summary {", css)
        self.assertNotIn(".providers-page .provider-batch-delete-modal-dialog {", css)
        self.assertIn(".providers-page .provider-table-checkbox {", css)
        self.assertIn(".providers-page .model-test-toolbar {", css)
        self.assertIn(".providers-page .model-test-table {", css)
        self.assertIn(".providers-page .model-test-status-badge {", css)
        self.assertIn(".providers-page .provider-group-list {", css)
        self.assertIn(".providers-page .provider-group-card {", css)
        self.assertIn(".providers-page .provider-group-header {", css)
        self.assertIn(".providers-page .provider-group-heading {", css)
        self.assertIn(".providers-page .provider-group-batch-btn {", css)
        self.assertIn(".providers-page .provider-group-title {", css)
        self.assertIn(".providers-page #providersContainer.providers-table-shell {", css)
        self.assertIn(':root[data-theme="dark"] .providers-page .provider-group-card {', css)
        self.assertIn(".providers-page .provider-editor-modal .provider-form-grid {", css)
        self.assertIn(".providers-page .provider-proxy-row {", css)
        self.assertIn(".providers-page .provider-proxy-row.is-custom {", css)
        self.assertIn("grid-template-columns: minmax(126px, 150px) minmax(0, 1fr);", css)
        self.assertIn(".providers-page .provider-editor-modal .provider-model-list-section {", css)
        self.assertNotIn(".providers-page .provider-modal-tabs {", css)
        self.assertNotIn(".providers-page .provider-modal-tab-btn {", css)
        self.assertNotIn(".providers-page .provider-modal-tab-panel[hidden] {", css)
        self.assertIn(".providers-page .provider-list-table col.provider-select-col {", css)
        self.assertIn(".providers-page .drag-handle-button {", css)
        self.assertIn(".providers-page .drag-handle-placeholder {", css)
        self.assertIn(".providers-page .providers-table tbody tr.is-drag-over-before td {", css)
        self.assertIn(".providers-page .providers-table tbody tr.is-drag-over-after td {", css)
        self.assertIn(".providers-page .btn-action:disabled {", css)
        self.assertIn(':root[data-theme="dark"] .providers-page .drag-handle-button {', css)
        self.assertIn(".providers-page .field-label-with-help {", css)
        self.assertIn(".providers-page .field-mode-badge {", css)
        self.assertNotIn(".providers-page .field-inline-note {", css)
        self.assertNotIn(".providers-page .custom-select-trigger-summary {", css)
        self.assertIn(".providers-page .custom-select.is-multi .custom-select-trigger {", css)
        self.assertNotIn(".providers-page .custom-select-trigger-badge {", css)
        self.assertIn(".providers-page .custom-select-option-check {", css)
        self.assertIn(".providers-page .custom-select-menu-hint {", css)
        self.assertIn(".providers-page .entry-import-editor {", css)
        self.assertIn(".providers-page .entry-import-editor .ace_gutter-cell,", css)
        self.assertIn(
            ".providers-page .entry-import-editor .ace_invisible.ace_invisible_eol {",
            css,
        )
        self.assertIn(
            ".providers-page .entry-import-editor-shell.is-editor-ready .entry-import-textarea {",
            css,
        )
        self.assertNotIn(".providers-page .compatibility-card {", css)

        self.assertNotIn('id="chatWhitelistToggle"', html)
        self.assertIn('id="chatWhitelistToggle"', users_html)
        self.assertIn("function showUsersHelp(", users_html)
        self.assertIn("function initUsersHelpInteractions()", users_html)
        self.assertIn("scheduleUsersHelpPopoverHide()", users_html)
        self.assertIn("function parseLocalDateTime(value)", users_html)
        self.assertNotIn("new Date(user.created_at)", users_html)
        self.assertIn("formatDateTime(user.created_at)", users_html)

    def test_oauth_template_contains_oauth_workflows(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "oauth.html"
        html = template_path.read_text(encoding="utf-8")
        css_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "static" / "css" / "oauth.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn("Codex OAuth", html)
        self.assertIn("Claude OAuth", html)
        self.assertIn('class="oauth-toolbar-section"', html)
        self.assertIn('class="oauth-toolbar-card"', html)
        self.assertIn('id="oauthTopTabBtn_codex"', html)
        self.assertIn('id="oauthTopTabBtn_claude"', html)
        self.assertIn('id="oauthTopTabPanel_claude"', html)
        self.assertIn('id="codexRefreshAuthLinkBtn"', html)
        self.assertIn('id="claudeRefreshAuthLinkBtn"', html)
        self.assertIn('id="claudeAuthUrlBox"', html)
        self.assertIn('id="claudeCallbackUrlInput"', html)
        self.assertIn('id="claudeSubmitCallbackBtn"', html)
        self.assertIn('id="claudeAuthFileList"', html)
        self.assertIn("Claude 可用模型", html)
        self.assertIn('id="claudeModelIdInput"', html)
        self.assertIn('id="claudeAddModelBtn"', html)
        self.assertIn('id="claudeModelList"', html)
        claude_panel_index = html.index('id="oauthTopTabPanel_claude"')
        claude_model_index = html.index('id="claudeModelIdInput"')
        claude_reference_index = html.index('class="oauth-model-reference-links"', claude_model_index)
        claude_auth_file_index = html.index('id="claudeAuthFileList"')
        self.assertLess(claude_panel_index, claude_model_index)
        self.assertLess(claude_model_index, claude_reference_index)
        self.assertLess(claude_reference_index, claude_auth_file_index)
        self.assertLess(claude_model_index, claude_auth_file_index)
        self.assertIn("<code>http://localhost:1455/auth/callback</code>", html)
        self.assertIn("<code>http://localhost:54545/callback</code>", html)
        self.assertIn("http://localhost:54545/callback", html)
        self.assertIn("data/oauth/claude", html)
        self.assertIn("Codex 可用模型", html)
        self.assertNotIn('id="codexRefreshModelsBtn"', html)
        self.assertNotIn("刷新模型", html)
        self.assertIn('id="codexModelIdInput"', html)
        self.assertIn('id="codexAddModelBtn"', html)
        self.assertIn('id="codexModelList"', html)
        codex_panel_index = html.index('id="oauthTopTabPanel_codex"')
        codex_model_index = html.index('id="codexModelIdInput"')
        self.assertLess(codex_panel_index, codex_model_index)
        self.assertLess(codex_model_index, claude_panel_index)
        self.assertIn("https://raw.githubusercontent.com/router-for-me/models/refs/heads/main/models.json", html)
        self.assertIn("https://models.router-for.me/models.json", html)
        self.assertEqual(
            2,
            html.count('href="https://raw.githubusercontent.com/router-for-me/models/refs/heads/main/models.json"'),
        )
        self.assertEqual(2, html.count('href="https://models.router-for.me/models.json"'))
        self.assertIn("<ul>", html)
        self.assertNotIn("function groupCodexModels", html)
        self.assertNotIn("display_name", html)
        self.assertNotIn("modelsUpdatedAt", html)
        self.assertNotIn("modelsSource", html)
        self.assertNotIn("oauth-model-group-title", html)
        self.assertNotIn("最近刷新：", html)
        self.assertNotIn(".oauth-page .oauth-model-group-title", css)
        self.assertNotIn("查看本地生成的 Codex OAuth 认证文件", html)
        self.assertNotIn("codexRefreshAuthFilesBtn", html)
        self.assertIn('id="codexAuthFileToolbar"', html)
        self.assertIn('id="codexSelectAllAuthFiles"', html)
        self.assertIn('id="codexImportAuthFilesInput"', html)
        self.assertIn('id="codexImportAuthFilesBtn"', html)
        self.assertIn('id="codexRefreshSelectedQuotaBtn"', html)
        self.assertIn('aria-label="刷新选中额度"', html)
        self.assertIn('id="codexExportSelectedAuthFilesBtn"', html)
        self.assertIn('aria-label="导出选中认证文件"', html)
        self.assertIn('id="codexDeleteSelectedAuthFilesBtn"', html)
        self.assertIn('aria-label="删除选中认证文件"', html)
        self.assertIn('id="codexBatchDeletePopover"', html)
        self.assertIn('id="codexAuthFilePagination"', html)
        self.assertIn("authFilePageSize: 50", html)
        self.assertIn("selectedAuthFiles: new Set()", html)
        self.assertIn("batchDeleteConfirm: false", html)
        self.assertIn("batchDeleting: false", html)
        self.assertIn("batchExporting: false", html)
        self.assertIn("batchImporting: false", html)
        self.assertIn(
            "codexAuthState.quotaLoadingByFile[name] = false;\n                        renderCodexAuthFiles();",
            html,
        )
        self.assertIn("已选择 ${selectedSize} 个", html)
        self.assertNotIn("已选择 ${selectedSize} 个 / 共", html)
        self.assertIn("function renderCodexQuotaProgress", html)
        self.assertIn("function renderTrashIcon", html)
        self.assertIn("function copyTextToClipboard(text)", html)
        self.assertIn("function copyTextWithTextarea(text)", html)
        self.assertIn(
            "navigator.clipboard && typeof navigator.clipboard.writeText === 'function'",
            html,
        )
        self.assertIn("copyTextToClipboard(codexAuthState.authorizationUrl)", html)
        self.assertIn("copyTextToClipboard(normalizedModelName)", html)
        self.assertNotIn(
            "await navigator.clipboard.writeText(codexAuthState.authorizationUrl)",
            html,
        )
        self.assertNotIn(
            "await navigator.clipboard.writeText(normalizedModelName)",
            html,
        )
        self.assertIn("function addCodexModel", html)
        self.assertIn("modelDeleteConfirmId: ''", html)
        self.assertIn("function toggleCodexModelDeleteConfirm", html)
        self.assertIn("function deleteCodexModel", html)
        self.assertNotIn("Codex 模型已添加", html)
        self.assertNotIn("Codex 模型已删除", html)
        self.assertIn("function deleteSelectedCodexAuthFiles", html)
        self.assertIn("function triggerCodexAuthFileImport", html)
        self.assertIn("function handleCodexAuthFileImportChange", html)
        self.assertIn("function exportSelectedCodexAuthFiles", html)
        self.assertIn("function downloadSelectedAuthFiles", html)
        self.assertIn("function uploadAuthFiles", html)
        self.assertIn("function requestDeleteCodexAuthFile", html)
        self.assertIn("function deleteCodexAuthFile", html)
        self.assertIn("method: 'DELETE'", html)
        self.assertIn("function normalizeCodexAvailabilityStatus", html)
        self.assertIn("function getCodexAuthFileInfo", html)
        self.assertIn("查看 Codex 可用模型说明", html)
        self.assertIn("查看 Codex 认证文件状态说明", html)
        self.assertIn("POST /v1/chat/completions", html)
        self.assertIn("POST /v1/responses", html)
        self.assertIn("POST /v1/messages", html)
        self.assertIn("https://chatgpt.com/backend-api/codex/responses", html)
        self.assertIn("认证失败", html)
        self.assertIn("额度用完", html)
        self.assertIn("{ key: 'auth_failed', label: '认证失败', count: counts.auth_failed }", html)
        self.assertIn("{ key: 'quota_exhausted', label: '额度用完', count: counts.quota_exhausted }", html)
        self.assertIn("{ key: 'other_unavailable', label: '其他', count: counts.other_unavailable }", html)
        self.assertIn("function getCodexAuthFileFilterKey(file)", html)
        self.assertIn("if (status === 'quota_cooldown') return 'quota_exhausted';", html)
        self.assertIn("if (status === 'quota_cooldown' || status === 'quota_exhausted') return '额度用完';", html)
        self.assertIn("return files.filter(file => getCodexAuthFileFilterKey(file) === filter);", html)
        self.assertNotIn("{ key: 'unavailable', label: '不可用'", html)
        self.assertNotIn("{ key: 'quota_cooldown', label: '额度冷却'", html)
        self.assertNotIn("{ key: 'filtered', label: '已过滤'", html)
        self.assertNotIn("配额冷却中", html)
        self.assertNotIn("配额已耗尽", html)
        self.assertNotIn("quota_cooldown: 0", html)
        self.assertNotIn("filtered: 0", html)
        self.assertNotIn("“信息”只显示最近一次非 success 的数据面错误摘要", html)
        self.assertIn('class="oauth-auth-file-status"', html)
        self.assertIn('class="oauth-auth-file-info"', html)
        self.assertIn('class="oauth-status-text ${availabilityClass}"', html)
        self.assertIn("<span>信息：</span>", html)
        self.assertIn("<span>上次刷新：</span>", html)
        self.assertIn("formatCodexQuotaRefreshedAt(file, quota)", html)
        self.assertIn("quota_refreshed_at", html)
        self.assertNotIn("formatCodexUsageStatusLabel", html)
        self.assertNotIn("oauth-auth-file-error", html)
        self.assertIn('class="oauth-icon-button"', html)
        self.assertNotIn("oauth-icon-button-primary", html)
        self.assertIn("return `${percent.text} 剩余", html)
        self.assertNotIn("return `${escapeHtml(window.label || 'Codex')}：${percent.text}", html)
        self.assertIn('class="btn btn-primary" id="codexSubmitCallbackBtn"', html)
        self.assertIn('id="codexCallbackSection" hidden', html)
        self.assertIn('id="codexCallbackUrlInput"', html)
        self.assertIn("callbackSection.hidden = !hasAuthorizationUrl", html)
        self.assertIn("/api/oauth/codex/session", html)
        self.assertIn("/api/oauth/codex/callback", html)
        self.assertIn("/api/oauth/codex/auth-files", html)
        self.assertIn("/api/oauth/codex/auth-files/export", html)
        self.assertIn("/api/oauth/codex/auth-files/import", html)
        self.assertIn("/api/oauth/codex/models", html)
        self.assertNotIn("/api/oauth/codex/models/refresh", html)
        self.assertIn("function refreshClaudeAuthLink", html)
        self.assertIn("/api/oauth/claude/session", html)
        self.assertIn("/api/oauth/claude/callback", html)
        self.assertIn("/api/oauth/claude/auth-files", html)
        self.assertIn("/api/oauth/claude/auth-files/export", html)
        self.assertIn("/api/oauth/claude/auth-files/import", html)
        self.assertIn("/api/oauth/claude/models", html)
        self.assertIn("https://api.anthropic.com/v1/messages?beta=true", html)
        self.assertIn("function addClaudeModel", html)
        self.assertIn("function deleteClaudeModel", html)
        self.assertIn("function deleteClaudeAuthFile", html)
        self.assertIn('class="oauth-toolbar-summary"', html)
        self.assertIn('class="oauth-toolbar-pill oauth-toolbar-pill-primary"', html)
        self.assertIn('id="codexAuthFileCount"', html)
        self.assertIn('id="claudeAuthFileCount"', html)
        self.assertIn('id="claudeAuthFileToolbar"', html)
        self.assertIn('id="claudeSelectAllAuthFiles"', html)
        self.assertIn('id="claudeSelectedAuthFileCount"', html)
        self.assertIn('id="claudeImportAuthFilesInput"', html)
        self.assertIn('id="claudeImportAuthFilesBtn"', html)
        self.assertIn('id="claudeExportSelectedAuthFilesBtn"', html)
        self.assertIn('id="claudeDeleteSelectedAuthFilesBtn"', html)
        self.assertIn('id="claudeBatchDeletePopover"', html)
        self.assertIn("function renderOAuthToolbarSummary", html)
        self.assertIn("function toggleClaudeSelectAllAuthFiles", html)
        self.assertIn("function toggleClaudeAuthFileSelection", html)
        self.assertIn("function triggerClaudeAuthFileImport", html)
        self.assertIn("function handleClaudeAuthFileImportChange", html)
        self.assertIn("function exportSelectedClaudeAuthFiles", html)
        self.assertIn("function deleteSelectedClaudeAuthFiles", html)
        self.assertIn("function requestDeleteClaudeAuthFile", html)
        self.assertIn("function removeClaudeAuthFileFromState", html)
        self.assertIn(".oauth-page .oauth-toolbar-card", css)
        self.assertIn(".oauth-page .oauth-toolbar-summary", css)
        self.assertIn(".oauth-page .oauth-toolbar-pill", css)
        self.assertIn(".oauth-page .oauth-tabs", css)
        self.assertIn(".oauth-page .oauth-model-editor", css)
        self.assertIn(".oauth-page .oauth-model-summary:empty", css)
        self.assertIn(".oauth-page .oauth-model-delete-wrap", css)
        self.assertIn(".oauth-page .oauth-model-grid", css)
        self.assertIn(".oauth-page .oauth-model-item", css)
        self.assertIn(".oauth-page .oauth-title-with-help", css)
        self.assertIn(".oauth-page .oauth-help-button", css)
        self.assertIn(".oauth-page .oauth-help-popover", css)
        self.assertIn(".oauth-page .oauth-auth-file-item", css)
        self.assertIn(".oauth-page .oauth-auth-file-toolbar", css)
        self.assertIn(".oauth-page .oauth-auth-file-status", css)
        self.assertIn(".oauth-page .oauth-auth-file-info", css)
        self.assertIn(".oauth-page .oauth-toolbar-action-wrap", css)
        self.assertIn(".oauth-page .oauth-icon-button", css)
        self.assertNotIn(".oauth-page .oauth-icon-button-primary", css)
        self.assertIn(".oauth-page .oauth-icon-button-danger", css)
        self.assertIn(".oauth-page .oauth-quota-refreshed-at", css)
        self.assertIn(".oauth-page .oauth-quota-progress", css)
        self.assertIn(".oauth-page .oauth-delete-popover", css)
        self.assertIn(".oauth-page .oauth-status-text", css)
        self.assertNotIn(".oauth-page .oauth-auth-file-error", css)
        self.assertIn(".oauth-page .oauth-pagination", css)
        self.assertIn(':root[data-theme="dark"] .oauth-page .btn-secondary', css)

    def test_settings_template_contains_oauth_network_settings(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "presentation"
        html = (root / "templates" / "settings.html").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "settings.css").read_text(encoding="utf-8")
        web_controller_py = (root / "web_controller.py").read_text(encoding="utf-8")

        self.assertIn("OAuth", html)
        self.assertIn("客户端 IP", html)
        self.assertIn('class="section-card settings-shell-card settings-shell-card-client-ip"', html)
        self.assertIn('id="realIpEnabled"', html)
        self.assertIn('id="realIpHeader"', html)
        self.assertIn('id="realIpHeaderField" hidden', html)
        self.assertIn('data-settings-help-topic="real_client_ip"', html)
        self.assertIn("function collectClientIpSettingsPayload()", html)
        self.assertIn("function saveClientIpSettings()", html)
        self.assertIn("function syncRealIpFields()", html)
        self.assertIn("real_ip_enabled: document.getElementById", html)
        self.assertIn("real_ip_header: document.getElementById", html)
        self.assertIn("header 缺失或不是合法 IP 时会回退到对端 IP", html)
        self.assertIn('fetch("/api/settings/system/client-ip"', html)
        self.assertIn('id="oauthEnabled"', html)
        self.assertIn('id="oauthProxyMode"', html)
        self.assertIn('<label class="setting-label" for="oauthProxyMode">模式</label>', html)
        self.assertIn('aria-label="OAuth 出站代理模式"', html)
        self.assertIn('class="oauth-toggle-row"', html)
        self.assertIn('class="oauth-toggle-card oauth-enable-row"', html)
        self.assertIn('class="oauth-toggle-card oauth-ssl-row"', html)
        self.assertIn('id="oauthProxyRow"', html)
        self.assertIn('id="oauthProxyCustomField" hidden', html)
        self.assertIn('class="setting-field oauth-proxy-custom-field" id="oauthProxyCustomField" hidden', html)
        self.assertIn('id="oauthProxy"', html)
        self.assertIn('aria-label="自定义 OAuth 出站代理地址"', html)
        self.assertIn('class="form-control sensitive-input-control"', html)
        self.assertIn('data-sensitive-toggle-for="oauthProxy"', html)
        self.assertIn('data-sensitive-label="OAuth 出站代理"', html)
        self.assertIn('const settingsSensitiveInputIds = ["adminPassword", "oauthProxy"];', html)
        self.assertIn("function handleSensitiveInputCopy(event)", html)
        self.assertIn('input.addEventListener("copy", handleSensitiveInputCopy);', html)
        self.assertIn("function updateSensitiveInputSaveButtonState(inputId)", html)
        self.assertIn("function saveSensitiveInputOnBlur(inputId)", html)
        self.assertIn('id="oauthVerifySsl"', html)
        self.assertNotIn('id="oauthAutoConfirmProxyWarning"', html)
        self.assertIn('id="oauthDetailsPanel" hidden', html)
        self.assertNotIn('id="saveOAuthSettingsBtn"', html)
        self.assertIn("function collectOAuthSettingsPayload()", html)
        self.assertIn("function getOAuthProxyMode()", html)
        self.assertIn("function syncOAuthProxyFields()", html)
        self.assertNotIn("function scheduleOAuthSettingsSave(", html)
        self.assertNotIn("oauthAutoSaveDelayMs", html)
        self.assertIn("function syncOAuthDetailsVisibility(enabled)", html)
        self.assertIn("function syncOAuthNavLink(enabled)", html)
        self.assertIn("function saveOAuthSettings()", html)
        self.assertIn("preserveSensitiveVisibility", html)
        self.assertIn("fillOAuthSettingsForm(data.settings, { preserveSensitiveVisibility: true });", html)
        self.assertIn('fetch("/api/settings/system/oauth"', html)
        self.assertIn('data-settings-help-topic="oauth_enabled"', html)
        self.assertIn('data-settings-help-topic="oauth_proxy"', html)
        self.assertIn('data-settings-help-topic="oauth_verify_ssl"', html)
        self.assertNotIn('for="oauthProxyMode">OAuth 出站代理模式</label>', html)
        self.assertIn("不是下游客户端访问本服务的入口代理", html)
        self.assertIn("HTTP_PROXY", html)
        self.assertIn("它不保证读取操作系统桌面代理设置", html)
        self.assertIn("留空时等同直连", html)
        self.assertIn("自定义代理支持在 URL 中填写账号密码", html)
        self.assertIn("用户名里包含冒号需要手动写成 <code>%3A</code>", html)
        self.assertIn("OAuth token 请求、token 刷新、Codex 配额查询和 OAuth 数据面代理", html)
        self.assertNotIn('data-settings-help-topic="oauth_auto_confirm_proxy_warning"', html)
        self.assertIn("function showSettingsHelp(", html)
        self.assertIn("function initSettingsHelpInteractions()", html)
        self.assertIn("scheduleSettingsHelpPopoverHide()", html)
        self.assertIn('popover.style.setProperty("--popover-arrow-left"', html)
        self.assertIn(".settings-page .oauth-toggle-row", css)
        self.assertIn(".settings-page .oauth-toggle-card", css)
        self.assertIn(".settings-page .setting-toggle-inline", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 360px));", css)
        self.assertIn("width: min(360px, 100%);", css)
        self.assertIn(".settings-page .oauth-details-panel[hidden]", css)
        self.assertIn(".settings-page .oauth-settings-block", css)
        self.assertIn(".settings-page .oauth-proxy-grid", css)
        self.assertIn(".settings-page .oauth-proxy-grid.is-custom", css)
        self.assertIn(".settings-page .settings-grid-client-ip", css)
        self.assertNotIn(".settings-page .oauth-settings-block-ssl", css)
        self.assertNotIn(".settings-page .oauth-network-toggle", css)
        self.assertIn(".settings-page .settings-grid-oauth {", css)
        self.assertIn(".settings-page .settings-help-popover {\n    position: fixed;", css)
        self.assertIn("left: var(--popover-arrow-left, 24px);", css)
        self.assertIn('self._app.route("/api/settings/system/client-ip", methods=["PUT"])', web_controller_py)
        self.assertIn('self._app.route("/api/settings/system/oauth", methods=["PUT"])', web_controller_py)

    def test_provider_model_list_tidy_sorts_and_manual_cleanup_is_explicit(
        self,
    ) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")

        self.assertNotIn(
            "document.getElementById('providerModelList').addEventListener('blur'",
            html,
        )
        self.assertNotIn(
            "document.getElementById('providerModelList').addEventListener('input'",
            html,
        )
        self.assertNotIn("applyNormalizedModelListValue();", html)
        self.assertIn("function setModelTestRowsFromModels(", html)

        script_start = html.index("function normalizeModelListItems")
        script_end = html.index("function fillForm")
        script = html[script_start:script_end]

        node_script = f"""
const vm = require("vm");
const sandbox = {{
  console,
  messages: [],
  authGroups: [],
  modelTestRows: [],
  modelTestRowSequence: 0,
  escapeHtml(value) {{
    return String(value ?? "");
  }},
  renderCustomSelectOptions() {{}},
  formatActionErrorMessage(_title, detail, options) {{
    return detail?.message || options?.fallback || "";
  }},
  showMessage(message, level) {{
    sandbox.messages.push({{ message, level }});
  }},
  document: {{
    elements: {{
      providerName: {{ value: " demo " }},
      providerApi: {{ value: " https://example.com/v1/chat/completions " }},
      providerAuthMode: {{ value: "auth_group" }},
      providerAuthGroup: {{ value: " shared-pool " }},
      modelTestAuthEntry: {{ value: " entry-a ", innerHTML: "", disabled: false }},
      modelTestAuthEntryShell: {{ hidden: false }},
      modelTestAuthHint: {{ textContent: "", hidden: true }},
      modelTestSummary: {{ textContent: "" }},
      modelTestTableContainer: {{ innerHTML: "" }},
      runSelectedModelTestsBtn: {{ disabled: false, textContent: "" }},
      deleteSelectedModelTestsBtn: {{ disabled: false }},
      tidyModelListBtn: {{ disabled: false }},
      modelTestSelectAllCheckbox: {{ checked: false, indeterminate: false }},
      providerSourceFormat: {{ value: "openai_chat" }},
      providerApiKey: {{ value: " secret " }},
      providerProxy: {{ value: "" }},
      providerTimeout: {{ value: "" }},
      providerRetries: {{ value: "" }},
      providerVerifySsl: {{ value: "false" }},
      providerHook: {{ value: "" }},
    }},
    getElementById(id) {{
      return this.elements[id] || null;
    }},
  }},
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
sandbox.renderCustomSelectOptions = () => {{}};
sandbox.syncCustomSelectValue = () => {{}};

sandbox.setModelTestRowsFromModels(" beta \\nAlpha\\nalpha\\nBeta\\nbeta\\n");
const collectedBefore = sandbox.collectFormData();
sandbox.tidyModelList();
const collectedAfter = sandbox.collectFormData();

process.stdout.write(JSON.stringify({{
  beforeModelList: collectedBefore.model_list,
  beforeAuthGroup: collectedBefore.auth_group,
  beforeApiKey: collectedBefore.api_key,
  afterRows: sandbox.modelTestRows.map(row => row.model),
  afterModelList: collectedAfter.model_list,
  afterAuthGroup: collectedAfter.auth_group,
  afterApiKey: collectedAfter.api_key,
  countText: sandbox.document.elements.modelTestSummary.textContent,
  message: sandbox.messages[0]?.message || "",
}}));
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertEqual("beta\nAlpha\nalpha\nBeta", payload["beforeModelList"])
        self.assertEqual("shared-pool", payload["beforeAuthGroup"])
        self.assertEqual("", payload["beforeApiKey"])
        self.assertEqual(["Alpha", "Beta", "alpha", "beta"], payload["afterRows"])
        self.assertEqual("Alpha\nBeta\nalpha\nbeta", payload["afterModelList"])
        self.assertEqual("shared-pool", payload["afterAuthGroup"])
        self.assertEqual("", payload["afterApiKey"])
        self.assertIn("4", payload["countText"])
        self.assertTrue(payload["message"])

    def test_fetch_models_button_supports_auth_group_mode(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")

        script_start = html.index("function canFetchModels")
        script_end = html.index("function openCreateModal")
        script = html[script_start:script_end]

        node_script = f"""
const vm = require("vm");
const sandbox = {{
  console,
  document: {{
    elements: {{
      providerApi: {{ value: " https://example.com/v1/chat/completions " }},
      providerAuthMode: {{ value: "auth_group" }},
      providerAuthGroup: {{ value: " pool-a " }},
      fetchModelsBtn: {{ disabled: false, dataset: {{}}, title: "" }},
    }},
    getElementById(id) {{
      return this.elements[id] || null;
    }},
  }},
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
sandbox.updateFetchModelsButtonState();
const authGroupDisabled = sandbox.document.elements.fetchModelsBtn.disabled;
const authGroupTitle = sandbox.document.elements.fetchModelsBtn.title;
sandbox.document.elements.providerAuthGroup.value = "";
sandbox.updateFetchModelsButtonState();
const missingGroupDisabled = sandbox.document.elements.fetchModelsBtn.disabled;
sandbox.document.elements.providerAuthGroup.value = " pool-a ";
sandbox.document.elements.providerAuthMode.value = "legacy_api_key";
sandbox.updateFetchModelsButtonState();
const legacyDisabled = sandbox.document.elements.fetchModelsBtn.disabled;
process.stdout.write(JSON.stringify({{
  authGroupDisabled,
  authGroupTitle,
  missingGroupDisabled,
  legacyDisabled,
}}));
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertFalse(payload["authGroupDisabled"])
        self.assertEqual("", payload["authGroupTitle"])
        self.assertTrue(payload["missingGroupDisabled"])
        self.assertFalse(payload["legacyDisabled"])

    def test_fetch_models_request_is_aborted_when_modal_closes(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")

        script = "\n".join(
            [
                "let fetchModelsAbortController = null;",
                (
                    "function collectFormData() { return { api: 'https://example.com/v1/chat/completions', "
                    "api_key: '', auth_group: '', proxy: '', timeout_seconds: '', verify_ssl: '' }; }"
                ),
                html[html.index("function canFetchModels()") : html.index("function openCreateModal()")],
                html[html.index("async function fetchModels()") : html.index("function initProvidersPage()")],
            ]
        )

        node_script = f"""
const vm = require("vm");
const sandbox = {{
  console,
  URLSearchParams,
  AbortController,
  aborted: false,
  actionErrorCalls: 0,
  document: {{
    elements: {{
      providerApi: {{ value: "https://example.com/v1/chat/completions" }},
      providerAuthMode: {{ value: "legacy_api_key" }},
      providerAuthGroup: {{ value: "" }},
      modelTestAuthEntry: {{ value: "" }},
      fetchModelsBtn: {{ disabled: false, dataset: {{}}, title: "", textContent: "拉取模型" }},
    }},
    getElementById(id) {{
      return this.elements[id] || null;
    }},
  }},
  fetch(url, options) {{
    sandbox.fetchUrl = url;
    sandbox.fetchSignalAttached = !!options?.signal;
    return new Promise((resolve, reject) => {{
      options.signal.addEventListener("abort", () => {{
        sandbox.aborted = true;
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      }});
    }});
  }},
  showMessage() {{}},
  showActionError() {{
    sandbox.actionErrorCalls += 1;
  }},
  openFetchModelPicker() {{
    sandbox.pickerOpened = true;
    return 0;
  }},
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
(async () => {{
  const promise = sandbox.fetchModels();
  sandbox.cancelFetchModelsRequest();
  await promise;
  process.stdout.write(JSON.stringify({{
    aborted: sandbox.aborted,
    fetchSignalAttached: sandbox.fetchSignalAttached,
    actionErrorCalls: sandbox.actionErrorCalls,
    loading: sandbox.document.elements.fetchModelsBtn.dataset.loading,
    disabled: sandbox.document.elements.fetchModelsBtn.disabled,
    text: sandbox.document.elements.fetchModelsBtn.textContent,
    pickerOpened: !!sandbox.pickerOpened,
  }}));
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertTrue(payload["fetchSignalAttached"])
        self.assertTrue(payload["aborted"])
        self.assertEqual(0, payload["actionErrorCalls"])
        self.assertEqual("false", payload["loading"])
        self.assertFalse(payload["disabled"])
        self.assertEqual("拉取模型", payload["text"])
        self.assertFalse(payload["pickerOpened"])

    def test_provider_drag_drop_helpers_keep_enabled_group_before_disabled_group(
        self,
    ) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")

        script_start = html.index("function isProviderEnabled")
        script_end = html.index("function getProviderTabButton")
        script = html[script_start:script_end]

        node_script = f"""
const vm = require("vm");
const sandbox = {{
  console,
  providers: [
    {{ name: "enabled-a", enabled: true }},
    {{ name: "enabled-b", enabled: true }},
    {{ name: "disabled-a", enabled: false }},
    {{ name: "disabled-b", enabled: false }},
  ],
  providerBatchActionInFlight: false,
  providerOrderActionInFlight: false,
  togglingProviderNames: new Set(),
  deletingProviderName: null,
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
process.stdout.write(JSON.stringify({{
  dropEnabled: sandbox.buildDroppedProviderOrderNames("enabled-a", "enabled-b", true),
  dropDisabled: sandbox.buildDroppedProviderOrderNames("disabled-b", "disabled-a", false),
  crossGroup: sandbox.buildDroppedProviderOrderNames("enabled-a", "disabled-a", true),
  mutationIdle: sandbox.hasProviderMutationInFlight(),
}}));
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertEqual(
            ["enabled-b", "enabled-a", "disabled-a", "disabled-b"],
            payload["dropEnabled"],
        )
        self.assertEqual(
            ["enabled-a", "enabled-b", "disabled-b", "disabled-a"],
            payload["dropDisabled"],
        )
        self.assertIsNone(payload["crossGroup"])
        self.assertFalse(payload["mutationIdle"])

    def test_provider_name_validation_helper_matches_input_limit(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")
        script = "\n".join(
            [
                "const providerNameMaxLength = 64;",
                "const providerNamePattern = /^[A-Za-z][A-Za-z0-9_]*$/;",
                html[
                    html.index("function getProviderNameValidationError")
                    : html.index("function updateProviderNameValidity")
                ],
            ]
        )

        node_script = f"""
const vm = require("vm");
const sandbox = {{ console }};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
process.stdout.write(JSON.stringify({{
  empty: sandbox.getProviderNameValidationError(""),
  safe: sandbox.getProviderNameValidationError("openai_1"),
  hyphen: sandbox.getProviderNameValidationError("openai-demo"),
  digitStart: sandbox.getProviderNameValidationError("1openai"),
  longName: sandbox.getProviderNameValidationError("a".repeat(65)),
}}));
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertEqual("请输入 provider 名称", payload["empty"])
        self.assertEqual("", payload["safe"])
        self.assertEqual("Provider 名称必须英文开头，且只能包含英文、数字和下划线", payload["hyphen"])
        self.assertEqual("Provider 名称必须英文开头，且只能包含英文、数字和下划线", payload["digitStart"])
        self.assertEqual("Provider 名称最多 64 个字符", payload["longName"])


class FrontendMessageLocalizationTests(unittest.TestCase):
    def test_app_version_matches_pyproject_version(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(pyproject["project"]["version"], get_app_version())

    def test_rendered_templates_use_unified_app_version(self) -> None:
        app = create_flask_app()
        app_version = get_app_version()
        expected_version = f'<span class="header-version" aria-label="当前版本">v{app_version}</span>'

        with app.test_request_context("/"):
            html = render_template(
                "providers.html",
                active_page="providers",
                chat_whitelist_enabled=False,
                current_username="",
                auth_enabled=False,
                oauth_enabled=False,
                api_key_management_enabled=False,
            )

        self.assertIn(expected_version, html)

    def test_ui_message_script_contains_localized_error_formatter(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "static" / "js" / "ui-message.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("function formatActionErrorMessage(", script)
        self.assertIn("window.showActionError = showActionError;", script)
        self.assertNotIn("failed to fetch models", script)
        self.assertNotIn("failed to toggle user status", script)

    def test_templates_use_versioned_scripts_and_titles(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "presentation"
        login_html = (root / "templates" / "login.html").read_text(encoding="utf-8")
        users_html = (root / "templates" / "users.html").read_text(encoding="utf-8")
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        base_page_html = (root / "templates" / "base_page.html").read_text(encoding="utf-8")
        theme_js = (root / "static" / "js" / "theme.js").read_text(encoding="utf-8")
        base_admin_html = (root / "templates" / "base_admin.html").read_text(encoding="utf-8")
        admin_base_css = (root / "static" / "css" / "admin-base.css").read_text(encoding="utf-8")
        web_controller_py = (root.parent / "presentation" / "web_controller.py").read_text(encoding="utf-8")
        app_version_expression = "{{ app_version|default('0.0.0') }}"

        self.assertIn("/static/js/ui-message.js?v=20260319-1", login_html)
        self.assertIn("/static/js/ui-message.js?v=20260319-1", users_html)
        self.assertIn("/static/js/ui-message.js?v=20260319-1", index_html)
        self.assertIn("/static/css/admin-base.css?v=20260603-4", base_page_html)
        self.assertIn("/static/js/theme.js?v=20260319-1", base_page_html)
        self.assertIn("showActionError('登录'", login_html)
        self.assertIn("showActionError('创建用户'", users_html)
        self.assertIn("showActionError('更新用户'", users_html)
        self.assertIn("showActionError('删除用户'", users_html)
        self.assertNotIn("Toggle theme", theme_js)
        self.assertIn('href="/">Provider 管理</a>', base_admin_html)
        self.assertIn("{% if oauth_enabled %}", base_admin_html)
        oauth_link_snippet = 'href="/oauth" data-nav-page="oauth">OAuth</a>'
        self.assertIn(oauth_link_snippet, base_admin_html)
        self.assertIn('data-nav-page="oauth"', base_admin_html)
        self.assertIn('href="/users">用户管理</a>', base_admin_html)
        self.assertIn('href="/statistics">统计概览</a>', base_admin_html)
        version_snippet = f'<span class="header-version" aria-label="当前版本">v{app_version_expression}</span>'
        settings_link_snippet = 'href="/settings">系统设置</a>'
        self.assertIn(version_snippet, base_admin_html)
        self.assertIn(".app-page .header-version", admin_base_css)
        self.assertIn("position: absolute;", admin_base_css)
        self.assertIn("top: -18px;", admin_base_css)
        self.assertLess(
            base_admin_html.index(settings_link_snippet),
            base_admin_html.index(version_snippet),
        )
        self.assertLess(
            base_admin_html.index("</nav>"),
            base_admin_html.index(version_snippet),
        )
        self.assertLess(
            base_admin_html.index('href="/">Provider 管理</a>'),
            base_admin_html.index(oauth_link_snippet),
        )
        self.assertLess(
            base_admin_html.index(oauth_link_snippet),
            base_admin_html.index('href="/users">用户管理</a>'),
        )
        self.assertLess(
            base_admin_html.index('href="/users">用户管理</a>'),
            base_admin_html.index('href="/statistics">统计概览</a>'),
        )
        self.assertIn('self._app.route("/")(auth(self.home))', web_controller_py)
        self.assertIn('self._app.route("/statistics")(auth(self.statistics_page))', web_controller_py)
        self.assertIn("def home(self) -> str:", web_controller_py)
        self.assertIn("oauth_enabled=self._is_oauth_enabled()", web_controller_py)

    def test_ui_message_formatter_appends_upstream_original_error(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "static" / "js" / "ui-message.js"
        node_script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({str(script_path)!r}, "utf8");
const sandbox = {{
  window: {{}},
  console: console,
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const output = sandbox.window.formatActionErrorMessage(
  "鎷夊彇妯″瀷",
  "https://example.com/v1/models returned 401",
  {{ fallback: "鎷夊彇妯″瀷澶辫触" }}
);
process.stdout.write(output);
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        stdout = completed.stdout.decode("utf-8")

        self.assertIn("https://example.com/v1/models returned 401", stdout)


class DashboardTemplateTests(unittest.TestCase):
    def test_index_template_uses_lazy_loaded_tabs(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "presentation"
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        index_css = (root / "static" / "css" / "index.css").read_text(encoding="utf-8")
        admin_base_css = (root / "static" / "css" / "admin-base.css").read_text(encoding="utf-8")

        self.assertIn("/static/css/index.css?v=20260409-6", index_html)
        self.assertIn("dashboard-tabs-section", index_html)
        self.assertIn('id="dashboardTabBtn_stats"', index_html)
        self.assertIn('id="dashboardTabBtn_userUsage"', index_html)
        self.assertIn('id="dashboardTabBtn_logs"', index_html)
        self.assertIn("调用汇总</button>", index_html)
        self.assertIn("用户用量</button>", index_html)
        self.assertNotIn("用户用量汇总</button>", index_html)
        self.assertNotIn("userUsageSortIndicator_request_model", index_html)
        self.assertIn('id="userUsageTable"', index_html)
        self.assertIn("function loadUserUsageSummary()", index_html)
        self.assertIn("function renderUserUsageSummary()", index_html)
        self.assertIn("function toggleUserUsageSort(", index_html)
        self.assertIn("function exportActiveDashboardTab()", index_html)
        self.assertIn(
            '<button class="btn btn-primary" onclick="exportActiveDashboardTab()">导出 Excel</button>', index_html
        )
        self.assertIn('id="username"', index_html)
        self.assertIn('id="requestModel"', index_html)
        self.assertIn('data-visible-chip-count="3"', index_html)
        self.assertIn("function getSelectedOptionValues(", index_html)
        self.assertIn("function buildMultiSelectTriggerMarkup(", index_html)
        self.assertIn("function buildDashboardSearchParams(", index_html)
        self.assertIn("function filterCustomSelectOptions(", index_html)
        self.assertIn("function selectFilteredCustomSelectOptions(", index_html)
        self.assertIn("custom-select-search-input", index_html)
        self.assertIn("custom-select-menu-action", index_html)
        self.assertIn("全选过滤结果", index_html)
        self.assertIn("custom-select-menu-action-box", index_html)
        self.assertIn("function switchDashboardTab(", index_html)
        self.assertIn("function loadActiveDashboardTabData()", index_html)
        self.assertIn("fetch(`/api/statistics?${params}`, { cache: 'no-store' })", index_html)
        self.assertIn("fetch(`/api/statistics/user-usage-summary?${params}`, { cache: 'no-store' })", index_html)
        self.assertIn("window.location.href = `/api/statistics/export?${params}`;", index_html)
        self.assertIn("fetch(`/api/request-logs?${params}`, { cache: 'no-store' })", index_html)
        self.assertIn("function parseLocalDateTime(value)", index_html)
        self.assertNotIn("new Date(log.start_time)", index_html)
        self.assertNotIn("new Date(log.end_time)", index_html)
        self.assertIn("calculateDurationSeconds(log.start_time, log.end_time)", index_html)
        self.assertIn("--dashboard-control-height: 40px;", index_css)
        self.assertIn(".dashboard-page .custom-select-trigger {", index_css)
        self.assertIn(
            ".dashboard-page .custom-select.is-multi .custom-select-trigger {",
            index_css,
        )
        self.assertIn(".dashboard-page .custom-select-trigger-chip {", index_css)
        self.assertIn(".dashboard-page .custom-select-option-check {", index_css)
        self.assertIn(".dashboard-page .custom-select-menu-hint {", index_css)
        self.assertIn(".dashboard-page .custom-select-menu-toolbar {", index_css)
        self.assertIn(".dashboard-page .custom-select-search-input {", index_css)
        self.assertIn(".dashboard-page .custom-select-menu-action {", index_css)
        self.assertIn(".dashboard-page .custom-select-empty {", index_css)
        self.assertIn(".dashboard-page .custom-select-menu-action-box {", index_css)
        self.assertIn(".dashboard-page .custom-select-menu-action.is-checked,", index_css)
        self.assertIn(
            ".dashboard-page .custom-select-menu-action.is-checked .custom-select-menu-action-box,", index_css
        )
        self.assertIn("--nav-tab-hover-bg:", admin_base_css)
        self.assertIn("body.app-page .field-help-button,", admin_base_css)
        self.assertIn("body.app-page .oauth-help-button", admin_base_css)
        self.assertIn(".provider-help-popover-body", admin_base_css)
        self.assertIn(".oauth-help-popover", admin_base_css)
        self.assertIn("color: #c2185b;", admin_base_css)

    def test_provider_template_keeps_model_row_change_from_rebuilding_table(self) -> None:
        providers_html = (
            Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        ).read_text(encoding="utf-8")

        self.assertIn("function refreshModelTestRowDisplay(rowId, inputElement)", providers_html)
        self.assertIn("function updateModelTestTableSummaryAndControls()", providers_html)
        self.assertIn('onchange="handleModelTestRowChange(${row.rowId}, this.value, this)"', providers_html)
        self.assertIn("function buildDroppedModelTestRows(dragRowId, targetRowId, placeAfter)", providers_html)
        self.assertIn("function handleModelTestRowDragStart(rowId, event)", providers_html)
        self.assertIn("function buildDroppedProviderOrderNames(name, targetName, placeAfter)", providers_html)
        self.assertIn("function handleProviderRowDragStart(name, groupKey, event)", providers_html)
        self.assertIn('class="drag-handle-button model-test-drag-handle"', providers_html)
        self.assertIn('class="drag-handle-button provider-drag-handle"', providers_html)
        self.assertNotIn(
            "function handleModelTestRowChange(rowId, value) {\n"
            "            const row = modelTestRows.find(item => item.rowId === rowId);\n"
            "            if (!row) return;\n"
            "            if (row.testing) return;\n"
            "            row.model = String(value || '').trim();\n"
            "            clearModelTestRowResult(row);\n"
            "            updateModelListCount();\n"
            "            renderModelTestTable();\n"
            "        }",
            providers_html,
        )

    def test_provider_drag_drop_helper_keeps_group_boundary(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")
        script_start = html.index("function isProviderEnabled")
        script_end = html.index("function getProviderTabButton")
        script = html[script_start:script_end]

        node_script = f"""
const vm = require("vm");
const sandbox = {{
  console,
  providers: [
    {{ name: "enabled-a", enabled: true }},
    {{ name: "enabled-b", enabled: true }},
    {{ name: "disabled-a", enabled: false }},
    {{ name: "disabled-b", enabled: false }},
  ],
  providerBatchActionInFlight: false,
  providerOrderActionInFlight: false,
  togglingProviderNames: new Set(),
  deletingProviderName: null,
  providerDragState: null,
  providerDragTargetElement: null,
  modelTestDragState: null,
  modelTestDragTargetElement: null,
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
process.stdout.write(JSON.stringify({{
  dropEnabled: sandbox.buildDroppedProviderOrderNames("enabled-a", "enabled-b", true),
  dropDisabled: sandbox.buildDroppedProviderOrderNames("disabled-b", "disabled-a", false),
  crossGroup: sandbox.buildDroppedProviderOrderNames("enabled-a", "disabled-a", true),
}}));
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertEqual(
            ["enabled-b", "enabled-a", "disabled-a", "disabled-b"],
            payload["dropEnabled"],
        )
        self.assertEqual(
            ["enabled-a", "enabled-b", "disabled-b", "disabled-a"],
            payload["dropDisabled"],
        )
        self.assertIsNone(payload["crossGroup"])


if __name__ == "__main__":
    unittest.main()
