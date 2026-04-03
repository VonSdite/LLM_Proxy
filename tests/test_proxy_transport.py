import json
import ssl
import subprocess
import sys
import unittest
from pathlib import Path

from flask import Flask
from websocket import ABNF

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.provider_manager import ProviderManager
from src.config.provider_config import ProviderConfigSchema, RuntimeProviderSpec
from src.external.stream_probe import probe_stream_response
from src.external.upstream_websocket import (
    WebSocketUpstreamResponse,
    collect_websocket_response_body,
    normalize_websocket_message,
)
from src.proxy_core import resolve_stream_format
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


class FakeAuthGroupManager:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []

    def register_legacy_provider_group(self, provider_name: str, api_key: str) -> str:
        self.registered.append((provider_name, api_key))
        return f"__legacy_provider__/{provider_name}"


class ProviderTransportTests(unittest.TestCase):
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
        with self.assertRaisesRegex(ValueError, "requires api to use http:// or https://"):
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
        runtime = RuntimeProviderSpec.from_schema(schema)
        self.assertEqual("openai_chat", runtime.source_format)
        self.assertEqual("openai_chat", runtime.target_format)

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
        self.assertEqual("ws_json", resolve_stream_format(None, "", "websocket"))
        self.assertEqual("sse_json", resolve_stream_format(None, "text/event-stream; charset=utf-8", "http"))
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
                        b"data: {\"ok\":true}\n\n",
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
            [b"data: {\"ok\":true}\n\n", b"data: [DONE]\n\n"],
            list(response.iter_content()),
        )


