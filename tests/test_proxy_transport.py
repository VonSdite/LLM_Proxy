import json
import ssl
import subprocess
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
        css_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "presentation"
            / "static"
            / "css"
            / "providers.css"
        )
        css = css_path.read_text(encoding="utf-8")

        self.assertIn('id="providerTransport"', html)
        self.assertIn('/static/css/providers.css?v=20260319-6', html)
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
        self.assertIn('class="provider-model-cell"', html)
        self.assertIn(".providers-page .providers-table td.provider-name-cell {", css)
        self.assertIn(".providers-page .providers-table td.provider-api-cell {", css)
        self.assertIn(".providers-page .providers-table td.provider-model-cell {", css)
        self.assertIn("vertical-align: middle;", css)
        self.assertIn("showActionError('保存 Provider'", html)
        self.assertIn("showActionError('删除 Provider'", html)
        self.assertIn("showActionError('拉取模型'", html)


    def test_provider_model_list_tidy_sorts_and_manual_cleanup_is_explicit(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")

        self.assertNotIn("addEventListener('blur'", html)
        self.assertNotIn("applyNormalizedModelListValue();", html)

        script_start = html.index("function normalizeModelListItems")
        script_end = html.index("function fillForm")
        script = html[script_start:script_end]

        node_script = f"""
const vm = require("vm");
const sandbox = {{
  console,
  messages: [],
  showMessage(message, level) {{
    sandbox.messages.push({{ message, level }});
  }},
  document: {{
    elements: {{
      providerModelList: {{ value: " beta \\nAlpha\\nalpha\\nBeta\\nbeta\\n" }},
      providerModelCount: {{ textContent: "" }},
      providerName: {{ value: " demo " }},
      providerApi: {{ value: " https://example.com/v1/chat/completions " }},
      providerTransport: {{ value: "http" }},
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

const collectedBefore = sandbox.collectFormData();
sandbox.tidyModelList();
const collectedAfter = sandbox.collectFormData();

process.stdout.write(JSON.stringify({{
  beforeModelList: collectedBefore.model_list,
  afterTextarea: sandbox.document.elements.providerModelList.value,
  afterModelList: collectedAfter.model_list,
  countText: sandbox.document.elements.providerModelCount.textContent,
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
        self.assertEqual("Alpha\nBeta\nalpha\nbeta", payload["afterTextarea"])
        self.assertEqual("Alpha\nBeta\nalpha\nbeta", payload["afterModelList"])
        self.assertIn("4", payload["countText"])
        self.assertIn("整理并排序", payload["message"])


class FrontendMessageLocalizationTests(unittest.TestCase):
    def test_ui_message_script_contains_localized_error_formatter(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "static" / "js" / "ui-message.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("function formatActionErrorMessage(", script)
        self.assertIn("window.showActionError = showActionError;", script)
        self.assertIn("invalid username or password", script)
        self.assertIn("用户名或密码错误", script)
        self.assertIn("上游接口鉴权失败（401）", script)
        self.assertNotIn("failed to fetch models", script)
        self.assertNotIn("whitelist control is disabled", script)
        self.assertNotIn("failed to toggle user status", script)

    def test_templates_use_versioned_scripts_and_localized_titles(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "presentation"
        login_html = (root / "templates" / "login.html").read_text(encoding="utf-8")
        users_html = (root / "templates" / "users.html").read_text(encoding="utf-8")
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        base_page_html = (root / "templates" / "base_page.html").read_text(encoding="utf-8")
        base_admin_html = (root / "templates" / "base_admin.html").read_text(encoding="utf-8")
        theme_js = (root / "static" / "js" / "theme.js").read_text(encoding="utf-8")

        self.assertIn('/static/js/ui-message.js?v=20260319-1', login_html)
        self.assertIn('/static/js/ui-message.js?v=20260319-1', users_html)
        self.assertIn('/static/js/ui-message.js?v=20260319-1', index_html)
        self.assertIn('/static/css/admin-base.css?v=20260319-3', base_page_html)
        self.assertIn('/static/js/theme.js?v=20260319-1', base_page_html)
        self.assertIn('aria-label="切换主题"', base_admin_html)
        self.assertIn('title="切换主题"', base_admin_html)
        self.assertIn("showActionError('登录'", login_html)
        self.assertIn("showActionError('创建用户'", users_html)
        self.assertIn("showActionError('更新用户'", users_html)
        self.assertIn("showActionError('删除用户'", users_html)
        self.assertIn("切换到浅色主题", theme_js)
        self.assertIn("切换到深色主题", theme_js)
        self.assertNotIn("Toggle theme", base_admin_html)
        self.assertNotIn("Switch to light theme", theme_js)
        self.assertNotIn("Switch to dark theme", theme_js)

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
  "拉取模型",
  "https://example.com/v1/models returned 401",
  {{ fallback: "拉取模型失败" }}
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

        self.assertIn("上游接口鉴权失败（401）", stdout)
        self.assertIn("原始信息：https://example.com/v1/models returned 401", stdout)


class DashboardTemplateTests(unittest.TestCase):
    def test_index_template_uses_lazy_loaded_tabs(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "presentation"
        index_html = (
            root
            / "templates"
            / "index.html"
        ).read_text(encoding="utf-8")
        index_css = (root / "static" / "css" / "index.css").read_text(encoding="utf-8")
        admin_base_css = (root / "static" / "css" / "admin-base.css").read_text(encoding="utf-8")

        self.assertIn('/static/css/index.css?v=20260319-4', index_html)
        self.assertIn('dashboard-tabs-section', index_html)
        self.assertIn('id="dashboardTabBtn_stats"', index_html)
        self.assertIn('id="dashboardTabBtn_logs"', index_html)
        self.assertIn("function switchDashboardTab(", index_html)
        self.assertIn("function loadActiveDashboardTabData()", index_html)
        self.assertIn("switchDashboardTab(activeDashboardTab);", index_html)
        self.assertIn("if (!['stats', 'logs'].includes(tabName)) {", index_html)
        self.assertIn("fetch(`/api/statistics?${params}`, { cache: 'no-store' })", index_html)
        self.assertIn("fetch(`/api/request-logs?${params}`, { cache: 'no-store' })", index_html)
        self.assertNotIn("dashboardTabDirtyState", index_html)
        self.assertNotIn("markDashboardTabsDirty();", index_html)
        self.assertIn("document.querySelector('#dashboardTabPanel_logs table')", index_html)
        self.assertIn("--dashboard-control-height: 40px;", index_css)
        self.assertIn(".dashboard-page .custom-select-trigger {", index_css)
        self.assertIn("height: var(--dashboard-control-height);", index_css)
        self.assertIn("--nav-tab-hover-bg:", admin_base_css)
        self.assertIn(".app-page .header-nav-link:hover {", admin_base_css)
        self.assertIn("background: var(--nav-tab-hover-bg);", admin_base_css)
        self.assertIn("border-color: var(--nav-tab-hover-border);", index_css)


if __name__ == "__main__":
    unittest.main()
