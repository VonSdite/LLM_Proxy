from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from flask import Flask

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

    def test_provider_hidden_model_list_keeps_only_configured_models(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["visible", "hidden"],
                "hidden_model_list": ["hidden", "missing", "hidden"],
            }
        )

        self.assertEqual(("visible", "hidden"), schema.model_list)
        self.assertEqual(("hidden",), schema.hidden_model_list)
        self.assertEqual(["hidden"], schema.to_mapping()["hidden_model_list"])
        self.assertEqual(("hidden",), RuntimeProviderSpec.from_schema(schema).hidden_model_list)

    def test_provider_force_upstream_stream_defaults_to_false_and_accepts_true(self) -> None:
        default_schema = ProviderConfigSchema.from_mapping(
            {
                "name": "default-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )
        enabled_schema = ProviderConfigSchema.from_mapping(
            {
                "name": "stream-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "force_upstream_stream": True,
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertFalse(default_schema.force_upstream_stream)
        self.assertFalse(RuntimeProviderSpec.from_schema(default_schema).force_upstream_stream)
        self.assertTrue(enabled_schema.force_upstream_stream)
        self.assertTrue(RuntimeProviderSpec.from_schema(enabled_schema).force_upstream_stream)
        self.assertFalse(default_schema.to_mapping()["force_upstream_stream"])
        self.assertTrue(enabled_schema.to_mapping()["force_upstream_stream"])

    def test_provider_force_upstream_stream_rejects_invalid_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provider force_upstream_stream must be a boolean value"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "force_upstream_stream": "maybe",
                    "model_list": ["gpt-4.1"],
                }
            )

    def test_provider_safe_desensitization_defaults_to_false_and_accepts_true(self) -> None:
        default_schema = ProviderConfigSchema.from_mapping(
            {
                "name": "default-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )
        enabled_schema = ProviderConfigSchema.from_mapping(
            {
                "name": "privacy-provider",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "safe_desensitization_enabled": True,
                "model_list": ["gpt-4.1"],
            }
        )

        default_runtime = RuntimeProviderSpec.from_schema(default_schema)
        enabled_runtime = RuntimeProviderSpec.from_schema(enabled_schema)
        self.assertFalse(default_schema.safe_desensitization_enabled)
        self.assertFalse(default_runtime.safe_desensitization_enabled)
        self.assertTrue(enabled_schema.safe_desensitization_enabled)
        self.assertTrue(enabled_runtime.safe_desensitization_enabled)
        self.assertFalse(default_schema.to_mapping()["safe_desensitization_enabled"])
        self.assertTrue(enabled_schema.to_mapping()["safe_desensitization_enabled"])

    def test_provider_safe_desensitization_rejects_invalid_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provider safe_desensitization_enabled must be a boolean value"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "safe_desensitization_enabled": "maybe",
                    "model_list": ["gpt-4.1"],
                }
            )

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
    def test_hidden_model_remains_routable_but_is_not_visible(self) -> None:
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        manager = ProviderManager(ctx)
        manager.load_providers(
            (
                ProviderConfigSchema.from_mapping(
                    {
                        "name": "demo",
                        "api": "https://example.com/v1/chat/completions",
                        "api_key": "demo-key",
                        "model_list": ["visible", "hidden"],
                        "hidden_model_list": ["hidden"],
                    }
                ),
            )
        )

        self.assertEqual(("demo/hidden", "demo/visible"), manager.list_model_names())
        self.assertEqual(("demo/visible",), manager.list_visible_model_names())
        self.assertIsNotNone(manager.get_provider_for_model("demo/hidden"))
        provider_view = manager.get_provider_view("demo")
        self.assertIsNotNone(provider_view)
        assert provider_view is not None
        self.assertEqual(("hidden",), provider_view.hidden_model_list)

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

    def test_fetch_models_preview_uses_hook_fetch_models_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_dir = root_path / "hooks" / "custom"
            hook_dir.mkdir(parents=True)
            (hook_dir / "model_fetch_hook.py").write_text(
                """
class Hook:
    def fetch_models(self, ctx, payload):
        assert ctx.provider_name == "preview_provider"
        assert ctx.auth_group_name == "pool-a"
        assert ctx.auth_entry_id == "entry-a"
        assert payload["headers"]["Authorization"] == "Bearer sk-entry-a"
        assert payload["request_headers"]["Authorization"] == "Bearer sk-entry-a"
        assert payload["candidate_urls"] == [
            "https://example.com/v1/models",
            "https://example.com/models",
        ]
        assert payload["auth_group"] == "pool-a"
        assert payload["auth_entry_id"] == "entry-a"
        assert payload["proxy_mode"] == "direct"
        assert payload["proxy"] is None
        assert payload["timeout_seconds"] == 7
        assert payload["verify_ssl"] is True
        return {
            "data": [
                {"id": "hook-model"},
                {"id": "hook-model"},
                {"name": "second-model"},
            ]
        }
""".lstrip(),
                encoding="utf-8",
            )
            logger = FakeLogger()
            ctx = AppContext(
                logger=logger,
                config_manager=None,  # type: ignore[arg-type]
                root_path=root_path,
                flask_app=Flask(__name__),
            )
            service = ModelDiscoveryService(ctx)

            with patch(
                "src.services.model_discovery_service.requests.Session",
                side_effect=AssertionError("default model endpoint fetch should not run"),
            ):
                result = service.fetch_models_preview(
                    api="https://example.com/v1/chat/completions",
                    request_headers={"Authorization": "Bearer sk-entry-a"},
                    hook="custom/model_fetch_hook.py",
                    provider_name="preview_provider",
                    auth_group="pool-a",
                    auth_entry_id="entry-a",
                    timeout_seconds=7,
                    verify_ssl=True,
                )

        self.assertEqual(["hook-model", "second-model"], result["fetched_models"])
        self.assertEqual(2, result["fetched_count"])

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


class FrontendMessageLocalizationTests(unittest.TestCase):
    def test_app_version_matches_pyproject_version(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(pyproject["project"]["version"], get_app_version())


if __name__ == "__main__":
    unittest.main()