class ProviderManagerEnabledTests(unittest.TestCase):
    def test_disabled_provider_is_not_registered_at_runtime(self) -> None:
        logger = FakeLogger()
        auth_group_manager = FakeAuthGroupManager()
        ctx = AppContext(
            logger=logger,
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        manager = ProviderManager(ctx, auth_group_manager)

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
        self.assertIsNotNone(manager.get_provider_view("enabled-provider"))
        self.assertEqual([("enabled-provider", "enabled-key")], auth_group_manager.registered)
        self.assertTrue(
            any("disabled-provider" in message and "skipped runtime registration" in message for _, message in logger.records)
        )


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
    def test_provider_template_contains_clean_provider_fields_and_help(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "providers.html"
        html = template_path.read_text(encoding="utf-8")
        users_template_path = Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "users.html"
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

        self.assertIn('id="providerTransport"', html)
        self.assertIn('id="providerSourceFormat"', html)
        self.assertIn('id="providerTargetFormat"', html)
        self.assertIn('id="providerAuthMode"', html)
        self.assertIn('id="providerAuthGroup"', html)
        self.assertIn('providerTransportAutoSyncEnabled', html)
        self.assertIn('syncProviderTransportWithApi', html)
        self.assertIn("document.getElementById('providerApi').addEventListener('input', handleProviderApiInput);", html)
        self.assertIn("document.getElementById('providerTransport').addEventListener('change', handleProviderTransportChange);", html)
        self.assertIn('id="authGroupsContainer"', html)
        self.assertIn('id="authGroupModal"', html)
        self.assertIn('id="authEntryImportModal"', html)
        self.assertIn('id="authGroupRuntimeModal"', html)
        self.assertIn('id="providerModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"', html)
        self.assertIn('id="authGroupModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"', html)
        self.assertIn('id="authEntryImportModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false"', html)
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
        self.assertIn("/api/auth-groups", html)
        self.assertIn("/api/auth-groups/import-entries", html)
        self.assertIn("'disable'", html)
        self.assertIn("'enable'", html)
        self.assertIn("'reset'", html)
        self.assertIn("setupCustomSelect('providerAuthMode');", html)
        self.assertIn("setupCustomSelect('providerAuthGroup');", html)
        self.assertIn("renderCustomSelectOptions('providerAuthGroup');", html)
        self.assertIn("setupCustomSelect('authGroupStrategy');", html)
        self.assertIn("setupCustomSelect('providerTransport');", html)
        self.assertIn("setupCustomSelect('providerSourceFormat');", html)
        self.assertIn("setupCustomSelect('providerTargetFormat');", html)
        self.assertIn("setupCustomSelect('providerVerifySsl');", html)
        self.assertIn("id=\"selectedProviderCount\"", html)
        self.assertIn("id=\"enableSelectedProvidersBtn\"", html)
        self.assertIn("id=\"disableSelectedProvidersBtn\"", html)
        self.assertNotIn("id=\"deleteSelectedProvidersBtn\"", html)
        self.assertNotIn("id=\"clearSelectedProvidersBtn\"", html)
        self.assertIn("function updateProviderSelectionUi()", html)
        self.assertIn("function toggleProviderSelection(name, checked)", html)
        self.assertIn("function toggleAllProvidersSelection(checked)", html)
        self.assertIn("function clearSelectedProviders()", html)
        self.assertIn("function setProviderEnabled(name, enabled)", html)
        self.assertIn("function runProviderBatchAction(action)", html)
        self.assertIn("/api/providers/batch", html)
        self.assertIn("/api/providers/${encodeURIComponent(normalizedName)}/${enabled ? 'enable' : 'disable'}", html)
        self.assertIn('id="providerSelectAllCheckbox"', html)
        self.assertIn('data-provider-row-checkbox="${encodedProviderName}"', html)
        self.assertIn("class=\"btn-action ${isEnabled ? 'btn-delete' : 'btn-edit'}\"", html)
        self.assertNotIn("window.confirm(`确认批量删除已选中的 ${selectedNames.length} 个 Provider？`)", html)
        self.assertNotIn("setupCustomSelect('providerFormat');", html)
        self.assertNotIn("setupCustomSelect('providerStreamFormat');", html)
        self.assertNotIn('id="providerFormatMatrix"', html)
        self.assertNotIn('49 pairs', html)
        self.assertNotIn('data-bs-toggle="tooltip"', html)

        self.assertIn('id="fetchModelSelectAllCheckbox"', html)
        self.assertIn('toggleFilteredFetchedModels(this.checked)', html)
        self.assertIn('class="provider-model-cell"', html)
        self.assertIn('class="provider-meta-line"', html)
        self.assertIn('class="providers-table-shell auth-groups-table-shell"', html)
        self.assertIn('<th>Entry 数</th>', html)
        self.assertIn('placeholder="例如 openai-shared，供 Provider 绑定引用"', html)
        self.assertIn('placeholder="例如 60，表示该组默认遇到 429 冷却 60 秒"', html)
        self.assertIn('YAML 编辑', html)
        self.assertIn('id="authEntryImportYaml"', html)
        self.assertIn('这里编辑的是当前 Auth Entries 的完整 YAML', html)
        self.assertIn('插入 Entry 模板', html)
        self.assertIn('必填：Entry 唯一 ID', html)
        self.assertIn('可选：每分钟请求数上限', html)
        self.assertIn('新增 Entry', html)
        self.assertNotIn("新 Entry", html)
        self.assertIn('<span>Header 数</span>', html)
        self.assertIn('<span>限制概览</span>', html)
        self.assertIn('class="auth-entry-table-toggle-column"', html)
        self.assertIn("function toggleAuthEntryCard(", html)
        self.assertIn('id="authEntryDeletePopover"', html)
        self.assertIn("function removeAuthEntryCard(source) {", html)
        self.assertIn("function toggleDeleteAuthEntryConfirm(entryKey, event) {", html)
        self.assertIn("function confirmDeleteAuthEntry(event) {", html)
        self.assertIn("function closeDeleteAuthEntryConfirm(shouldSync = true) {", html)
        self.assertIn("function syncDeleteAuthEntryPopover() {", html)
        self.assertIn('data-auth-entry-delete-trigger="${entryKey}"', html)
        self.assertIn('onclick="toggleDeleteAuthEntryConfirm(\'${entryKey}\', event)"', html)
        self.assertIn('class="btn-action btn-edit auth-entry-toggle-btn"', html)
        self.assertIn("function handleAuthEntrySummaryKeydown(", html)
        self.assertIn('onclick="toggleAuthEntryCard(this)"', html)
        self.assertNotIn("expandAuthEntryCard(nextCard);", html)
        self.assertNotIn('id="authEntryImportTemplate"', html)
        self.assertNotIn('复制模板', html)
        self.assertNotIn('填入模板', html)
        self.assertIn("function buildAuthEntriesYamlText(", html)
        self.assertIn("function insertAuthEntryYamlTemplate(", html)
        self.assertIn("function getSingleAuthEntryYamlTemplate(", html)
        self.assertIn("function saveAuthEntriesFromYaml(", html)
        self.assertIn("function replaceAuthEntryCards(entries, options = {}) {", html)
        self.assertIn("const { expandFirst = false } = options || {};", html)
        self.assertIn("entries.forEach((entry, index) => addAuthEntryCard(entry, { expand: expandFirst && index === 0 }));", html)
        self.assertIn("replaceAuthEntryCards(entries);", html)
        self.assertIn('/static/vendor/ace/ace.js?v=1.43.6', html)
        self.assertIn('/static/vendor/ace/mode-yaml.js?v=1.43.6', html)
        self.assertIn('/static/vendor/ace/theme-textmate.js?v=1.43.6', html)
        self.assertIn('/static/vendor/ace/theme-tomorrow_night.js?v=1.43.6', html)
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
        self.assertNotIn('key-a', html)
        self.assertIn('id="authEntryErrorModal"', html)
        self.assertIn("showActionError('保存 Provider'", html)
        self.assertIn("showActionError('删除 Provider'", html)
        self.assertIn("showActionError('拉取模型'", html)

        self.assertIn(".providers-page .provider-help-popover {", css)
        self.assertIn(".providers-page .provider-batch-summary {", css)
        self.assertNotIn(".providers-page .provider-batch-delete-modal-dialog {", css)
        self.assertIn(".providers-page .provider-table-checkbox {", css)
        self.assertIn(".providers-page .field-label-with-help {", css)
        self.assertIn(".providers-page .entry-import-editor {", css)
        self.assertIn(".providers-page .entry-import-editor .ace_gutter-cell,", css)
        self.assertIn(".providers-page .entry-import-editor .ace_invisible.ace_invisible_eol {", css)
        self.assertIn(".providers-page .entry-import-editor-shell.is-editor-ready .entry-import-textarea {", css)
        self.assertNotIn('.providers-page .compatibility-card {', css)

        self.assertNotIn('id="chatWhitelistToggle"', html)
        self.assertIn('id="chatWhitelistToggle"', users_html)
        self.assertIn("function parseLocalDateTime(value)", users_html)
        self.assertNotIn("new Date(user.created_at)", users_html)
        self.assertIn("formatDateTime(user.created_at)", users_html)

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
      providerAuthMode: {{ value: "auth_group" }},
      providerAuthGroup: {{ value: " shared-pool " }},
      providerTransport: {{ value: "http" }},
      providerSourceFormat: {{ value: "openai_chat" }},
      providerTargetFormat: {{ value: "openai_chat" }},
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
  beforeAuthGroup: collectedBefore.auth_group,
  beforeApiKey: collectedBefore.api_key,
  afterTextarea: sandbox.document.elements.providerModelList.value,
  afterModelList: collectedAfter.model_list,
  afterAuthGroup: collectedAfter.auth_group,
  afterApiKey: collectedAfter.api_key,
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
        self.assertEqual("shared-pool", payload["beforeAuthGroup"])
        self.assertEqual("", payload["beforeApiKey"])
        self.assertEqual("Alpha\nBeta\nalpha\nbeta", payload["afterTextarea"])
        self.assertEqual("Alpha\nBeta\nalpha\nbeta", payload["afterModelList"])
        self.assertEqual("shared-pool", payload["afterAuthGroup"])
        self.assertEqual("", payload["afterApiKey"])
        self.assertIn("4", payload["countText"])
        self.assertTrue(payload["message"])


class FrontendMessageLocalizationTests(unittest.TestCase):
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

        self.assertIn('/static/js/ui-message.js?v=20260319-1', login_html)
        self.assertIn('/static/js/ui-message.js?v=20260319-1', users_html)
        self.assertIn('/static/js/ui-message.js?v=20260319-1', index_html)
        self.assertIn('/static/css/admin-base.css?v=20260319-3', base_page_html)
        self.assertIn('/static/js/theme.js?v=20260319-1', base_page_html)
        self.assertIn("showActionError('登录'", login_html)
        self.assertIn("showActionError('创建用户'", users_html)
        self.assertIn("showActionError('更新用户'", users_html)
        self.assertIn("showActionError('删除用户'", users_html)
        self.assertNotIn("Toggle theme", theme_js)

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

        self.assertIn('/static/css/index.css?v=20260330-1', index_html)
        self.assertIn('dashboard-tabs-section', index_html)
        self.assertIn('id="dashboardTabBtn_stats"', index_html)
        self.assertIn('id="dashboardTabBtn_logs"', index_html)
        self.assertIn("function switchDashboardTab(", index_html)
        self.assertIn("function loadActiveDashboardTabData()", index_html)
        self.assertIn("fetch(`/api/statistics?${params}`, { cache: 'no-store' })", index_html)
        self.assertIn("fetch(`/api/request-logs?${params}`, { cache: 'no-store' })", index_html)
        self.assertIn("function parseLocalDateTime(value)", index_html)
        self.assertNotIn("new Date(log.start_time)", index_html)
        self.assertNotIn("new Date(log.end_time)", index_html)
        self.assertIn("calculateDurationSeconds(log.start_time, log.end_time)", index_html)
        self.assertIn("--dashboard-control-height: 40px;", index_css)
        self.assertIn(".dashboard-page .custom-select-trigger {", index_css)
        self.assertIn("--nav-tab-hover-bg:", admin_base_css)


if __name__ == "__main__":
    unittest.main()
