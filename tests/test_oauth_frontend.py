from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


class OAuthFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.html = (self.root / "src/presentation/templates/oauth.html").read_text(encoding="utf-8")

    def _function(self, name: str, next_name: str) -> str:
        start = self.html.index(name)
        return self.html[start : self.html.index(next_name, start)]

    def _run_node(self, script: str) -> dict[str, Any]:
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        return json.loads(completed.stdout)

    def test_reset_card_expiry_waits_in_safe_segments_and_redraws_only_at_expiry(self) -> None:
        script = self._function("function parseCodexQuotaTimestamp", "function getCodexQuotaResetTimes")
        script += self._function("function scheduleCodexResetCardExpiryRedraw", "function formatCodexResetCardExpiry")
        result = self._run_node(f"""
const vm = require("vm");
let now = Date.parse("2026-10-08T00:00:00Z");
const expiry = now + 30 * 86400000;
const delays = [];
let callback;
let redraws = 0;
const sandbox = {{
  Date: {{ now: () => now, parse: Date.parse }},
  CODEX_RESET_CARD_TIMER_MAX_DELAY_MS: 2147483647,
  codexResetCardExpiryTimer: null,
  codexAuthState: {{ resetCardsByFile: {{ demo: [{{ expires_at: expiry }}] }} }},
  window: {{
    setTimeout: (fn, delay) => {{ callback = fn; delays.push(delay); return delays.length; }},
    clearTimeout: () => {{}},
  }},
  renderCodexAuthFiles: () => redraws++,
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
sandbox.scheduleCodexResetCardExpiryRedraw();
now += delays[0];
callback();
const redrawsBeforeExpiry = redraws;
now = expiry + 100;
callback();
const redrawsAtExpiry = redraws;
sandbox.scheduleCodexResetCardExpiryRedraw();
const timersAfterExpiry = delays.length;
sandbox.codexAuthState.resetCardsByFile.demo = [{{ expires_at: now + 60000 }}, {{ expires_at: expiry }}];
sandbox.scheduleCodexResetCardExpiryRedraw();
process.stdout.write(JSON.stringify({{ delays, redrawsBeforeExpiry, redrawsAtExpiry, timersAfterExpiry }}));
""")
        self.assertEqual(2_147_483_647, result["delays"][0])
        self.assertTrue(all(100 <= delay <= 2_147_483_647 for delay in result["delays"]))
        self.assertEqual(0, result["redrawsBeforeExpiry"])
        self.assertEqual(1, result["redrawsAtExpiry"])
        self.assertEqual(2, result["timersAfterExpiry"])
        self.assertEqual(60_100, result["delays"][-1])

    def test_filter_redraw_preserves_buttons_and_updates_counts_and_selection(self) -> None:
        script = self._function("function renderCodexAuthFileFilters", "function getCodexAuthFileFilterCounts")
        result = self._run_node(f"""
const vm = require("vm");
const buttons = new Map();
let replacements = 0;
const container = {{
  set innerHTML(value) {{
    replacements++;
    buttons.clear();
    for (const [, key] of value.matchAll(/data-codex-auth-filter="([^"]+)"/g)) {{
      const button = {{ count: {{}}, active: false, attributes: {{}} }};
      button.classList = {{ toggle: (_, active) => button.active = active }};
      button.setAttribute = (key, value) => button.attributes[key] = value;
      button.querySelector = () => button.count;
      buttons.set(key, button);
    }}
  }},
  querySelector(selector) {{
    if (selector === ".oauth-auth-file-filter-pill") return buttons.get("all");
    return buttons.get(selector.match(/data-codex-auth-filter="([^"]+)"/)[1]);
  }},
}};
const sandbox = {{
  document: {{ getElementById: () => container }},
  codexAuthState: {{ authFileFilter: "all" }},
  normalizeCodexAuthFileFilter: value => value,
  getCodexAuthFileFilterCounts: files => ({{ all: files.length, available: files.length, disabled: 0,
    auth_failed: 0, quota_exhausted: 0, other_unavailable: 0 }}),
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
sandbox.renderCodexAuthFileFilters([{{}}]);
const firstButton = buttons.get("available");
sandbox.codexAuthState.authFileFilter = "available";
sandbox.renderCodexAuthFileFilters([{{}}, {{}}]);
process.stdout.write(JSON.stringify({{
  replacements, sameButton: firstButton === buttons.get("available"),
  count: firstButton.count.textContent, active: firstButton.active,
  pressed: firstButton.attributes["aria-pressed"], allActive: buttons.get("all").active,
}}));
""")
        self.assertEqual(1, result["replacements"])
        self.assertTrue(result["sameButton"])
        self.assertEqual("2", result["count"])
        self.assertTrue(result["active"])
        self.assertEqual("true", result["pressed"])
        self.assertFalse(result["allActive"])

    def test_read_timeout_covers_fetch_and_response_body_and_clears_timer(self) -> None:
        script = self._function("async function fetchCodexAuthFileJson", "async function loadCodexAuthFiles")
        result = self._run_node(f"""
const vm = require("vm");
async function probe(mode) {{
  let clearCount = 0;
  let aborted = false;
  const sandbox = {{
    AbortController, CODEX_AUTH_FILE_REQUEST_TIMEOUT_MS: 5,
    window: {{ setTimeout, clearTimeout: timer => {{ clearCount++; clearTimeout(timer); }} }},
    fetch: async (_, {{ signal }}) => {{
      const pending = () => new Promise((_, reject) => signal.addEventListener("abort", () => {{
        aborted = true;
        reject(new Error("aborted"));
      }}, {{ once: true }}));
      if (mode === "fetch") return pending();
      return {{ ok: true, json: () => mode === "body" ? pending() : Promise.resolve({{ status: "ok" }}) }};
    }},
  }};
  vm.createContext(sandbox);
  vm.runInContext({json.dumps(script)}, sandbox);
  try {{
    const result = await sandbox.fetchCodexAuthFileJson("/quota");
    return {{ status: result.result.status, clearCount, aborted }};
  }} catch (error) {{
    return {{ error: error.message, clearCount, aborted }};
  }}
}}
(async () => {{
  process.stdout.write(JSON.stringify({{
    fetch: await probe("fetch"), body: await probe("body"), success: await probe("success"),
  }}));
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
""")
        for scenario in ("fetch", "body"):
            self.assertIn("请求超时", result[scenario]["error"])
            self.assertTrue(result[scenario]["aborted"])
            self.assertEqual(1, result[scenario]["clearCount"])
        self.assertEqual({"status": "ok", "clearCount": 1, "aborted": False}, result["success"])

    def test_quota_and_auth_file_list_timeouts_release_loading_states(self) -> None:
        script = self._function("async function fetchCodexAuthFileJson", "function isCodexAuthFileSyncBusy")
        script += self._function("async function refreshCodexResetCardsByName", "function selectCodexResetCard")
        script += self._function("async function refreshCodexQuotaByName", "function replaceCodexAuthFileInState")
        script += self._function("async function refreshSelectedCodexQuotas", "function toggleCodexBatchDeleteConfirm")
        result = self._run_node(f"""
const vm = require("vm");
let requestCount = 0;
const sandbox = {{
  AbortController, CODEX_AUTH_FILE_REQUEST_TIMEOUT_MS: 5,
  window: {{ setTimeout, clearTimeout }},
  console: {{ error: () => {{}} }},
  document: {{ getElementById: () => null }},
  codexAuthState: {{ authFilesLoading: false, quotaLoadingByFile: {{}}, resetCardLoadingByFile: {{}},
    quotaByFile: {{}}, resetCardErrorByFile: {{}}, batchQuotaRefreshing: false }},
  renderCodexAuthFiles: () => {{}}, updateCodexAuthFileQuotaState: () => {{}},
  getCodexSelectedAuthFileNamesForCurrentFilter: () => ["demo.json"], showMessage: () => {{}},
  fetch: (_, {{ signal }}) => new Promise((_, reject) => {{
    requestCount++;
    signal.addEventListener("abort", () => reject(new Error("aborted")), {{ once: true }});
  }}),
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
(async () => {{
  const quotaOk = await sandbox.refreshCodexQuotaByName("demo.json", {{ silent: true, refreshResetCards: true }});
  await sandbox.refreshSelectedCodexQuotas();
  const listOk = await sandbox.loadCodexAuthFiles({{ showLoading: false }});
  process.stdout.write(JSON.stringify({{
    quotaOk, listOk, requestCount, state: sandbox.codexAuthState,
  }}));
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
""")
        self.assertFalse(result["quotaOk"])
        self.assertFalse(result["listOk"])
        self.assertEqual(5, result["requestCount"])
        self.assertFalse(result["state"]["authFilesLoading"])
        self.assertFalse(result["state"]["quotaLoadingByFile"]["demo.json"])
        self.assertFalse(result["state"]["resetCardLoadingByFile"]["demo.json"])
        self.assertFalse(result["state"]["batchQuotaRefreshing"])
        self.assertEqual(0, result["state"]["batchQuotaRefreshingCount"])


if __name__ == "__main__":
    unittest.main()
