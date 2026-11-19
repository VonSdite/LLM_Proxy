"""声明式 Agent 扩展、DSH 与 Hermes 的配置保存回归。"""

from __future__ import annotations

import json
import os
import posixpath
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.external.agent_config_transport import AgentConfigError
from src.services.agent_config_documents import parse_document
from src.services.agent_extensions import AgentExtensions, yaml_load

from test_agent_config import draft as legacy_draft
from test_agent_config import make_service


class AgentExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.env = patch.dict(
            os.environ, {key: "" for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "DSH_HOME", "HERMES_HOME")}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.service = make_service(self.directory)
        self.addCleanup(self.service.close)

    def save(self, agent, values):
        snapshot = self.service.snapshot("browser", "local")
        ticket = self.service.preview(
            "browser",
            "local",
            agent,
            {
                "draft": {"values": values},
                "versions": {key: file["version"] for key, file in snapshot["files"].items()},
            },
        )
        self.service.apply("browser", "local", ticket["token"])
        return ticket

    def test_registry_and_remote_paths(self):
        registry = self.service.extensions
        self.assertEqual([], registry.errors)
        self.assertEqual(
            ["Claude", "Codex", "Opencode v1", "Opencode v2", "DSH", "Hermes"],
            [item["label"] for item in registry.public()],
        )
        paths = registry.paths(SimpleNamespace(home="/home/alice", local=False), posixpath)
        self.assertEqual("/home/alice/.dsh/cordis.patch.yml", paths["dsh"])
        self.assertEqual("/home/alice/.hermes/config.yaml", paths["hermes"])
        bootstrap = self.service.bootstrap("browser", "http://localhost:8080")
        self.assertEqual([], bootstrap["extensionErrors"])
        self.assertEqual(6, len(bootstrap["agents"]))

    def test_opencode_existing_sharing_and_update_snapshot(self):
        path = self.directory / "home/.config/opencode/opencode.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        for share in ("disabled", "manual", "auto"):
            for update in (False, True, "notify"):
                with self.subTest(share=share, update=update):
                    path.write_text(
                        json.dumps({"share": share, "autoupdate": update, "disabled_providers": ["openai"]}),
                        encoding="utf-8",
                    )
                    managed = self.service.snapshot("browser", "local")["files"]["opencode"]["managed"]
                    self.assertEqual(share, managed["share"])
                    self.assertEqual(update, managed["autoupdate"])
                    self.assertEqual(["openai"], managed["disabled_providers"])

    def test_preview_order_matches_forms_across_files(self):
        for agent in ("claude", "codex", "opencode", "opencode_v2", "dsh", "hermes"):
            registry = self.service.extensions
            if registry.agents[agent].get("legacy"):
                draft = legacy_draft(agent)
            else:
                draft = {
                    "values": {"base-url": "http://localhost:8080/v1", "api-key": "test-key", "model": "main/model"}
                }
            snapshot = self.service.snapshot("browser", "local")
            ticket = self.service.preview(
                "browser",
                "local",
                agent,
                {
                    "draft": draft,
                    "versions": {target: file["version"] for target, file in snapshot["files"].items()},
                },
            )
            paths = [row["path"] for row in ticket["rows"]]
            with self.subTest(agent=agent):
                self.assertTrue(paths[0].endswith(("base_url", "baseURL", "ANTHROPIC_BASE_URL")), paths)
                self.assertTrue(
                    paths[1].endswith(
                        ("apiKey", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "LLM_PROXY_API_KEY", "api_key")
                    ),
                    paths,
                )
                self.assertTrue(paths[2].endswith(("model", "ANTHROPIC_MODEL", "default", "modelID")), paths)
                self.assertTrue(all("_order" not in row for row in ticket["rows"]))

    def test_opencode_v2_native_fields_and_round_trip(self):
        values = {
            "base-url": "http://localhost:8080/v1",
            "api-key": "secret-v2",
            "model": "main/org/model.v1",
            "protocol": "@opencode/ai/providers/openai-compatible",
            "effort": "high",
            "context": 200000,
            "buffer": "15%",
            "output": 32000,
            "permission": "allow",
            "auto-compress": True,
            "snapshots": False,
            "update": "notify",
        }
        ticket = self.save("opencode_v2", values)
        path = self.directory / "home/.config/opencode/opencode.json"
        data = json.loads(path.read_text())
        self.assertEqual("proxy/main/org/model.v1", data["model"])
        provider = data["providers"]["proxy"]
        self.assertEqual("secret-v2", provider["settings"]["apiKey"])
        model = provider["models"]["main/org/model.v1"]
        self.assertEqual("main/org/model.v1", model["modelID"])
        self.assertEqual("high", model["settings"]["reasoningEffort"])
        self.assertEqual({"context": 200000, "output": 32000}, model["limit"])
        self.assertEqual([{"action": "*", "resource": "*", "effect": "allow"}], data["permissions"])
        self.assertEqual({"buffer": "15%", "auto": True}, data["compaction"])
        self.assertNotIn("permission", data)
        self.assertNotIn("provider", data)
        self.assertNotIn("secret-v2", json.dumps(ticket))
        self.assertFalse(self.save("opencode_v2", values)["hasChanges"])
        managed = self.service.snapshot("browser", "local")["files"]["opencodeV2"]["managed"]
        self.assertEqual(values, managed)
        self.save("opencode_v2", {"buffer": "20000", "permission": "ask"})
        data = json.loads(path.read_text())
        self.assertEqual(20000, data["compaction"]["buffer"])
        self.assertFalse(self.save("opencode_v2", {"buffer": "20000"})["hasChanges"])
        self.save("opencode_v2", {"api-key": None, "buffer": None, "permission": None})
        data = json.loads(path.read_text())
        self.assertNotIn("apiKey", data["providers"]["proxy"]["settings"])
        self.assertNotIn("buffer", data["compaction"])
        self.assertNotIn("permissions", data)

    def test_opencode_v2_provider_exclusions_and_sharing(self):
        path = self.directory / "home/.config/opencode/opencode.json"
        path.parent.mkdir(parents=True)
        policy = [{"action": "tool.use", "resource": "shell:rm *", "effect": "deny"}]
        path.write_text(json.dumps({"policies": policy, "autoupdate": False}), encoding="utf-8")
        values = {"disabled-providers": "openai, anthropic，openai", "share": "disabled", "update": "notify"}
        self.save("opencode_v2", values)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(["openai", "anthropic"], data["disabled_providers"])
        self.assertEqual("disabled", data["share"])
        self.assertEqual("notify", data["update"])
        self.assertIs(False, data["autoupdate"])
        self.assertEqual(policy, data["policies"])
        self.assertFalse(self.save("opencode_v2", values)["hasChanges"])
        managed = self.service.snapshot("browser", "local")["files"]["opencodeV2"]["managed"]
        self.assertEqual("openai, anthropic", managed["disabled-providers"])
        self.assertEqual("disabled", managed["share"])
        self.save("opencode_v2", {"disabled-providers": None, "share": None})
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("disabled_providers", data)
        self.assertNotIn("share", data)
        self.assertEqual(policy, data["policies"])

    def test_opencode_v2_existing_alias_comments_and_model_switch(self):
        path = self.directory / "home/.config/opencode/opencode.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            "// 保留注释\n"
            + json.dumps(
                {
                    "model": "proxy/alias",
                    "provider": {"v1": {"options": {"apiKey": "v1-key"}}},
                    "permission": "ask",
                    "compaction": {"reserved": 12345, "buffer": 12000, "keep": {"tokens": 5000}},
                    "permissions": [{"action": "shell", "resource": "git *", "effect": "allow"}],
                    "providers": {
                        "proxy": {
                            "package": "@opencode/ai/providers/openai/responses",
                            "settings": {"baseURL": "https://provider.example/v1", "apiKey": "provider-key"},
                            "models": {
                                "alias": {
                                    "modelID": "main/old",
                                    "settings": {
                                        "reasoningEffort": "high",
                                        "apiKey": "model-key",
                                        "baseURL": "https://model.example/v1",
                                    },
                                    "limit": {"context": 200000, "output": 32000},
                                    "headers": {"X-Custom": "keep"},
                                }
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        managed = self.service.snapshot("browser", "local")["files"]["opencodeV2"]["managed"]
        self.assertEqual("main/old", managed["model"])
        self.assertEqual("model-key", managed["api-key"])
        self.assertEqual({"custom": True}, managed["permission"])
        self.save("opencode_v2", {"effort": "low"})
        original = parse_document("json", path.read_text(encoding="utf-8"))
        self.assertEqual("proxy/alias", original["model"])
        self.assertIn("proxy", original["providers"])
        self.save("opencode_v2", {"model": "main/new", "api-key": "new-key"})
        data = parse_document("json", path.read_text(encoding="utf-8"))
        self.assertIn("// 保留注释", path.read_text(encoding="utf-8"))
        self.assertEqual("proxy/main/new", data["model"])
        self.assertEqual(
            original["providers"]["proxy"]["models"]["alias"], data["providers"]["proxy"]["models"]["alias"]
        )
        model = data["providers"]["proxy"]["models"]["main/new"]
        self.assertEqual("low", model["settings"]["reasoningEffort"])
        self.assertEqual("new-key", model["settings"]["apiKey"])
        self.assertEqual("https://model.example/v1", model["settings"]["baseURL"])
        self.assertEqual(32000, model["limit"]["output"])
        for key in ("provider", "permission", "compaction", "permissions"):
            self.assertEqual(original[key], data[key])
        self.assertEqual("provider-key", data["providers"]["proxy"]["settings"]["apiKey"])

    def test_opencode_v2_validation_and_independent_path(self):
        for value in ("0", "0%", "100%", "150%", "-2", "NaN", "20k"):
            with self.assertRaises(ValueError):
                self.save("opencode_v2", {"buffer": value})
        with self.assertRaises(ValueError):
            self.save("opencode_v2", {"model": "main/a", "context": 1000, "output": 2000})
        paths = self.service.extensions.paths(SimpleNamespace(home="/home/alice", local=False), posixpath)
        self.assertEqual(paths["opencode"], paths["opencodeV2"])
        target = str(self.directory / "home/opencode-v2.jsonc")
        self.service.set_path("browser", "local", {"target": "opencodeV2", "path": target})
        self.save("opencode_v2", {"model": "main/a", "base-url": "http://localhost:8080/v1"})
        self.assertTrue(Path(target).is_file())
        self.assertFalse((self.directory / "home/.config/opencode/opencode.json").exists())

    def test_optional_default_models_clear_and_round_trip(self):
        for agent, target in (("opencode_v2", "opencodeV2"), ("dsh", "dsh"), ("hermes", "hermes")):
            with self.subTest(agent=agent):
                values = {
                    "base-url": "http://localhost:8080/v1",
                    "api-key": "test-key",
                    "model": "main/model",
                    "effort": "high",
                    "context": 200000,
                    "output": 32000,
                }
                if agent == "hermes":
                    values.pop("output")
                    values["provider"] = "custom"
                self.save(agent, values)
                path = Path(self.service.snapshot("browser", "local")["files"][target]["path"])
                load = lambda: (
                    parse_document("json", path.read_text(encoding="utf-8"))
                    if agent == "opencode_v2"
                    else yaml_load(path.read_text(encoding="utf-8"))
                )
                original = load()
                values["model"] = None
                ticket = self.save(agent, values)
                self.assertTrue(ticket["hasChanges"])
                saved = load()
                if agent == "opencode_v2":
                    self.assertNotIn("model", saved)
                    self.assertEqual(original["providers"], saved["providers"])
                    self.assertNotIn("", saved["providers"]["proxy"]["models"])
                elif agent == "dsh":
                    self.assertFalse(any(row["id"] == "agent-default-model" for row in saved))
                    self.assertEqual([row for row in original if row["id"] != "agent-default-model"], saved)
                else:
                    self.assertNotIn("default", saved["model"])
                    expected = original["model"].copy()
                    expected.pop("default")
                    self.assertEqual(expected, saved["model"])
                managed = self.service.snapshot("browser", "local")["files"][target]["managed"]
                self.assertNotIn("model", managed)
                self.assertFalse(self.save(agent, values)["hasChanges"])
                values.pop("model")
                self.assertFalse(self.save(agent, values)["hasChanges"])
                values["model"] = "main/new"
                self.save(agent, values)
                self.assertEqual(
                    "main/new", self.service.snapshot("browser", "local")["files"][target]["managed"]["model"]
                )

    def test_new_connections_without_default_do_not_create_empty_model_entries(self):
        for agent, target in (("opencode_v2", "opencodeV2"), ("dsh", "dsh"), ("hermes", "hermes")):
            with self.subTest(agent=agent):
                values = {"base-url": "https://example.com/v1", "model": None}
                self.save(agent, values)
                snapshot = self.service.snapshot("browser", "local")["files"][target]
                self.assertNotIn("model", snapshot["managed"])
                text = Path(snapshot["path"]).read_text(encoding="utf-8")
                if agent == "opencode_v2":
                    data = parse_document("json", text)
                    self.assertNotIn("models", data["providers"]["proxy"])
                elif agent == "dsh":
                    self.assertEqual(["llm-pi-ai"], [row["id"] for row in yaml_load(text)])
                self.assertFalse(self.save(agent, values)["hasChanges"])

    def test_hermes_preserves_comments_and_unmanaged_values_and_clears_explicitly(self):
        path = self.directory / "home/.hermes/config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(
            '# 用户注释\nmodel: ""\nmcp_servers:\n  demo: {command: uvx}\nagent:\n  reasoning_effort: {enabled: true, effort: thinking}\n',
            encoding="utf-8",
        )
        ticket = self.save(
            "hermes",
            {
                "base-url": "http://localhost:8080/v1",
                "api-key": "secret-hermes",
                "model": "main/org/model.v1",
                "provider": "custom",
                "protocol": "chat_completions",
                "memory": False,
                "approvals": "off",
                "compression": 0.65,
            },
        )
        text = path.read_text(encoding="utf-8")
        data = yaml_load(text)
        self.assertIn("# 用户注释", text)
        self.assertEqual("uvx", data["mcp_servers"]["demo"]["command"])
        self.assertEqual("main/org/model.v1", data["model"]["default"])
        self.assertEqual("thinking", data["agent"]["reasoning_effort"]["effort"])
        self.assertIs(False, data["memory"]["memory_enabled"])
        self.assertEqual("off", data["approvals"]["mode"])
        self.assertNotIn("secret-hermes", json.dumps(ticket))
        self.save("hermes", {"api-key": None, "memory": None})
        data = yaml_load(path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", data["model"])
        self.assertNotIn("memory_enabled", data["memory"])
        self.assertEqual("main/org/model.v1", data["model"]["default"])
        self.assertTrue(Path(str(path) + ".bak.1").is_file())

    def test_dsh_patch_and_separate_credentials_preserve_other_routes(self):
        path = self.directory / "home/.dsh/cordis.patch.yml"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# 插件注释\n- id: other-plugin\n  config: {enabled: true}\n- id: llm-pi-ai\n  config:\n    providers:\n      external: {baseURL: https://other.example/v1}\n      proxy:\n        models:\n          - {id: old, contextWindow: 1234, custom: keep}\n",
            encoding="utf-8",
        )
        values = {
            "base-url": "http://localhost:8080/v1",
            "api-key": "secret-dsh",
            "model": "main/gpt-5",
            "protocol": "openai-completions",
            "context": 200000,
            "output": 32000,
        }
        ticket = self.save("dsh", values)
        text = path.read_text(encoding="utf-8")
        patches = yaml_load(text)
        self.assertIn("# 插件注释", text)
        self.assertEqual("other-plugin", patches[0]["id"])
        providers = patches[1]["config"]["providers"]
        self.assertEqual("https://other.example/v1", providers["external"]["baseURL"])
        self.assertEqual("keep", providers["proxy"]["models"][0]["custom"])
        self.assertEqual("main/gpt-5", providers["proxy"]["models"][1]["id"])
        credentials = self.directory / "home/.dsh/.credentials.yaml"
        self.assertEqual("secret-dsh", yaml_load(credentials.read_text())["refs"]["LLM_PROXY_API_KEY"])
        self.assertNotIn("secret-dsh", text)
        self.assertNotIn("secret-dsh", json.dumps(ticket))
        self.assertFalse(self.save("dsh", values)["hasChanges"])
        snapshot = self.service.snapshot("browser", "local")
        self.assertEqual(200000, snapshot["files"]["dsh"]["managed"]["context"])
        self.save("dsh", {"api-key": None})
        self.assertNotIn("apiKeyEnv", yaml_load(path.read_text())[1]["config"]["providers"]["proxy"])

    def test_dsh_global_permissions_round_trip_and_clear(self):
        values = {"base-url": "http://localhost:8080/v1", "model": "main/test", "protocol": "openai-completions"}
        path = self.directory / "home/.dsh/cordis.patch.yml"
        self.assertNotIn("permission", self.service.snapshot("browser", "local")["files"]["dsh"]["managed"])
        for mode, approval in (("read-only", "ask"), ("workspace-write", "ask"), ("danger-full-access", "never")):
            with self.subTest(mode=mode):
                ticket = self.save("dsh", {**values, "permission": mode})
                rows = yaml_load(path.read_text(encoding="utf-8"))
                permission = next(row["config"] for row in rows if row["id"] == "permission")
                self.assertEqual(mode, permission["defaultPreset"])
                self.assertEqual({"sandbox": mode, "approval": approval}, permission["presets"][mode])
                self.assertEqual(3, len(permission["presets"]))
                self.assertEqual(
                    mode, self.service.snapshot("browser", "local")["files"]["dsh"]["managed"]["permission"]
                )
                self.assertIn("permission.config.defaultPreset", [row["path"] for row in ticket["rows"]])
                self.assertFalse(self.save("dsh", {**values, "permission": mode})["hasChanges"])
        self.save("dsh", {**values, "permission": None})
        permission = next(row["config"] for row in yaml_load(path.read_text()) if row["id"] == "permission")
        self.assertNotIn("defaultPreset", permission)
        self.assertEqual(3, len(permission["presets"]))
        self.assertFalse((path.parent / "profiles").exists())

    def test_dsh_custom_path_and_permission_presets_are_preserved(self):
        path = self.directory / "custom/profiles/web/cordis.patch.yml"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# 用户权限\n- id: permission\n  config:\n    defaultPreset: workspace-write\n    extra: keep\n    presets:\n      workspace-write: {sandbox: read-only, approval: never}\n      restricted: {sandbox: read-only, approval: never}\n",
            encoding="utf-8",
        )
        self.service.set_path("browser", "local", {"target": "dsh", "path": str(path)})
        values = {"base-url": "http://localhost:8080/v1", "model": "main/test", "protocol": "openai-completions"}
        managed = self.service.snapshot("browser", "local")["files"]["dsh"]["managed"]
        self.assertEqual({"custom": True}, managed["permission"])
        self.save("dsh", values)
        permission = next(row["config"] for row in yaml_load(path.read_text()) if row["id"] == "permission")
        self.assertEqual("read-only", permission["presets"]["workspace-write"]["sandbox"])
        self.save("dsh", {**values, "permission": "read-only"})
        permission = next(row["config"] for row in yaml_load(path.read_text()) if row["id"] == "permission")
        self.assertEqual("keep", permission["extra"])
        self.assertEqual({"sandbox": "read-only", "approval": "never"}, permission["presets"]["restricted"])
        self.assertIn("# 用户权限", path.read_text(encoding="utf-8"))
        self.assertFalse((self.directory / "home/.dsh/cordis.patch.yml").exists())
        self.assertEqual(str(path), self.service.snapshot("browser", "local")["files"]["dsh"]["path"])
        registry = self.service.extensions
        with patch.dict(os.environ, {"DSH_HOME": str(self.directory / "custom-home")}):
            paths = registry.paths(SimpleNamespace(home="unused", local=True), os.path)
        self.assertEqual(str(self.directory / "custom-home/cordis.patch.yml"), paths["dsh"])

    def test_new_declarative_agent_and_invalid_extension_isolation(self):
        root = self.directory / "agents"
        good = root / "sample"
        good.mkdir(parents=True)
        (good / "agent.yaml").write_text(
            "id: sample\nname: Sample\nfiles:\n  sample:\n    path: ~/.sample/config.toml\n    format: toml\nfields:\n  - id: credential\n    label: 凭据\n    component: secret\n    file: sample\n    path: [login, value]\n",
            encoding="utf-8",
        )
        bad = root / "broken"
        bad.mkdir()
        (bad / "agent.yaml").write_text("id: mismatch\n", encoding="utf-8")
        registry = AgentExtensions(root)
        self.assertEqual(["sample"], list(registry.agents))
        self.assertEqual("broken", registry.errors[0]["id"])
        self.service.extensions = registry
        target = str(self.directory / "custom/config.toml")
        self.service.set_path("browser", "local", {"target": "sample", "path": target})
        ticket = self.save("sample", {"credential": "secret-extension"})
        self.assertNotIn("secret-extension", json.dumps(ticket))
        self.assertEqual("secret-extension", registry.parse("sample", Path(target).read_text())["login"]["value"])
        self.assertEqual(target, self.service.repository.load()["paths"]["local"]["sample"])

    def test_validation_and_file_conflict(self):
        registry = self.service.extensions
        for values in (
            {"compression": 1.1},
            {"memory": "false"},
            {"turns": 1.5},
            {"base-url": "file:///etc/passwd"},
            {"unknown": True},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                registry.desired("hermes", {"values": values}, [])
        with self.assertRaises(ValueError):
            registry.parse("dsh", "- id: llm-pi-ai\n- id: llm-pi-ai\n")
        with self.assertRaises(ValueError):
            registry.parse("dshCredentials", "version: 2\n")
        snapshot = self.service.snapshot("browser", "local")
        payload = {
            "versions": {key: value["version"] for key, value in snapshot["files"].items()},
            "draft": {"values": {"memory": True}},
        }
        ticket = self.service.preview("browser", "local", "hermes", payload)
        path = Path(snapshot["files"]["hermes"]["path"])
        path.parent.mkdir(parents=True)
        path.write_text("memory: {memory_enabled: false}\n", encoding="utf-8")
        with self.assertRaises(AgentConfigError):
            self.service.apply("browser", "local", ticket["token"])
        self.assertIn("false", path.read_text())

    def test_dsh_rolls_back_profile_when_credentials_write_fails(self):
        snapshot = self.service.snapshot("browser", "local")
        ticket = self.service.preview(
            "browser",
            "local",
            "dsh",
            {
                "versions": {key: value["version"] for key, value in snapshot["files"].items()},
                "draft": {
                    "values": {
                        "base-url": "http://localhost:8080/v1",
                        "model": "main/model",
                        "protocol": "openai-completions",
                        "api-key": "secret",
                    }
                },
            },
        )
        write = self.service.local.write

        def fail_credentials(path, data, mode=0o600):
            if path == snapshot["files"]["dshCredentials"]["path"]:
                raise OSError("Simulated credential write failure")
            return write(path, data, mode)

        with patch.object(self.service.local, "write", side_effect=fail_credentials), self.assertRaises(OSError):
            self.service.apply("browser", "local", ticket["token"])
        self.assertFalse(Path(snapshot["files"]["dsh"]["path"]).exists())
        self.assertFalse(Path(snapshot["files"]["dshCredentials"]["path"]).exists())

    def test_parse_errors_are_isolated_in_snapshot(self):
        snapshot = self.service.snapshot("browser", "local")
        path = Path(snapshot["files"]["dsh"]["path"])
        path.parent.mkdir(parents=True)
        path.write_text("- id: [invalid\n", encoding="utf-8")
        snapshot = self.service.snapshot("browser", "local")
        self.assertTrue(snapshot["files"]["dsh"]["parseError"])
        self.assertFalse(snapshot["files"]["hermes"]["parseError"])
