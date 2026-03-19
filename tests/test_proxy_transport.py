import ssl
import sys
import unittest
from pathlib import Path

from websocket import ABNF

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.provider_config import ProviderConfigSchema
from src.external.upstream_websocket import (
    WebSocketUpstreamResponse,
    collect_websocket_response_body,
    normalize_websocket_message,
)
from src.services.model_discovery_service import ModelDiscoveryService
from src.utils.net import build_websocket_connect_options


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


class ProviderTransportTests(unittest.TestCase):
    def test_provider_transport_defaults_to_websocket_for_ws_scheme(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "codex",
                "api": "wss://example.com/v1/chat/completions",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("websocket", schema.transport)

    def test_provider_transport_allows_explicit_override(self) -> None:
        schema = ProviderConfigSchema.from_mapping(
            {
                "name": "codex",
                "api": "https://example.com/v1/chat/completions",
                "transport": "websocket",
                "model_list": ["gpt-4.1"],
            }
        )

        self.assertEqual("websocket", schema.transport)

    def test_provider_transport_rejects_http_transport_with_ws_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires api to use http:// or https://"):
            ProviderConfigSchema.from_mapping(
                {
                    "name": "bad-provider",
                    "api": "wss://example.com/v1/chat/completions",
                    "transport": "http",
                    "model_list": ["demo"],
                }
            )


class WebSocketProxyBridgeTests(unittest.TestCase):
    def test_normalize_websocket_message_wraps_json_as_sse(self) -> None:
        chunk = normalize_websocket_message(b'{"id":"evt_1"}')

        self.assertEqual(b'data: {"id":"evt_1"}\n\n', chunk)

    def test_stream_response_converts_websocket_messages_to_sse(self) -> None:
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
                b'data: {"id":"evt_1"}\n\n',
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
                (ABNF.OPCODE_TEXT, b"data: {\"id\":\"evt_1\"}\n\ndata: [DONE]\n\n"),
                (ABNF.OPCODE_CLOSE, b""),
            ]
        )

        body = collect_websocket_response_body(connection)

        self.assertEqual(b'{"id":"evt_1"}', body)


class WebSocketConnectOptionsTests(unittest.TestCase):
    def test_build_websocket_connect_options_supports_http_proxy(self) -> None:
        options = build_websocket_connect_options("http://user:pass@proxy.local:8080", False)

        self.assertEqual("proxy.local", options["http_proxy_host"])
        self.assertEqual(8080, options["http_proxy_port"])
        self.assertEqual(("user", "pass"), options["http_proxy_auth"])
        self.assertEqual(ssl.CERT_NONE, options["sslopt"]["cert_reqs"])


class ModelDiscoveryCandidateTests(unittest.TestCase):
    def test_model_discovery_maps_websocket_scheme_to_https(self) -> None:
        candidates = ModelDiscoveryService._build_model_endpoint_candidates(
            "wss://example.com/v1/chat/completions"
        )

        self.assertIn("https://example.com/v1/models", candidates)
        self.assertIn("https://example.com/models", candidates)
        self.assertNotIn("wss://example.com/v1/models", candidates)


class ProviderTemplateTransportTests(unittest.TestCase):
    def test_provider_template_contains_transport_field(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")

        self.assertIn('id="providerTransport"', html)
        self.assertIn('class="field-help-button"', html)
        self.assertIn('data-bs-toggle="tooltip"', html)
        self.assertIn('id="fetchModelSelectAllCheckbox"', html)
        self.assertIn('toggleFilteredFetchedModels(this.checked)', html)
        self.assertNotIn('selectAllFetchedModels()', html)
        self.assertNotIn('clearFetchedModelsSelection()', html)
        self.assertNotIn("自动（按 API 推断）", html)
        self.assertIn("证书校验（HTTPS/WSS）", html)
        self.assertNotIn('<option value="">-</option>', html)
        self.assertIn("WebSocket", html)
        self.assertIn("provider-transport-badge", html)


if __name__ == "__main__":
    unittest.main()
