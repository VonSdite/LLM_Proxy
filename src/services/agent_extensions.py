"""从项目 agents 目录加载受信任的 Agent 定义和配置适配器。"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
from copy import deepcopy
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from ruamel.yaml import YAML

from . import agent_config_documents as documents

ROOT = Path(__file__).resolve().parents[2] / "agents"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
COMPONENTS = {"text", "secret", "model", "select", "boolean", "number", "url"}


def yaml_load(text):
    """以往返模式保留注释，不执行 YAML 标签中的代码。"""
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    return parser.load(text)


def yaml_dump(data):
    writer = YAML(typ="rt")
    writer.preserve_quotes = True
    writer.width = 4096
    stream = StringIO()
    writer.dump(data, stream)
    return stream.getvalue()


def apply_edits(data, edits):
    """只修改声明字段，不覆盖同层的其他配置。"""
    for path, value in edits.items():
        current = data
        for key in path[:-1]:
            if key not in current:
                if value is documents.MISSING:
                    break
                current[key] = {}
            if not isinstance(current[key], dict):
                raise ValueError(f"Configuration field {key} must be an object")
            current = current[key]
        else:
            if value is documents.MISSING:
                current.pop(path[-1], None)
            else:
                current[path[-1]] = deepcopy(value)
    return data


class AgentExtensions:
    """定义在服务启动时加载；文件传输和保存事务仍由配置服务管理。"""

    def __init__(self, directory=ROOT):
        self.agents = {}
        self.files = {}
        self.errors = []
        self.adapters = {}
        for folder in sorted(Path(directory).iterdir()):
            source = folder / "agent.yaml"
            if not folder.is_dir() or not source.is_file():
                continue
            try:
                definition = yaml.safe_load(source.read_text(encoding="utf-8"))
                self._validate(folder.name, definition)
                groups = list(definition.get("groups", {}))
                if groups:
                    definition["fields"] = sorted(
                        definition.get("fields", []),
                        key=lambda field: groups.index(field["group"]) if field.get("group") in groups else len(groups),
                    )
                adapter = None
                if (folder / "adapter.py").is_file():
                    spec = importlib.util.spec_from_file_location(
                        f"llm_proxy_agent_{folder.name}", folder / "adapter.py"
                    )
                    adapter = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = adapter
                    spec.loader.exec_module(adapter)
                self.agents[folder.name] = definition
                self.adapters[folder.name] = adapter
                for key, file in definition["files"].items():
                    self.files[key] = {**file, "agent": folder.name}
            except Exception as exc:
                self.errors.append({"id": folder.name, "message": str(exc)})
        self.agents = dict(sorted(self.agents.items(), key=lambda item: (item[1].get("order", 100), item[0])))

    def _validate(self, agent, definition):
        if not IDENTIFIER.fullmatch(agent) or not isinstance(definition, dict) or definition.get("id") != agent:
            raise ValueError("Agent ID must match its directory name")
        if not isinstance(definition.get("name"), str) or not definition["name"].strip():
            raise ValueError("Agent name is required")
        if type(definition.get("order", 100)) is not int:
            raise ValueError("Agent order must be an integer")
        if type(definition.get("legacy", False)) is not bool:
            raise ValueError("Legacy must be a boolean")
        if definition.get("legacy") and agent not in ("claude", "codex", "opencode"):
            raise ValueError("Legacy forms are reserved for Claude, Codex and Opencode")
        if not isinstance(definition.get("files"), dict) or not definition["files"]:
            raise ValueError("Agent files are required")
        for target, file in definition["files"].items():
            if (
                not isinstance(target, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", target)
                or target in self.files
            ):
                raise ValueError("File target must be unique")
            if not isinstance(file, dict) or file.get("format") not in ("json", "jsonc", "toml", "yaml"):
                raise ValueError("Supported formats: json, jsonc, toml, yaml")
            if not isinstance(file.get("path"), str) or not file["path"].strip():
                raise ValueError("Default file path is required")
        fields = definition.get("fields", [])
        groups = definition.get("groups", {})
        if not isinstance(groups, dict) or not all(
            isinstance(key, str) and type(value) is int and 1 <= value <= 4 for key, value in groups.items()
        ):
            raise ValueError("Group columns must be integers from 1 to 4")
        if not isinstance(fields, list):
            raise ValueError("Fields must be a list")
        ids, paths = set(), set()
        for field in fields:
            if not isinstance(field, dict) or not IDENTIFIER.fullmatch(str(field.get("id", ""))) or field["id"] in ids:
                raise ValueError("Field IDs must be unique lowercase identifiers")
            ids.add(field["id"])
            if field.get("component") not in COMPONENTS or field.get("file") not in definition["files"]:
                raise ValueError("Invalid field component or file")
            path = field.get("path")
            if not isinstance(path, list) or not path or not all(isinstance(key, str) and key for key in path):
                raise ValueError("Field paths must be nonempty string arrays")
            location = (field["file"], *path)
            if location in paths:
                raise ValueError("Field paths must be unique")
            paths.add(location)
            if not isinstance(field.get("label"), str):
                raise ValueError("Field label is required")
            if "group" in field and not isinstance(field["group"], str):
                raise ValueError("Field group must be a string")
            if "help" in field and (
                not isinstance(field["help"], list) or not all(isinstance(item, str) for item in field["help"])
            ):
                raise ValueError("Field help must be a string list")
            if (field["component"] == "select" or "options" in field) and (
                not isinstance(field.get("options"), list)
                or not all(
                    isinstance(option, dict)
                    and isinstance(option.get("value"), str)
                    and isinstance(option.get("label"), str)
                    for option in field["options"]
                )
            ):
                raise ValueError("Field options require string values and labels")
            if "default" in field and field["default"] not in (None, ""):
                self._validate_value(field, field["default"])
        for field in fields:
            condition = field.get("showWhen")
            if condition is not None and (
                not isinstance(condition, dict)
                or condition.get("field") not in ids
                or type(condition.get("equals")) not in (str, bool, int, float)
            ):
                raise ValueError("showWhen requires a declared field and scalar equals value")
        constants = definition.get("constants", {})
        if not isinstance(constants, dict) or set(constants) - set(definition["files"]):
            raise ValueError("Constant targets must belong to this Agent")
        for entries in constants.values():
            if not isinstance(entries, list) or not all(
                isinstance(entry, dict)
                and isinstance(entry.get("path"), list)
                and entry["path"]
                and all(isinstance(key, str) and key for key in entry["path"])
                and "value" in entry
                for entry in entries
            ):
                raise ValueError("Constants require a path and value")

    def secret(self, target, path):
        return any(
            field["component"] == "secret" and field["file"] == target and tuple(field["path"]) == path
            for field in self.agents[self.files[target]["agent"]].get("fields", [])
        ) or any(name in path[-1].lower() for name in ("token", "api_key", "apikey", "password"))

    @staticmethod
    def preview_value(value):
        """将 YAML 日期或带标签的旧值转为可展示的 JSON 值。"""
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False))
        except (TypeError, ValueError):
            return str(value)

    def display_path(self, target, path):
        adapter = self.adapters[self.files[target]["agent"]]
        if adapter and hasattr(adapter, "display_path"):
            return adapter.display_path(target, path)
        return documents.display_path(path)

    def preview_order(self, agent, target, path, draft):
        """预览按表单顺序跨文件排列，自动生成的附属字段放在末尾。"""
        definition = self.agents[agent]
        if not definition.get("legacy"):
            locations = [(field["file"], tuple(field["path"])) for field in definition.get("fields", [])]
        elif agent == "claude":
            paths = [
                ("env", name)
                for name in (
                    "ANTHROPIC_BASE_URL",
                    "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_MODEL",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL",
                    "CLAUDE_CODE_EFFORT_LEVEL",
                    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
                    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
                    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                )
            ]
            paths += [("permissions", "defaultMode"), ("sandbox", "enabled"), ("attribution",), ("autoMemoryEnabled",)]
            paths += [("env", values[0]) for values in documents.CLAUDE_MODES.values()]
            paths += [("skipWebFetchPreflight",)]
            locations = [(agent, item) for item in paths]
        else:
            model = str(draft.get("model") or "").strip()
            provider = documents.provider_key(model)
            if agent == "codex":
                locations = [
                    (agent, ("model_providers", provider, "base_url")),
                    ("codexAuth", ("OPENAI_API_KEY",)),
                    *[
                        (agent, (name,))
                        for name in (
                            "model",
                            "model_reasoning_effort",
                            "model_context_window",
                            "model_auto_compact_token_limit",
                            "service_tier",
                            "sandbox_mode",
                            "approval_policy",
                        )
                    ],
                    (agent, ("features", "memories")),
                ]
            else:
                prefix = ("provider", provider)
                selected = (*prefix, "models", model)
                paths = [
                    (*prefix, "options", "baseURL"),
                    (*prefix, "options", "apiKey"),
                    ("model",),
                    (*selected, "options", "reasoningEffort"),
                    (*selected, "limit", "context"),
                    ("compaction", "reserved"),
                    (*selected, "limit", "output"),
                    ("disabled_providers",),
                    ("share",),
                    ("autoupdate",),
                    ("permission",),
                ]
                locations = [(agent, item) for item in paths]
        try:
            return locations.index((target, path))
        except ValueError:
            return len(locations)

    def public(self):
        return [
            {
                "id": key,
                "label": value["name"],
                "files": list(value["files"]),
                "fields": value.get("fields", []),
                "legacy": value.get("legacy", False),
                "help": value.get("help", []),
                "fileFormats": {key: file["format"] for key, file in value["files"].items()},
                "groups": value.get("groups", {}),
            }
            for key, value in self.agents.items()
        ]

    def paths(self, files, path_module):
        result = {}
        for target, definition in self.files.items():
            path = definition["path"]
            if files.local and definition.get("homeEnv") and os.environ.get(definition["homeEnv"]):
                path = path_module.join(os.environ[definition["homeEnv"]], *definition.get("homeRelative", []))
            elif path.startswith("~/"):
                path = path_module.join(files.home, *path[2:].split("/"))
            result[target] = path
        return result

    def parse(self, target, text):
        file = self.files[target]
        adapter = self.adapters[file["agent"]]
        if adapter and hasattr(adapter, "parse"):
            try:
                return adapter.parse(target, text)
            except Exception as exc:
                raise ValueError(f"Invalid {file['format'].upper()} configuration: {exc}") from exc
        if file["format"] == "yaml":
            try:
                data = yaml_load(text) if text.strip() else {}
                if not isinstance(data, dict):
                    raise ValueError("Configuration must contain a YAML mapping")
                return data
            except Exception as exc:
                raise ValueError("Invalid YAML configuration") from exc
        return documents.parse_document("codex" if file["format"] == "toml" else "json", text)

    def edit(self, target, text, edits):
        file = self.files[target]
        adapter = self.adapters[file["agent"]]
        if adapter and hasattr(adapter, "edit"):
            return adapter.edit(target, text, edits)
        if file["format"] == "yaml":
            return yaml_dump(apply_edits(self.parse(target, text), edits))
        return documents.edit_document("codex" if file["format"] == "toml" else "json", text, edits)

    def visible(self, target, data):
        agent = self.files[target]["agent"]
        adapter = self.adapters[agent]
        if adapter and hasattr(adapter, "visible"):
            return adapter.visible(target, data)
        result = {
            field["id"]: deepcopy(value)
            for field in self.agents[agent].get("fields", [])
            if field["file"] == target
            if (value := documents.at_path(data, tuple(field["path"]))) is not documents.MISSING
        }
        for key, value in result.items():
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError):
                result[key] = {"custom": True}
        return result

    def desired(self, agent, draft, catalog):
        adapter = self.adapters[agent]
        if adapter and hasattr(adapter, "desired"):
            return adapter.desired(draft, catalog)
        if not isinstance(draft, dict) or not isinstance(draft.get("values"), dict):
            raise ValueError("Invalid Agent form")
        fields = self.agents[agent].get("fields", [])
        values = draft["values"]
        if set(values) - {field["id"] for field in fields}:
            raise ValueError("Unknown Agent field")
        result = {target: {} for target in self.agents[agent]["files"]}
        for field in fields:
            condition = field.get("showWhen")
            if condition and values.get(condition["field"]) != condition["equals"]:
                continue
            if field["id"] not in values:
                continue
            value = values[field["id"]]
            if value is None or value == "":
                if field.get("required"):
                    raise ValueError(f"请填写{field['label']}")
                value = documents.MISSING
            else:
                self._validate_value(field, value)
            result[field["file"]][tuple(field["path"])] = value
        for target, defaults in self.agents[agent].get("constants", {}).items():
            for entry in defaults:
                result[target][tuple(entry["path"])] = entry["value"]
        if adapter and hasattr(adapter, "prepare"):
            return adapter.prepare(draft, result)
        return result

    @staticmethod
    def _validate_value(field, value):
        kind = field["component"]
        invalid = False
        if kind == "boolean":
            invalid = type(value) is not bool
        elif kind == "number":
            invalid = (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value < field.get("min", 0)
                or value > field.get("max", 2_000_000_000)
                or (field.get("integer", True) and int(value) != value)
            )
        else:
            invalid = not isinstance(value, str) or len(value) > 16384 or any(char in value for char in "\r\n\x00")
            if not invalid and kind == "select":
                invalid = value not in [option["value"] for option in field["options"]]
            if not invalid and kind == "url":
                parsed = urlsplit(value)
                invalid = (
                    parsed.scheme not in ("http", "https")
                    or not parsed.hostname
                    or bool(parsed.username or parsed.password or parsed.query or parsed.fragment)
                )
                try:
                    invalid = invalid or (parsed.port is not None and not 1 <= parsed.port <= 65535)
                except ValueError:
                    invalid = True
        if invalid:
            raise ValueError(f"{field['label']}的值无效")
