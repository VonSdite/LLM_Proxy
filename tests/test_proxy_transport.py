from __future__ import annotations

import json
import ssl
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from websocket import ABNF

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
from src.external.upstream_websocket import (
    WebSocketUpstreamResponse,
    collect_websocket_response_body,
    normalize_websocket_message,
)
from src.proxy_core import resolve_stream_format
from src.repositories import AuthGroupRepository
from src.services.model_discovery_service import ModelDiscoveryService
from src.utils.database import create_connection_factory
from src.utils.local_time import now_local_datetime
from src.utils.net import build_websocket_connect_options, is_valid_ip, normalize_ip


class FakeWebSocketConnection:
    def __init__(self, frames):
        self._frames = iter(frames)
        self.closed = False
        self.pongs = []

    def recv_data(self, control_frame=True):
        return next(self._frames)

    def pong(self, payload):
        self.pongs.append(payload)

    def close(self):
        self.closed = True


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

    def test_provider_transport_defaults_to_websocket_for_ws_scheme(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "codex",
                "api": "wss://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("websocket", schema.transport)

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

    def test_provider_transport_allows_explicit_override(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "codex",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "transport": "websocket",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("websocket", schema.transport)

    def test_provider_transport_rejects_http_transport_with_ws_scheme(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "requires api to use http:// or https://"
        ):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "bad-provider",
                    "api": "wss://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "transport": "http",
                    "model_list": ["demo"],
                }
            )

    def test_provider_defaults_source_and_target_formats(self) -> None:
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
        self.assertEqual(("openai_chat",), schema.target_formats)
        runtime = RuntimeProviderSpec.from_schema(schema)
        self.assertEqual("openai_chat", runtime.source_format)
        self.assertEqual("openai_chat", runtime.primary_target_format)
        self.assertEqual(("openai_chat",), runtime.target_formats)

    def test_provider_schema_accepts_multiple_target_formats(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "api_key": "demo-key",
                "target_formats": ["openai_chat", "claude_chat"],
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("openai_chat", schema.target_format)
        self.assertEqual(("openai_chat", "claude_chat"), schema.target_formats)

    def test_provider_schema_rejects_conflicting_target_formats(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Provider target_formats contains mutually exclusive formats: openai_responses, codex",
        ):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "target_formats": ["openai_responses", "codex"],
                    "model_list": ["gpt-4.1"],
                }
            )

    def test_provider_schema_rejects_legacy_target_format_field(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "Unsupported provider field\\(s\\): target_format"
        ):
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
        with self.assertRaisesRegex(
            ValueError, "Unsupported provider field\\(s\\): format"
        ):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "demo-key",
                    "format": "openai_chat",
                    "model_list": ["gpt-4.1"],
                }
            )

        with self.assertRaisesRegex(
            ValueError, "Unsupported provider field\\(s\\): stream_format"
        ):
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
        self.assertEqual("ws_json", resolve_stream_format(None, "", "websocket"))
        self.assertEqual(
            "sse_json",
            resolve_stream_format(None, "text/event-stream; charset=utf-8", "http"),
        )
        self.assertEqual(
            "ndjson", resolve_stream_format(None, "application/x-ndjson", "http")
        )
        self.assertEqual(
            "nonstream", resolve_stream_format(None, "application/json", "http")
        )

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
        runtime_provider = manager.get_provider_for_model(
            "enabled-provider/gpt-4.1-mini"
        )
        self.assertIsNotNone(runtime_provider)
        assert runtime_provider is not None
        self.assertIsNone(runtime_provider.auth_group)
        self.assertTrue(
            any(
                "disabled-provider" in message
                and "skipped runtime registration" in message
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
                any(
                    "Purged legacy provider runtime state rows" in message
                    for _, message in logger.records
                )
            )
        finally:
            if db_path.exists():
                db_path.unlink()


class WebSocketProxyBridgeTests(unittest.TestCase):
    def test_normalize_websocket_message_preserves_raw_payload(self) -> None:
        chunk = normalize_websocket_message(b'{"id":"evt_1"}')

        self.assertEqual(b'{"id":"evt_1"}', chunk)

    def test_stream_response_preserves_websocket_message_boundaries(self) -> None:
        connection = FakeWebSocketConnection(
            [
                (ABNF.OPCODE_TEXT, b'{"id":"evt_1"}'),
                (ABNF.OPCODE_TEXT, b"data: [DONE]\n\n"),
                (ABNF.OPCODE_CLOSE, b""),
            ]
        )
        response = WebSocketUpstreamResponse(connection)

        chunks = list(response.iter_content())

        self.assertEqual(
            [
                b'{"id":"evt_1"}',
                b"data: [DONE]\n\n",
            ],
            chunks,
        )
        response.close()
        self.assertTrue(connection.closed)

    def test_collect_non_stream_websocket_body_uses_terminal_payload(self) -> None:
        connection = FakeWebSocketConnection(
            [
                (ABNF.OPCODE_TEXT, b'{"delta":"hello"}'),
                (
                    ABNF.OPCODE_TEXT,
                    b'{"choices":[],"usage":{"total_tokens":1},"model":"demo"}',
                ),
                (ABNF.OPCODE_CLOSE, b""),
            ]
        )

        body = collect_websocket_response_body(connection)

        self.assertEqual(
            b'{"choices":[],"usage":{"total_tokens":1},"model":"demo"}',
            body,
        )

    def test_collect_non_stream_websocket_body_extracts_sse_data(self) -> None:
        connection = FakeWebSocketConnection(
            [
                (ABNF.OPCODE_TEXT, b'data: {"id":"evt_1"}\n\ndata: [DONE]\n\n'),
                (ABNF.OPCODE_CLOSE, b""),
            ]
        )

        body = collect_websocket_response_body(connection)

        self.assertEqual(b'{"id":"evt_1"}', body)


class WebSocketConnectOptionsTests(unittest.TestCase):
    def test_normalize_ip_strips_ipv6_mapped_prefix(self) -> None:
        self.assertEqual("127.0.0.1", normalize_ip("::ffff:127.0.0.1"))

    def test_normalize_ip_preserves_unparseable_input(self) -> None:
        self.assertEqual("not-an-ip", normalize_ip(" not-an-ip "))

    def test_is_valid_ip_accepts_ipv6_mapped_ipv4(self) -> None:
        self.assertTrue(is_valid_ip("::ffff:127.0.0.1"))

    def test_build_websocket_connect_options_supports_http_proxy(self) -> None:
        options = build_websocket_connect_options(
            "http://user:pass@proxy.local:8080", False
        )

        self.assertEqual("proxy.local", options["http_proxy_host"])
        self.assertEqual(8080, options["http_proxy_port"])
        self.assertEqual(("user", "pass"), options["http_proxy_auth"])
        self.assertEqual(ssl.CERT_NONE, options["sslopt"]["cert_reqs"])


class ModelDiscoveryCandidateTests(unittest.TestCase):
    def test_model_discovery_maps_websocket_scheme_to_https(self) -> None:
        candidates = ModelDiscoveryService._build_model_endpoint_candidates(
            "wss://example.com/v1/chat/completions"
        )

        self.assertEqual(
            [
                "https://example.com/v1/models",
                "https://example.com/models",
            ],
            candidates,
        )

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
        candidates = ModelDiscoveryService._build_model_endpoint_candidates(
            "https://example.com/gateway/v1/responses"
        )

        self.assertEqual(
            [
                "https://example.com/gateway/v1/models",
                "https://example.com/gateway/models",
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

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None):
                captured["url"] = url
                captured["headers"] = dict(headers or {})
                captured["proxies"] = proxies
                captured["timeout"] = timeout
                captured["verify"] = verify
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

            def get(self, url, headers=None, proxies=None, timeout=None, verify=None):
                del url, proxies, timeout, verify
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
        template_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "providers.html"
        )
        html = template_path.read_text(encoding="utf-8")
        users_template_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "users.html"
        )
        users_html = users_template_path.read_text(encoding="utf-8")
        css_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "static"
            / "css"
            / "providers.css"
        )
        css = css_path.read_text(encoding="utf-8")

        self.assertRegex(
            html,
            r'/static/css/providers\.css\?v=\d{8}-\d+',
        )
        self.assertIn('id="providerTransport"', html)
        self.assertIn('id="providerSourceFormat"', html)
        self.assertIn('id="providerTargetFormat"', html)
        self.assertNotIn('data-multi-select-badge="多选"', html)
        self.assertIn(
            'data-multi-select-hint="可多选，点击已选项可取消；互斥项会自动替换"', html
        )
        self.assertIn('class="field-mode-badge">多选</span>', html)
        self.assertNotIn(
            'class="field-inline-note">可多选；点击已选项可取消，互斥项会自动替换。</div>',
            html,
        )
        self.assertIn('id="providerAuthMode"', html)
        self.assertIn('id="providerAuthGroup"', html)
        self.assertIn("providerTransportAutoSyncEnabled", html)
        self.assertIn("syncProviderTransportWithApi", html)
        self.assertIn(
            "document.getElementById('providerApi').addEventListener('input', handleProviderApiInput);",
            html,
        )
        self.assertIn(
            "document.getElementById('providerTransport').addEventListener('change', handleProviderTransportChange);",
            html,
        )
        self.assertIn('id="authGroupsContainer"', html)
        self.assertIn('id="authGroupModal"', html)
        self.assertIn('id="authEntryImportModal"', html)
        self.assertIn('id="authGroupRuntimeModal"', html)
        self.assertIn('id="authGroupDeletePopover"', html)
        self.assertIn(
            'id="providerModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"',
            html,
        )
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

        for value in ("openai_chat", "openai_responses", "claude_chat", "codex"):
            self.assertIn(f'value="{value}"', html)
        for removed_value in ("gemini_chat", "gemini_cli", "antigravity"):
            self.assertNotIn(f'value="{removed_value}"', html)

        self.assertIn('data-provider-help-topic="transport"', html)
        self.assertIn('data-provider-help-topic="source_format"', html)
        self.assertIn('data-provider-help-topic="target_format"', html)
        self.assertIn('data-provider-help-topic="auth_group_field"', html)
        self.assertIn('data-provider-help-topic="auth_groups_overview"', html)
        self.assertIn('data-provider-help-topic="auth_group_strategy"', html)
        self.assertIn('data-provider-help-topic="auth_entries_editor"', html)
        self.assertIn('data-provider-help-topic="fetch_models"', html)
        self.assertIn("所选 Auth Group 的第一个 entry", html)
        self.assertIn('id="providerHelpPopover"', html)
        self.assertIn("function toggleProviderHelp(", html)
        self.assertIn("function syncProviderHelpPopover()", html)
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
        self.assertIn("setupCustomSelect('providerTransport');", html)
        self.assertIn("setupCustomSelect('providerSourceFormat');", html)
        self.assertIn("setupCustomSelect('providerTargetFormat');", html)
        self.assertIn("setupCustomSelect('providerVerifySsl');", html)
        self.assertIn("function buildMultiSelectTriggerMarkup(", html)
        self.assertNotIn("custom-select-trigger-badge", html)
        self.assertNotIn("已选 ${selectedLabels.length} 项", html)
        self.assertIn("custom-select-option custom-select-option-multi", html)
        self.assertIn("custom-select-menu-hint", html)
        self.assertIn(
            "const defaultNewProviderTargetFormats = ['openai_chat', 'codex', 'claude_chat'];",
            html,
        )
        self.assertIn("const providerTargetFormatConflictGroups = [", html)
        self.assertIn("function setProviderTargetFormatValues(", html)
        self.assertIn("target_formats: normalizedTargetFormats,", html)
        self.assertNotIn('id="selectedProviderCount"', html)
        self.assertNotIn('id="enableSelectedProvidersBtn"', html)
        self.assertNotIn('id="disableSelectedProvidersBtn"', html)
        self.assertNotIn('id="deleteSelectedProvidersBtn"', html)
        self.assertNotIn('id="clearSelectedProvidersBtn"', html)
        self.assertIn("let providerOrderActionInFlight = false;", html)
        self.assertIn("function getProviderGroupNames(groupKey)", html)
        self.assertIn("function getSelectedProviderNamesForGroup(groupKey)", html)
        self.assertIn("function toggleProviderGroupSelection(groupKey, checked)", html)
        self.assertIn("function getProviderMoveState(name)", html)
        self.assertIn("function buildMovedProviderOrderNames(name, direction)", html)
        self.assertIn("function hasProviderMutationInFlight()", html)
        self.assertIn("function buildProviderTable(groupKey, title, providerList, emptyText)", html)
        self.assertIn("function saveProviderOrder(names)", html)
        self.assertIn("function moveProvider(name, direction)", html)
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
        self.assertIn('class="provider-order-column">顺序</th>', html)
        self.assertIn('class="provider-order-cell"', html)
        self.assertIn('data-provider-group-select-checkbox="${normalizedGroupKey}"', html)
        self.assertIn('id="${batchActionMeta.buttonId}"', html)
        self.assertIn("class=\"btn btn-toolbar-secondary provider-group-batch-btn\"", html)
        self.assertIn("onclick=\"runProviderBatchAction('${batchActionMeta.action}', '${normalizedGroupKey}')\"", html)
        self.assertIn("buttonId: 'disableEnabledProvidersBtn'", html)
        self.assertIn("buttonId: 'enableDisabledProvidersBtn'", html)
        self.assertIn("buttonLabel: '禁用'", html)
        self.assertIn("buttonLabel: '启用'", html)
        self.assertIn('class="provider-order-step provider-order-step-up"', html)
        self.assertIn('class="provider-order-step provider-order-step-down"', html)
        self.assertIn('class="provider-order-step-icon"', html)
        self.assertNotIn(">上移</button>", html)
        self.assertNotIn(">下移</button>", html)
        self.assertIn("已启用", html)
        self.assertIn("已禁用", html)
        self.assertIn('class="provider-order-stepper"', html)
        self.assertIn("providerActionLocked || !providerMoveState.canMoveUp", html)
        self.assertIn("providerActionLocked || !providerMoveState.canMoveDown", html)
        self.assertIn("if (!normalizedName || hasProviderMutationInFlight())", html)
        self.assertIn("groupLabel = groupKey === 'enabled'", html)
        self.assertIn('data-provider-row-checkbox="${encodedProviderName}"', html)
        self.assertIn('data-auth-group-delete-trigger="${encodedGroupName}"', html)
        self.assertNotIn('provider-chip ${isEnabled ? \'provider-chip-hook\' : \'provider-chip-muted\'}', html)
        self.assertIn(
            "class=\"btn-action ${isEnabled ? 'btn-delete' : 'btn-edit'}\"", html
        )
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
        self.assertIn(
            "onclick=\"toggleDeleteAuthEntryConfirm('${entryKey}', event)\"", html
        )
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
        self.assertIn(
            "authEntryYamlEditor.renderer.setShowInvisibles('tab space');", html
        )
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
        self.assertIn(".providers-page .providers-table th.provider-order-column,", css)
        self.assertIn(".providers-page .provider-order-actions {", css)
        self.assertIn(".providers-page .provider-order-stepper {", css)
        self.assertIn(".providers-page .provider-order-step {", css)
        self.assertIn(".providers-page .provider-list-table col.provider-select-col {", css)
        self.assertIn(".providers-page .drag-handle-button {", css)
        self.assertIn(".providers-page .providers-table tbody tr.is-drag-over-before td {", css)
        self.assertIn(".providers-page .providers-table tbody tr.is-drag-over-after td {", css)
        self.assertIn(".providers-page .btn-action:disabled {", css)
        self.assertIn(".providers-page .provider-order-step:disabled {", css)
        self.assertIn(':root[data-theme="dark"] .providers-page .provider-order-step:disabled {', css)
        self.assertIn(':root[data-theme="dark"] .providers-page .drag-handle-button {', css)
        self.assertIn(".providers-page .field-label-with-help {", css)
        self.assertIn(".providers-page .field-mode-badge {", css)
        self.assertNotIn(".providers-page .field-inline-note {", css)
        self.assertNotIn(".providers-page .custom-select-trigger-summary {", css)
        self.assertIn(
            ".providers-page .custom-select.is-multi .custom-select-trigger {", css
        )
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
        self.assertIn("function parseLocalDateTime(value)", users_html)
        self.assertNotIn("new Date(user.created_at)", users_html)
        self.assertIn("formatDateTime(user.created_at)", users_html)

    def test_provider_model_list_tidy_sorts_and_manual_cleanup_is_explicit(
        self,
    ) -> None:
        template_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "providers.html"
        )
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
  providerTargetFormatOptions: ["openai_chat", "openai_responses", "claude_chat", "codex"],
  providerTargetFormatConflictGroups: [["openai_responses", "codex"]],
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
      providerTransport: {{ value: "http" }},
      providerSourceFormat: {{ value: "openai_chat" }},
      providerTargetFormat: {{
        multiple: true,
        options: [
          {{ value: "openai_chat", textContent: "openai_chat", selected: true }},
          {{ value: "openai_responses", textContent: "openai_responses", selected: false }},
          {{ value: "claude_chat", textContent: "claude_chat", selected: false }},
          {{ value: "codex", textContent: "codex", selected: false }},
        ],
      }},
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
  beforeTargetFormats: collectedBefore.target_formats,
  beforeAuthGroup: collectedBefore.auth_group,
  beforeApiKey: collectedBefore.api_key,
  afterRows: sandbox.modelTestRows.map(row => row.model),
  afterModelList: collectedAfter.model_list,
  afterTargetFormats: collectedAfter.target_formats,
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
        self.assertEqual(["openai_chat"], payload["beforeTargetFormats"])
        self.assertEqual("shared-pool", payload["beforeAuthGroup"])
        self.assertEqual("", payload["beforeApiKey"])
        self.assertEqual(["Alpha", "Beta", "alpha", "beta"], payload["afterRows"])
        self.assertEqual("Alpha\nBeta\nalpha\nbeta", payload["afterModelList"])
        self.assertEqual(["openai_chat"], payload["afterTargetFormats"])
        self.assertEqual("shared-pool", payload["afterAuthGroup"])
        self.assertEqual("", payload["afterApiKey"])
        self.assertIn("4", payload["countText"])
        self.assertTrue(payload["message"])

    def test_fetch_models_button_supports_auth_group_mode(self) -> None:
        template_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "providers.html"
        )
        html = template_path.read_text(encoding="utf-8")

        script_start = html.index("function getEffectiveTransport")
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

    def test_provider_order_helpers_keep_enabled_group_before_disabled_group(
        self,
    ) -> None:
        template_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "providers.html"
        )
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
  ],
  providerBatchActionInFlight: false,
  providerOrderActionInFlight: false,
  togglingProviderNames: new Set(),
  deletingProviderName: null,
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
process.stdout.write(JSON.stringify({{
  moveStateFirst: sandbox.getProviderMoveState("enabled-a"),
  moveStateDisabled: sandbox.getProviderMoveState("disabled-a"),
  movedUp: sandbox.buildMovedProviderOrderNames("enabled-b", "up"),
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
            {"canMoveUp": False, "canMoveDown": True},
            payload["moveStateFirst"],
        )
        self.assertEqual(
            {"canMoveUp": False, "canMoveDown": False},
            payload["moveStateDisabled"],
        )
        self.assertEqual(
            ["enabled-b", "enabled-a", "disabled-a"],
            payload["movedUp"],
        )
        self.assertFalse(payload["mutationIdle"])


class FrontendMessageLocalizationTests(unittest.TestCase):
    def test_ui_message_script_contains_localized_error_formatter(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "static"
            / "js"
            / "ui-message.js"
        )
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
        base_page_html = (root / "templates" / "base_page.html").read_text(
            encoding="utf-8"
        )
        theme_js = (root / "static" / "js" / "theme.js").read_text(encoding="utf-8")
        base_admin_html = (root / "templates" / "base_admin.html").read_text(
            encoding="utf-8"
        )
        web_controller_py = (root.parent / "presentation" / "web_controller.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("/static/js/ui-message.js?v=20260319-1", login_html)
        self.assertIn("/static/js/ui-message.js?v=20260319-1", users_html)
        self.assertIn("/static/js/ui-message.js?v=20260319-1", index_html)
        self.assertIn("/static/css/admin-base.css?v=20260319-3", base_page_html)
        self.assertIn("/static/js/theme.js?v=20260319-1", base_page_html)
        self.assertIn("showActionError('登录'", login_html)
        self.assertIn("showActionError('创建用户'", users_html)
        self.assertIn("showActionError('更新用户'", users_html)
        self.assertIn("showActionError('删除用户'", users_html)
        self.assertNotIn("Toggle theme", theme_js)
        self.assertIn('href="/">Provider 管理</a>', base_admin_html)
        self.assertIn('href="/users">用户管理</a>', base_admin_html)
        self.assertIn('href="/statistics">统计概览</a>', base_admin_html)
        self.assertLess(
            base_admin_html.index('href="/">Provider 管理</a>'),
            base_admin_html.index('href="/users">用户管理</a>'),
        )
        self.assertLess(
            base_admin_html.index('href="/users">用户管理</a>'),
            base_admin_html.index('href="/statistics">统计概览</a>'),
        )
        self.assertIn('self._app.route("/")(auth(self.home))', web_controller_py)
        self.assertIn('self._app.route("/statistics")(auth(self.statistics_page))', web_controller_py)
        self.assertIn('def home(self) -> str:', web_controller_py)

    def test_ui_message_formatter_appends_upstream_original_error(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "static"
            / "js"
            / "ui-message.js"
        )
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
        admin_base_css = (root / "static" / "css" / "admin-base.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("/static/css/index.css?v=20260409-4", index_html)
        self.assertIn("dashboard-tabs-section", index_html)
        self.assertIn('id="dashboardTabBtn_stats"', index_html)
        self.assertIn('id="dashboardTabBtn_logs"', index_html)
        self.assertIn('id="username"', index_html)
        self.assertIn('id="requestModel"', index_html)
        self.assertIn('data-visible-chip-count="3"', index_html)
        self.assertIn("function getSelectedOptionValues(", index_html)
        self.assertIn("function buildMultiSelectTriggerMarkup(", index_html)
        self.assertIn("function buildDashboardSearchParams(", index_html)
        self.assertIn("function filterCustomSelectOptions(", index_html)
        self.assertIn("function selectFilteredCustomSelectOptions(", index_html)
        self.assertIn('custom-select-search-input', index_html)
        self.assertIn('custom-select-menu-action', index_html)
        self.assertIn('全选过滤结果', index_html)
        self.assertIn('custom-select-menu-action-box', index_html)
        self.assertIn("function switchDashboardTab(", index_html)
        self.assertIn("function loadActiveDashboardTabData()", index_html)
        self.assertIn(
            "fetch(`/api/statistics?${params}`, { cache: 'no-store' })", index_html
        )
        self.assertIn(
            "fetch(`/api/request-logs?${params}`, { cache: 'no-store' })", index_html
        )
        self.assertIn("function parseLocalDateTime(value)", index_html)
        self.assertNotIn("new Date(log.start_time)", index_html)
        self.assertNotIn("new Date(log.end_time)", index_html)
        self.assertIn(
            "calculateDurationSeconds(log.start_time, log.end_time)", index_html
        )
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
        self.assertIn(".dashboard-page .custom-select-menu-action.is-checked .custom-select-menu-action-box,", index_css)
        self.assertIn("--nav-tab-hover-bg:", admin_base_css)

    def test_provider_template_keeps_model_row_change_from_rebuilding_table(self) -> None:
        providers_html = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "providers.html"
        ).read_text(encoding="utf-8")

        self.assertIn("function refreshModelTestRowDisplay(rowId, inputElement)", providers_html)
        self.assertIn("function updateModelTestTableSummaryAndControls()", providers_html)
        self.assertIn("onchange=\"handleModelTestRowChange(${row.rowId}, this.value, this)\"", providers_html)
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
        template_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "templates"
            / "providers.html"
        )
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
