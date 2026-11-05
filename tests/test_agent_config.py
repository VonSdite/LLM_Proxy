"""Agent 配置的格式保留、写入事务、浏览器隔离和管理接口回归。"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.external.agent_config_transport import AgentConfigError
from src.presentation.agent_config_controller import AgentConfigController
from src.presentation.app_factory import create_flask_app
from src.services.agent_config_documents import MISSING, JsoncDocument, desired_fields, edit_document, parse_document
from src.services.agent_config_service import AgentConfigService


def make_service(directory):
    """所有配置均写入临时用户目录，环境变量不引用真实配置。"""
    config = SimpleNamespace(
        get_database_path=lambda: str(directory / "data" / "requests.db"),
        is_oauth_enabled=lambda: True,
        is_model_mapping_enabled=lambda: True,
        is_api_key_management_enabled=lambda: True,
        get_server_host=lambda: "0.0.0.0",
        get_server_port=lambda: 8080,
    )
    ctx = SimpleNamespace(
        config_manager=config, logger=logging.getLogger("test.agent_config"), flask_app=create_flask_app()
    )
    providers = SimpleNamespace(
        list_visible_model_names=lambda: ["main/gpt-5.4", "main/org/model.v1", "main/claude-sonnet-4-6"]
    )
    keys = SimpleNamespace(
        get_api_keys=lambda **kwargs: [
            {"name": "active", "api_key": "sk-downstream", "enabled": True},
            {"name": "disabled", "api_key": "sk-disabled", "enabled": False},
        ]
    )
    mapping = SimpleNamespace(list_mapping_ids=lambda: ["smart-model"])
    codex = SimpleNamespace(list_model_names=lambda: ["codex/gpt-5.4"])
    claude = SimpleNamespace(list_model_names=lambda: ["claude/claude-sonnet-4-6"])
    return AgentConfigService(ctx, providers, keys, mapping, codex, claude, home=directory / "home")


def draft(agent="claude"):
    return {
        "baseUrl": "http://127.0.0.1:8080" + ("" if agent == "claude" else "/v1"),
        "apiKey": "sk-downstream",
        "model": "main/org/model.v1",
        "models": {"ANTHROPIC_MODEL": "main/claude-sonnet-4-6"},
        "limits": {"context": 200000, "compact": 85, "output": 64000},
        "runtime": {
            "effortLevel": "high",
            "sandboxEnabled": "disabled",
            "modelReasoningEffort": "high",
            "reasoningEffort": "high",
        },
    }


class ConfigDocumentTests(unittest.TestCase):
    def test_jsonc_preserves_comments_unknown_values_and_model_id(self):
        source = '{\n  // 用户插件\n  "plugin": ["x", {"enabled": true}],\n  "provider": {"main": {"models": {"main/org/model.v1": {"temperature": 0.2}}}},\n}\n'
        updated = edit_document(
            "opencode",
            source,
            {
                ("provider", "main", "models", "main/org/model.v1", "name"): "main/org/model.v1",
                ("model",): "main/main/org/model.v1",
            },
        )
        data = parse_document("opencode", updated)
        self.assertIn("// 用户插件", updated)
        self.assertEqual(["x", {"enabled": True}], data["plugin"])
        self.assertEqual(0.2, data["provider"]["main"]["models"]["main/org/model.v1"]["temperature"])
        self.assertEqual("main/org/model.v1", data["provider"]["main"]["models"]["main/org/model.v1"]["name"])

    def test_jsonc_delete_positions_and_trailing_commas(self):
        for source in ('{"a":1,"b":2,"c":3}', '{"a":1,"b":2,"c":3,}', '{"a":1,/* 注释 */"b":2,// 注释\n"c":3}'):
            for key in ("a", "b", "c"):
                with self.subTest(source=source, key=key):
                    result = edit_document("claude", source, {(key,): MISSING})
                    self.assertEqual(
                        {name: value for name, value in {"a": 1, "b": 2, "c": 3}.items() if name != key},
                        parse_document("claude", result),
                    )
        self.assertEqual({}, parse_document("claude", edit_document("claude", '{"a":1}', {("a",): MISSING})))

    def test_jsonc_rejects_duplicate_keys_invalid_shapes_and_syntax(self):
        for text in ('{"x":1,"x":2}', "[1]", '{"x":}', '{"x":1 "y":2}', "{} true", '{"x":/* unclosed}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                JsoncDocument(text)
        with self.assertRaises(ValueError):
            edit_document("claude", '{"env":false}', {("env", "key"): "value"})

    def test_toml_preserves_comments_and_unrelated_tables(self):
        source = '# 用户设置\nmodel = "old" # 当前模型\n\n[mcp_servers.test]\ncommand = "uvx"\n'
        result = edit_document(
            "codex",
            source,
            {
                ("model",): "main/org/model.v1",
                ("model_providers", "name.with.dot", "base_url"): "http://localhost:8080/v1",
            },
        )
        self.assertIn("# 用户设置", result)
        self.assertIn("# 当前模型", result)
        data = parse_document("codex", result)
        self.assertEqual("uvx", data["mcp_servers"]["test"]["command"])
        self.assertEqual("http://localhost:8080/v1", data["model_providers"]["name.with.dot"]["base_url"])

    def test_authoritative_fields_preserve_full_proxy_ids(self):
        fields = desired_fields("codex", draft("codex"), [])
        self.assertEqual("main/org/model.v1", fields["codex"][("model",)])
        self.assertEqual("responses", fields["codex"][("model_providers", "main", "wire_api")])
        fields = desired_fields("opencode", draft("opencode"), ["main/gpt-5.4", "other/model"])["opencode"]
        self.assertEqual("main/main/org/model.v1", fields[("model",)])
        self.assertEqual("main/org/model.v1", fields[("provider", "main", "models", "main/org/model.v1", "name")])
        self.assertEqual(372000, fields[("provider", "main", "models", "main/gpt-5.4", "limit", "context")])
        self.assertNotIn(("provider", "main", "models", "other/model", "name"), fields)

    def test_claude_output_budget_and_compaction_window_are_independent(self):
        data = draft()
        data["limits"]["context"] = 100000
        data["limits"]["compact"] = 100
        data["limits"]["output"] = 128000
        fields = desired_fields("claude", data, [])["claude"]
        self.assertEqual("128000", fields[("env", "CLAUDE_CODE_MAX_OUTPUT_TOKENS")])
        self.assertEqual("100000", fields[("env", "CLAUDE_CODE_AUTO_COMPACT_WINDOW")])
        self.assertEqual("100", fields[("env", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")])

    def test_validation_limits_and_claude_runtime_choices(self):
        for update in (
            {"baseUrl": "file:///tmp/x"},
            {"baseUrl": "http://localhost:99999"},
            {"runtime": {"sandboxEnabled": "execute"}},
            {"runtime": {"sandboxEnabled": True}},
            {"runtime": {"skipWebFetchPreflight": True}},
            {"runtime": {"skipWebFetchPreflight": "true"}},
            {"runtime": {"permissionMode": "bypasspermission"}},
            {"runtime": {"permissionMode": True}},
            {"limits": {"context": True}},
            {"limits": {"compact": 101}},
            {"runtime": []},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                desired_fields("claude", {**draft(), **update}, [])
        for agent in ("codex", "opencode"):
            for limits in ({"context": 100, "output": 200}, {"compact": 100}):
                with self.subTest(agent=agent, limits=limits), self.assertRaises(ValueError):
                    desired_fields(agent, {**draft(agent), "limits": limits}, [])

    def test_claude_environment_marker_and_supported_overrides(self):
        data = draft()
        data["models"]["ANTHROPIC_REASONING_MODEL"] = "legacy-model"
        data["runtime"]["sandbox"] = "enabled"
        fields = desired_fields("claude", data, [])["claude"]
        self.assertNotIn(("env", "ANTHROPIC_REASONING_MODEL"), fields)
        self.assertEqual("1", fields[("env", "IS_SANDBOX")])
        self.assertIs(False, fields[("sandbox", "enabled")])
        for effort in ("auto", "unset"):
            data["runtime"]["effortLevel"] = effort
            fields = desired_fields("claude", data, [])["claude"]
            self.assertEqual(effort, fields[("env", "CLAUDE_CODE_EFFORT_LEVEL")])

    def test_exact_file_compaction_values_survive_form_round_trip(self):
        data = draft("codex")
        data["limits"] = {"context": 372000, "compact": 94, "compactTokens": 350000}
        self.assertEqual(350000, desired_fields("codex", data, [])["codex"][("model_auto_compact_token_limit",)])
        data = draft("opencode")
        data["limits"]["reserved"] = 0
        self.assertEqual(0, desired_fields("opencode", data, [])["opencode"][("compaction", "reserved")])

    def test_unset_codex_limits_stay_unset_and_existing_absolute_limit_is_preserved(self):
        data = draft("codex")
        data["limits"] = {"context": None, "compact": None}
        fields = desired_fields("codex", data, [])["codex"]
        self.assertNotIn(("model_context_window",), fields)
        self.assertNotIn(("model_auto_compact_token_limit",), fields)
        data["limits"]["compactTokens"] = 800000
        fields = desired_fields("codex", data, [])["codex"]
        self.assertNotIn(("model_context_window",), fields)
        self.assertEqual(800000, fields[("model_auto_compact_token_limit",)])
        data["limits"] = {"context": 400000, "compact": None}
        self.assertNotIn(("model_auto_compact_token_limit",), desired_fields("codex", data, [])["codex"])
        data["limits"] = {"context": None, "compact": 85}
        with self.assertRaisesRegex(ValueError, "上下文窗口"):
            desired_fields("codex", data, [])


class AgentConfigFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"CODEX_HOME": "", "CLAUDE_CONFIG_DIR": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.service = make_service(self.directory)
        self.addCleanup(self.service.close)
        self.owner = "browser-one"

    def snapshot(self):
        return self.service.snapshot(self.owner, "local")

    def preview(self, agent="claude", data=None):
        snapshot = self.snapshot()
        return self.service.preview(
            self.owner,
            "local",
            agent,
            {
                "versions": {target: item["version"] for target, item in snapshot["files"].items()},
                "draft": data or draft(agent),
            },
        )

    def save(self, agent="claude", data=None):
        return self.service.apply(self.owner, "local", self.preview(agent, data)["token"])


class AgentConfigServiceTests(AgentConfigFixture):
    def test_codex_memories_round_trip_and_clear_preserve_other_settings(self):
        path = Path(self.snapshot()["files"]["codex"]["path"])
        path.parent.mkdir(parents=True)
        path.write_text(
            "# 功能设置\n[features]\nmemories = false\ngoals = false\nmulti_agent = true\n"
            '\n[memories]\ngenerate_memories = false\n\n[mcp_servers.test]\ncommand = "uvx"\n',
            encoding="utf-8",
        )
        data = draft("codex")
        data["runtime"] = {}
        for mode, expected in ((None, False), ("enabled", True), ("disabled", False), ("enabled", True)):
            with self.subTest(mode=mode):
                if mode is not None:
                    data["runtime"]["memories"] = mode
                result = self.save("codex", data)
                self.assertIs(expected, result["files"]["codex"]["managed"]["features.memories"])
                saved = parse_document("codex", path.read_text(encoding="utf-8"))
                self.assertEqual({"memories": expected, "goals": False, "multi_agent": True}, saved["features"])
                self.assertEqual({"generate_memories": False}, saved["memories"])
                self.assertEqual("uvx", saved["mcp_servers"]["test"]["command"])
                self.assertFalse(self.preview("codex", data)["hasChanges"])
        data["runtime"]["memories"] = "unset"
        data["clearFields"] = ["runtime.memories"]
        preview = self.preview("codex", data)
        self.assertEqual({"features.memories"}, {row["path"] for row in preview["rows"] if row["kind"] == "remove"})
        result = self.service.apply(self.owner, "local", preview["token"])
        self.assertNotIn("features.memories", result["files"]["codex"]["managed"])
        text = path.read_text(encoding="utf-8")
        saved = parse_document("codex", text)
        self.assertEqual({"goals": False, "multi_agent": True}, saved["features"])
        self.assertEqual({"generate_memories": False}, saved["memories"])
        self.assertEqual("uvx", saved["mcp_servers"]["test"]["command"])
        self.assertIn("# 功能设置", text)

    def test_codex_memories_missing_value_keeps_defaults_and_rejects_invalid_choices(self):
        data = draft("codex")
        for value in (True, "true", "false", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                desired_fields("codex", {**data, "runtime": {"memories": value}}, [])
        for runtime in ({}, {"memories": "unset"}):
            data["runtime"] = runtime
            result = self.save("codex", data)
            self.assertNotIn("features.memories", result["files"]["codex"]["managed"])
            saved = parse_document("codex", Path(result["files"]["codex"]["path"]).read_text(encoding="utf-8"))
            self.assertNotIn("features", saved)

    def test_new_claude_configuration_saves_requested_defaults_after_preview(self):
        data = draft()
        data["runtime"] = {
            "sandboxEnabled": "unset",
            "attributionHeader": "disabled",
            "disableNonessentialTraffic": "enabled",
            "experimentalAgentTeams": "enabled",
            "enableToolSearch": "enabled",
            "sandbox": "enabled",
            "skipWebFetchPreflight": "enabled",
        }
        self.assertFalse(self.snapshot()["files"]["claude"]["exists"])
        preview = self.preview(data=data)
        self.assertFalse((self.directory / "home").exists())
        self.assertTrue(any(row["path"] == "skipWebFetchPreflight" and row["next"] is True for row in preview["rows"]))
        self.assertFalse(any(row["path"] == "sandbox.enabled" for row in preview["rows"]))
        result = self.service.apply(self.owner, "local", preview["token"])
        saved = json.loads(Path(result["files"]["claude"]["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("sandbox", saved)
        self.assertNotIn("permissions", saved)
        self.assertIs(True, saved["skipWebFetchPreflight"])
        for name, value in {
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            "ENABLE_TOOL_SEARCH": "true",
            "IS_SANDBOX": "1",
        }.items():
            self.assertEqual(value, saved["env"][name])
        self.assertNotIn("skipWebFetchPreflight", saved["env"])
        self.assertFalse(self.preview(data=data)["hasChanges"])

    def test_webfetch_preflight_round_trips_boolean_and_explicitly_clears_only_top_level_key(self):
        path = Path(self.snapshot()["files"]["claude"]["path"])
        path.parent.mkdir(parents=True)
        original = {
            "skipWebFetchPreflight": False,
            "permissions": {"deny": ["WebFetch(domain:example.org)", "WebSearch"]},
            "env": {"OTHER_SETTING": "keep"},
        }
        path.write_text("// 用户设置\n" + json.dumps(original), encoding="utf-8")
        data = draft()
        data["runtime"] = {}
        for mode, expected in ((None, False), ("enabled", True), ("disabled", False), ("enabled", True)):
            with self.subTest(mode=mode):
                if mode is not None:
                    data["runtime"]["skipWebFetchPreflight"] = mode
                result = self.save(data=data)
                self.assertIs(expected, result["files"]["claude"]["managed"]["skipWebFetchPreflight"])
                saved = parse_document("claude", path.read_text(encoding="utf-8"))
                self.assertIs(expected, saved["skipWebFetchPreflight"])
                self.assertEqual(original["permissions"], saved["permissions"])
                self.assertEqual("keep", saved["env"]["OTHER_SETTING"])
                self.assertNotIn("skipWebFetchPreflight", saved["env"])
        data["runtime"]["skipWebFetchPreflight"] = "unset"
        data["clearFields"] = ["runtime.skipWebFetchPreflight"]
        preview = self.preview(data=data)
        self.assertEqual({"skipWebFetchPreflight"}, {row["path"] for row in preview["rows"] if row["kind"] == "remove"})
        self.service.apply(self.owner, "local", preview["token"])
        saved = parse_document("claude", path.read_text(encoding="utf-8"))
        self.assertNotIn("skipWebFetchPreflight", saved)
        self.assertEqual(original["permissions"], saved["permissions"])
        self.assertEqual("keep", saved["env"]["OTHER_SETTING"])
        self.assertIn("// 用户设置", path.read_text(encoding="utf-8"))

    def test_missing_configs_are_created_and_secrets_masked(self):
        preview = self.preview()
        self.assertTrue(preview["hasChanges"])
        self.assertNotIn("sk-downstream", json.dumps(preview))
        self.assertFalse((self.directory / "home").exists())
        result = self.service.apply(self.owner, "local", preview["token"])
        path = Path(result["files"]["claude"]["path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("sk-downstream", data["env"]["ANTHROPIC_AUTH_TOKEN"])
        self.assertIs(False, data["sandbox"]["enabled"])
        self.assertNotIn("IS_SANDBOX", data["env"])
        self.assertNotIn("sk-downstream", self.service.repository.path.read_text(encoding="utf-8"))
        self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", path.read_bytes())

    def test_concurrent_file_changes_reject_preview_and_apply(self):
        self.save()
        snapshot = self.snapshot()
        path = Path(snapshot["files"]["claude"]["path"])
        preview = self.preview(data={**draft(), "apiKey": "new-key"})
        path.write_text('{"manual": true}\n', encoding="utf-8")
        with self.assertRaises(AgentConfigError) as error:
            self.service.apply(self.owner, "local", preview["token"])
        self.assertEqual("file_conflict", error.exception.code)
        with self.assertRaises(AgentConfigError):
            self.service.preview(
                self.owner,
                "local",
                "claude",
                {"versions": {target: item["version"] for target, item in snapshot["files"].items()}, "draft": draft()},
            )
        self.assertEqual({"manual": True}, json.loads(path.read_text()))

    def test_unset_only_removes_unchanged_owned_fields(self):
        self.save()
        path = Path(self.snapshot()["files"]["claude"]["path"])
        data = json.loads(path.read_text())
        data["sandbox"]["enabled"] = "manually-changed"
        data["mcpServers"] = {"local": {"command": "uvx"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        new_draft = draft()
        new_draft["runtime"] = {}
        self.save(data=new_draft)
        saved = json.loads(path.read_text())
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", saved["env"])
        self.assertEqual("manually-changed", saved["sandbox"]["enabled"])
        self.assertEqual(data["mcpServers"], saved["mcpServers"])

    def test_claude_owned_sandbox_marker_survives_permission_changes_and_clears_independently(self):
        data = draft()
        data["runtime"].update(permissionMode="default", sandbox="enabled", sandboxEnabled="enabled")
        self.save(data=data)
        data["runtime"].update(permissionMode="bypassPermissions", sandboxEnabled="disabled")
        result = self.save(data=data)
        managed = result["files"]["claude"]["managed"]
        self.assertEqual("bypassPermissions", managed["permissions.defaultMode"])
        self.assertEqual("1", managed["env.IS_SANDBOX"])
        self.assertIs(False, managed["sandbox.enabled"])
        data["runtime"]["sandbox"] = "unset"
        data["clearFields"] = ["runtime.sandbox"]
        result = self.save(data=data)
        managed = result["files"]["claude"]["managed"]
        self.assertNotIn("env.IS_SANDBOX", managed)
        self.assertIs(False, managed["sandbox.enabled"])
        self.assertEqual("bypassPermissions", managed["permissions.defaultMode"])

    def test_claude_permission_modes_and_sandbox_preserve_existing_policies(self):
        path = Path(self.snapshot()["files"]["claude"]["path"])
        path.parent.mkdir(parents=True)
        original = {
            "permissions": {
                "defaultMode": "manual",
                "allow": ["Bash(git status *)"],
                "ask": ["Bash(git push *)"],
                "deny": ["Read(.env)"],
                "disableBypassPermissionsMode": "disable",
            },
            "sandbox": {"enabled": False, "network": {"allowedDomains": ["example.org"]}},
            "env": {"IS_SANDBOX": "1", "ANTHROPIC_REASONING_MODEL": "legacy-model"},
        }
        path.write_text("// 用户权限规则\n" + json.dumps(original), encoding="utf-8")
        for mode in ("default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions", "manual"):
            with self.subTest(mode=mode):
                data = draft()
                data["runtime"].update(permissionMode=mode, sandboxEnabled="enabled")
                preview = self.preview(data=data)
                self.assertTrue(
                    any(row["path"] == "permissions.defaultMode" and row["next"] == mode for row in preview["rows"])
                )
                result = self.service.apply(self.owner, "local", preview["token"])
                self.assertEqual(mode, result["files"]["claude"]["managed"]["permissions.defaultMode"])
                self.assertIs(True, result["files"]["claude"]["managed"]["sandbox.enabled"])
                saved = parse_document("claude", path.read_text(encoding="utf-8"))
                self.assertEqual({**original["permissions"], "defaultMode": mode}, saved["permissions"])
                self.assertEqual({**original["sandbox"], "enabled": True}, saved["sandbox"])
                self.assertEqual("1", saved["env"]["IS_SANDBOX"])
                self.assertEqual("legacy-model", saved["env"]["ANTHROPIC_REASONING_MODEL"])
                self.assertIn("// 用户权限规则", path.read_text(encoding="utf-8"))

    def test_claude_permission_mode_and_sandbox_can_be_cleared_without_removing_siblings(self):
        path = Path(self.snapshot()["files"]["claude"]["path"])
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "permissions": {"defaultMode": "bypassPermissions", "deny": ["Read(.env)"]},
                    "sandbox": {"enabled": True, "failIfUnavailable": True},
                }
            ),
            encoding="utf-8",
        )
        data = draft()
        data["runtime"] = {"permissionMode": "", "sandboxEnabled": "unset"}
        data["clearFields"] = ["runtime.permissionMode", "runtime.sandboxEnabled"]
        preview = self.preview(data=data)
        removed = {row["path"] for row in preview["rows"] if row["kind"] == "remove"}
        self.assertEqual({"permissions.defaultMode", "sandbox.enabled"}, removed)
        self.service.apply(self.owner, "local", preview["token"])
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({"deny": ["Read(.env)"]}, saved["permissions"])
        self.assertEqual({"failIfUnavailable": True}, saved["sandbox"])

    def test_allow_nonessential_traffic_removes_even_unowned_false_like_values(self):
        path = Path(self.snapshot()["files"]["claude"]["path"])
        path.parent.mkdir(parents=True)
        key = "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
        for value in ("1", "0", "false", "custom"):
            with self.subTest(value=value):
                path.write_text(json.dumps({"env": {key: value, "OTHER_SETTING": "keep"}}), encoding="utf-8")
                data = draft()
                data["runtime"]["disableNonessentialTraffic"] = "disabled"
                preview = self.preview(data=data)
                self.assertTrue(any(row["path"] == "env." + key and row["kind"] == "remove" for row in preview["rows"]))
                self.service.apply(self.owner, "local", preview["token"])
                env = json.loads(path.read_text(encoding="utf-8"))["env"]
                self.assertNotIn(key, env)
                self.assertEqual("keep", env["OTHER_SETTING"])

    def test_explicit_clear_edits_existing_unowned_fields(self):
        path = Path(self.snapshot()["files"]["claude"]["path"])
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_MODEL": "main/claude-sonnet-4-6",
                        "ANTHROPIC_AUTH_TOKEN": "manual-key",
                        "CLAUDE_CODE_EFFORT_LEVEL": "high",
                        "OTHER_SETTING": "keep",
                    }
                }
            ),
            encoding="utf-8",
        )
        changed = draft()
        changed["apiKey"] = ""
        changed["runtime"] = {}
        changed["clearFields"] = ["apiKey", "runtime.effortLevel"]
        preview = self.preview(data=changed)
        self.assertNotIn("manual-key", json.dumps(preview))
        self.assertTrue(
            any(row["kind"] == "remove" and row["path"] == "env.ANTHROPIC_AUTH_TOKEN" for row in preview["rows"])
        )
        self.service.apply(self.owner, "local", preview["token"])
        env = json.loads(path.read_text(encoding="utf-8"))["env"]
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", env)
        self.assertEqual("keep", env["OTHER_SETTING"])
        with self.assertRaises(ValueError):
            self.preview(data={**draft(), "clearFields": ["mcpServers.local"]})

    def test_explicit_clear_of_missing_auth_does_not_create_empty_file(self):
        data = draft("codex")
        data["apiKey"] = ""
        data["clearFields"] = ["apiKey"]
        snapshot = self.save("codex", data)
        self.assertEqual(1, snapshot["written"])
        self.assertFalse(snapshot["files"]["codexAuth"]["exists"])

    def test_claude_model_overrides_can_all_be_unset(self):
        self.save()
        changed = draft()
        changed["models"] = {}
        changed["clearFields"] = ["models.ANTHROPIC_MODEL"]
        saved = self.save(data=changed)
        self.assertNotIn("env.ANTHROPIC_MODEL", saved["files"]["claude"]["managed"])

    def test_backup_rotation_and_raw_restore(self):
        for index in range(7):
            self.save(data={**draft(), "apiKey": f"key-{index}"})
        path = Path(self.snapshot()["files"]["claude"]["path"])
        self.assertEqual("key-5", json.loads(Path(str(path) + ".bak.1").read_text())["env"]["ANTHROPIC_AUTH_TOKEN"])
        self.assertEqual("key-1", json.loads(Path(str(path) + ".bak.5").read_text())["env"]["ANTHROPIC_AUTH_TOKEN"])
        self.assertFalse(Path(str(path) + ".bak.6").exists())
        backup = self.service.read_files(self.owner, "local", "claude", 1)["files"][0]
        preview = self.service.preview(
            self.owner,
            "local",
            "claude",
            {"versions": {"claude": backup["version"]}, "raw": {"claude": backup["text"]}},
        )
        self.service.apply(self.owner, "local", preview["token"])
        self.assertEqual("key-5", json.loads(path.read_text())["env"]["ANTHROPIC_AUTH_TOKEN"])

    def test_codex_multifile_failure_rolls_back_and_preserves_originals(self):
        self.save("codex")
        snapshot = self.snapshot()
        config_path = snapshot["files"]["codex"]["path"]
        auth_path = snapshot["files"]["codexAuth"]["path"]
        config_before = Path(config_path).read_bytes()
        auth_before = Path(auth_path).read_bytes()
        changed = draft("codex")
        changed["model"] = "main/gpt-5.4"
        changed["apiKey"] = "new-key"
        preview = self.preview("codex", changed)
        write = self.service.local.write

        def fail_auth(path, data, mode=0o600):
            if path == auth_path:
                raise OSError("test write failure")
            return write(path, data, mode)

        with patch.object(self.service.local, "write", side_effect=fail_auth), self.assertRaises(OSError):
            self.service.apply(self.owner, "local", preview["token"])
        self.assertEqual(config_before, Path(config_path).read_bytes())
        self.assertEqual(auth_before, Path(auth_path).read_bytes())

    def test_metadata_failure_rolls_back_new_files(self):
        preview = self.preview("codex")
        with (
            patch.object(self.service.repository, "save", side_effect=OSError("test registry failure")),
            self.assertRaises(OSError),
        ):
            self.service.apply(self.owner, "local", preview["token"])
        self.assertFalse(Path(self.snapshot()["files"]["codex"]["path"]).exists())

    def test_parse_error_can_be_corrected_in_raw_editor(self):
        path = Path(self.snapshot()["files"]["claude"]["path"])
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        snapshot = self.snapshot()
        self.assertTrue(snapshot["files"]["claude"]["parseError"])
        versions = {"claude": snapshot["files"]["claude"]["version"]}
        with self.assertRaises(ValueError):
            self.preview()
        preview = self.service.preview(
            self.owner, "local", "claude", {"versions": versions, "raw": {"claude": '{"user":1}'}}
        )
        result = self.service.apply(self.owner, "local", preview["token"])
        self.assertFalse(result["files"]["claude"]["parseError"])
        self.assertEqual("{broken", Path(str(path) + ".bak.1").read_text())

    def test_raw_codex_edit_only_writes_selected_file(self):
        paths = {target: Path(self.snapshot()["files"][target]["path"]) for target in ("codex", "codexAuth")}
        paths["codex"].parent.mkdir(parents=True, exist_ok=True)
        cases = (
            ("codex", 'model = "updated"\n', b'{"OPENAI_API_KEY":"fixture"}\r\n\r\n'),
            ("codexAuth", '{"OPENAI_API_KEY":"updated"}\n', b"invalid = [\r\n"),
        )
        for target, text, other_bytes in cases:
            with self.subTest(target=target):
                other = "codexAuth" if target == "codex" else "codex"
                paths[target].write_bytes(b'{"old":true}\n' if target == "codexAuth" else b'model = "old"\n')
                paths[other].write_bytes(other_bytes)
                backups = {
                    path.name: path.read_bytes() for path in paths[other].parent.glob(paths[other].name + ".bak.*")
                }
                version = self.snapshot()["files"][target]["version"]
                preview = self.service.preview(
                    self.owner, "local", "codex", {"versions": {target: version}, "raw": {target: text}}
                )
                self.assertEqual([paths[target].name], [row["file"] for row in preview["rows"]])
                result = self.service.apply(self.owner, "local", preview["token"])
                self.assertEqual(1, result["written"])
                self.assertEqual(text.encode(), paths[target].read_bytes())
                self.assertEqual(other_bytes, paths[other].read_bytes())
                self.assertEqual(
                    backups,
                    {path.name: path.read_bytes() for path in paths[other].parent.glob(paths[other].name + ".bak.*")},
                )

    def test_raw_codex_edit_keeps_other_missing_file_absent(self):
        snapshot = self.snapshot()
        preview = self.service.preview(
            self.owner,
            "local",
            "codex",
            {"versions": {"codex": snapshot["files"]["codex"]["version"]}, "raw": {"codex": 'model = "fixture"\n'}},
        )
        result = self.service.apply(self.owner, "local", preview["token"])
        self.assertEqual(1, result["written"])
        self.assertFalse(Path(snapshot["files"]["codexAuth"]["path"]).exists())

    def test_raw_null_and_wrong_targets_are_rejected(self):
        for raw in (None, [], {"codex": ""}):
            with self.subTest(raw=raw), self.assertRaises(AgentConfigError):
                self.service.preview(self.owner, "local", "claude", {"versions": {}, "raw": raw})

    def test_opencode_custom_path_reads_actual_file_and_conflicts_old_preview(self):
        old = self.preview("opencode")
        path = self.directory / "home" / "custom" / "opencode.jsonc"
        path.parent.mkdir(parents=True)
        path.write_text('{// 注释\n"theme":"dark"}\n', encoding="utf-8")
        snapshot = self.service.set_path(self.owner, "local", {"path": "~/custom/opencode.jsonc"})
        self.assertEqual(str(path.resolve()), snapshot["files"]["opencode"]["path"])
        with self.assertRaises(AgentConfigError):
            self.service.apply(self.owner, "local", old["token"])
        self.save("opencode")
        self.assertIn("// 注释", path.read_text(encoding="utf-8"))
        self.assertEqual("dark", parse_document("opencode", path.read_text())["theme"])

    def test_each_agent_path_persists_across_restart_and_resets_independently(self):
        defaults = self.snapshot()["files"]
        paths = {
            "claude": "~/profiles/claude/settings.json",
            "codex": "~/profiles/codex",
            "opencode": "~/profiles/opencode.jsonc",
        }
        for agent, path in paths.items():
            with self.subTest(agent=agent):
                old = self.preview(agent)
                result = self.service.set_path(self.owner, "local", {"target": agent, "path": path})
                self.assertEqual(path, result["files"][agent]["customPath"])
                self.assertFalse(result["files"][agent]["usesDefaultPath"])
                with self.assertRaises(AgentConfigError):
                    self.service.apply(self.owner, "local", old["token"])
                saved = self.save(agent)
                self.assertTrue(Path(saved["files"][agent]["path"]).is_file())
                self.assertFalse(Path(defaults[agent]["path"]).exists())
        codex_files = self.snapshot()["files"]
        self.assertEqual(Path(codex_files["codex"]["path"]).parent, Path(codex_files["codexAuth"]["path"]).parent)
        self.assertTrue(Path(codex_files["codexAuth"]["path"]).is_file())
        restarted = make_service(self.directory)
        self.addCleanup(restarted.close)
        restored = restarted.snapshot("other-browser", "local")["files"]
        for agent, path in paths.items():
            self.assertEqual(path, restored[agent]["customPath"])
        reset = restarted.set_path(self.owner, "local", {"target": "codex", "path": ""})["files"]
        for target in ("codex", "codexAuth"):
            self.assertTrue(reset[target]["usesDefaultPath"])
            self.assertEqual(defaults[target]["path"], reset[target]["path"])
            self.assertTrue(Path(restored[target]["path"]).exists())
        self.assertEqual(paths["claude"], reset["claude"]["customPath"])
        self.assertEqual(paths["opencode"], reset["opencode"]["customPath"])

    def test_legacy_opencode_path_migrates_without_losing_other_registry_data(self):
        state = deepcopy(self.service.state)
        state.update(version=1, paths={"local": "~/legacy/opencode.jsonc"})
        state["managed"] = {"fixture": {"digest": "preserved"}}
        self.service.repository.save(state)
        restarted = make_service(self.directory)
        self.addCleanup(restarted.close)
        self.assertEqual(
            "~/legacy/opencode.jsonc", restarted.snapshot(self.owner, "local")["files"]["opencode"]["customPath"]
        )
        restarted.set_path(self.owner, "local", {"target": "claude", "path": "~/custom/settings.json"})
        stored = json.loads(restarted.repository.path.read_text(encoding="utf-8"))
        self.assertEqual(2, stored["version"])
        self.assertEqual("~/legacy/opencode.jsonc", stored["paths"]["local"]["opencode"])
        self.assertEqual(state["managed"], stored["managed"])

    def test_invalid_or_unreadable_path_and_registry_failure_preserve_last_selection(self):
        self.service.set_path(self.owner, "local", {"target": "claude", "path": "~/good/settings.json"})
        before = deepcopy(self.service.state)
        disk_before = self.service.repository.path.read_bytes()
        for payload in (
            {"target": "claude", "path": "wrong.toml"},
            {"target": "codex", "path": "~/config.toml"},
            {"target": "opencode", "path": "wrong.txt"},
            {"target": "unknown", "path": "config.json"},
            {"target": "claude", "path": "bad\npath.json"},
        ):
            with self.subTest(payload=payload), self.assertRaises(AgentConfigError):
                self.service.set_path(self.owner, "local", payload)
        with patch.object(self.service.local, "read", side_effect=PermissionError("fixture unreadable")):
            with self.assertRaises(PermissionError):
                self.service.set_path(self.owner, "local", {"target": "claude", "path": "~/bad/settings.json"})
        with patch.object(self.service.repository, "save", side_effect=OSError("fixture registry failure")):
            with self.assertRaises(OSError):
                self.service.set_path(self.owner, "local", {"target": "claude", "path": "~/other/settings.json"})
        self.assertEqual(before, self.service.state)
        self.assertEqual(disk_before, self.service.repository.path.read_bytes())

    def test_remote_paths_expand_remote_home_and_are_isolated_from_local_paths(self):
        machine = self.service.save_machine(self.owner, {"host": "127.0.0.1", "user": "fixture"})
        files = SimpleNamespace(
            local=False,
            home="/home/fixture",
            resolve=lambda path: path.replace("~/", "/home/fixture/", 1),
            read=lambda path: None,
            alive=lambda: True,
            close=lambda: None,
        )
        self.service.sessions[(self.owner, machine["id"])] = {
            "files": files,
            "id": "fixture-session",
            "touched": time.monotonic(),
        }
        for agent, path in {
            "claude": "~/custom/settings.json",
            "codex": "~/custom/codex",
            "opencode": "~/custom/opencode.jsonc",
        }.items():
            result = self.service.set_path(self.owner, machine["id"], {"target": agent, "path": path})
            self.assertTrue(result["files"][agent]["path"].startswith("/home/fixture/custom/"))
            self.assertTrue(self.snapshot()["files"][agent]["usesDefaultPath"])
        self.assertEqual("/home/fixture/custom/codex/auth.json", result["files"]["codexAuth"]["path"])
        restarted = make_service(self.directory)
        self.addCleanup(restarted.close)
        self.assertEqual(self.service.state["paths"], restarted.state["paths"])

    def test_external_opencode_only_writes_the_selected_external_model(self):
        changed = {**draft("opencode"), "baseUrl": "https://external.example/v1", "model": "main/external-model"}
        result = self.save("opencode", changed)
        path = Path(result["files"]["opencode"]["path"])
        configured = parse_document("opencode", path.read_text(encoding="utf-8"))
        self.assertEqual({"main/external-model"}, set(configured["provider"]["main"]["models"]))
        self.assertEqual("https://external.example/v1", configured["provider"]["main"]["options"]["baseURL"])

    def test_own_opencode_endpoint_includes_catalog_models_through_reverse_proxy(self):
        snapshot = self.snapshot()
        preview = self.service.preview(
            self.owner,
            "local",
            "opencode",
            {
                "versions": {target: item["version"] for target, item in snapshot["files"].items()},
                "draft": {**draft("opencode"), "baseUrl": "https://proxy.example:443/v1/"},
            },
            origin="https://proxy.example",
        )
        result = self.service.apply(self.owner, "local", preview["token"])
        path = Path(result["files"]["opencode"]["path"])
        configured = parse_document("opencode", path.read_text(encoding="utf-8"))
        self.assertIn("main/gpt-5.4", configured["provider"]["main"]["models"])
        self.assertIn("main/org/model.v1", configured["provider"]["main"]["models"])

    def test_external_opencode_cannot_match_catalog_by_host_prefix_or_service_port(self):
        for url in (
            "https://proxy.example.evil/v1",
            "https://proxy.example:444/v1",
            "https://proxy.example/other",
            "http://external.example:8080/v1",
        ):
            with self.subTest(url=url):
                snapshot = self.snapshot()
                preview = self.service.preview(
                    self.owner,
                    "local",
                    "opencode",
                    {
                        "versions": {target: item["version"] for target, item in snapshot["files"].items()},
                        "draft": {**draft("opencode"), "baseUrl": url},
                    },
                    origin="https://proxy.example",
                )
                self.assertFalse(any("main/gpt-5.4" in row["path"] for row in preview["rows"]))

    def test_previews_are_owner_bound_expiring_and_do_not_reconnect(self):
        preview = self.preview()
        with self.assertRaises(AgentConfigError):
            self.service.apply("browser-two", "local", preview["token"])
        self.service.previews[preview["token"]]["expires"] = time.monotonic() - 1
        with self.assertRaises(AgentConfigError):
            self.service.apply(self.owner, "local", preview["token"])
        machine = self.service.save_machine(self.owner, {"host": "server.test", "user": "user"})
        with self.assertRaises(AgentConfigError) as error:
            self.service.snapshot("browser-two", machine["id"])
        self.assertEqual("disconnected", error.exception.code)

    def test_machine_credentials_are_session_only_and_registry_survives_restart(self):
        machine = self.service.save_machine(
            self.owner,
            {
                "name": "test",
                "host": "server.test",
                "user": "user",
                "password": "ssh-secret",
                "proxy": {"mode": "custom", "url": "socks5h://u:proxy-secret@127.0.0.1:7890"},
            },
        )
        state = self.service.repository.path.read_text()
        self.assertNotIn("ssh-secret", state)
        self.assertNotIn("proxy-secret", state)
        self.assertIn("proxy-secret", machine["proxy"]["url"])
        other = self.service.list_machines("browser-two")[1]
        self.assertNotIn("proxy-secret", other["proxy"]["url"])
        restarted = make_service(self.directory)
        self.addCleanup(restarted.close)
        self.assertEqual("test", restarted.list_machines(self.owner)[1]["name"])
        self.assertEqual("", restarted.list_machines(self.owner)[1]["password"])

    def test_bootstrap_uses_public_models_and_downstream_keys(self):
        result = self.service.bootstrap(self.owner, "https://proxy.test")
        self.assertIn("smart-model", result["models"])
        self.assertIn("codex/gpt-5.4", result["models"])
        self.assertEqual([{"name": "active", "key": "sk-downstream"}], result["apiKeys"])
        self.assertTrue(all(provider["apiKey"] == "" for provider in result["providers"]))

    def test_environment_home_overrides_are_respected(self):
        with patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.directory / "codex-home"),
                "CLAUDE_CONFIG_DIR": str(self.directory / "claude-home"),
            },
        ):
            snapshot = self.snapshot()
            self.assertEqual(
                str((self.directory / "codex-home/config.toml").resolve()), snapshot["files"]["codex"]["path"]
            )
            self.assertEqual(
                str((self.directory / "claude-home/settings.json").resolve()), snapshot["files"]["claude"]["path"]
            )


class AgentConfigControllerTests(AgentConfigFixture):
    def setUp(self):
        super().setUp()
        self.auth = Mock()
        self.auth.is_auth_enabled.return_value = False
        self.auth.validate_session.side_effect = lambda token: token == "valid-session"
        self.auth.get_session_username.return_value = "admin"
        AgentConfigController(self.service.ctx, self.service, self.auth)
        self.app = self.service.ctx.flask_app
        self.client = self.app.test_client()
        self.headers = {"X-Agent-Config": "1"}

    def test_page_template_and_no_store_header(self):
        response = self.client.get("/agents")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"/static/js/agents.js", response.data)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])

    def test_remote_access_requires_admin_and_ignores_forwarded_loopback(self):
        response = self.client.get(
            "/api/agent-config/bootstrap",
            headers={**self.headers, "X-Forwarded-For": "127.0.0.1"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.2"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("authentication_required", response.json["code"])
        response = self.client.get("/agents", base_url="http://attacker.test")
        self.assertEqual(403, response.status_code)

    def test_admin_auth_applies_to_page_and_all_apis(self):
        self.auth.is_auth_enabled.return_value = True
        self.assertEqual(302, self.client.get("/agents").status_code)
        self.assertEqual(401, self.client.get("/api/agent-config/bootstrap", headers=self.headers).status_code)
        self.client.set_cookie("session_token", "valid-session")
        response = self.client.get(
            "/api/agent-config/bootstrap", headers=self.headers, environ_overrides={"REMOTE_ADDR": "10.0.0.2"}
        )
        self.assertEqual(200, response.status_code)

    def test_cross_origin_and_missing_request_header_rejected(self):
        url = "/api/agent-config/machines"
        for headers in (
            {},
            {**self.headers, "Origin": "https://attacker.test"},
            {**self.headers, "Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                response = self.client.post(url, headers=headers, json={"host": "server.test", "user": "user"})
                self.assertEqual(403, response.status_code)
        self.assertEqual([], list(self.service.state["machines"]))

    def test_browser_preview_tokens_cannot_be_used_by_another_browser(self):
        base = "/api/agent-config/machines/local"
        snapshot = self.client.get(base + "/snapshot", headers=self.headers).json
        data = {"versions": {target: info["version"] for target, info in snapshot["files"].items()}, "draft": draft()}
        preview = self.client.post(base + "/claude/preview", headers=self.headers, json=data)
        self.assertEqual(200, preview.status_code)
        other = self.app.test_client()
        rejected = other.post(base + "/apply", headers=self.headers, json={"token": preview.json["token"]})
        self.assertEqual(409, rejected.status_code)
        saved = self.client.post(base + "/apply", headers=self.headers, json={"token": preview.json["token"]})
        self.assertEqual(200, saved.status_code)
        self.assertEqual(1, saved.json["written"])

    def test_invalid_json_and_backup_index_have_clear_errors(self):
        response = self.client.post("/api/agent-config/machines", json=[], headers=self.headers)
        self.assertEqual(400, response.status_code)
        response = self.client.get("/api/agent-config/machines/local/claude/files?backup=bad", headers=self.headers)
        self.assertEqual(400, response.status_code)


if __name__ == "__main__":
    unittest.main()
