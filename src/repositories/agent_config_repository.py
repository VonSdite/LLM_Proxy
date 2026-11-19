"""Agent 目标机器、配置路径与受管字段摘要的持久化。"""

import json
import math
import os
import posixpath
from copy import deepcopy
from pathlib import Path

from ..external.agent_config_transport import LocalConfigFiles


class AgentConfigRepository:
    """状态文件使用原子替换，字段所有权仅保存值的摘要。"""

    def __init__(self, directory: Path):
        self.path = directory / "state.json"
        self.files = LocalConfigFiles()

    def load(self) -> dict:
        raw = self.files.read(str(self.path))
        if raw is None:
            return {"version": 3, "machines": {}, "paths": {}, "managed": {}}
        try:
            data = json.loads(raw)
            if data.get("version") not in (1, 2, 3) or any(
                not isinstance(data.get(key), dict) for key in ("machines", "paths", "managed")
            ):
                raise ValueError
            pending = data.get("claudeEnvCleanup", {})
            if not isinstance(pending, dict):
                raise ValueError
            for key, entry in pending.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(entry, dict)
                    or not all(isinstance(entry.get(name), str) for name in ("machine", "path"))
                    or not isinstance(entry.get("keys"), list)
                    or not all(isinstance(name, str) for name in entry["keys"])
                    or type(entry.get("due")) not in (int, float)
                    or not math.isfinite(entry["due"])
                ):
                    raise ValueError
            for machine_id, paths in data["paths"].items():
                if isinstance(paths, str) and data["version"] == 1:
                    paths = {"opencode": paths} if paths else {}
                    data["paths"][machine_id] = paths
                if not isinstance(paths, dict) or any(
                    not isinstance(target, str) or not target or not isinstance(path, str)
                    for target, path in paths.items()
                ):
                    raise ValueError
                if data["version"] < 3 and paths.get("codex"):
                    join = os.path.join if machine_id == "local" else posixpath.join
                    directory = paths["codex"]
                    paths["codex"] = join(directory, "config.toml")
                    paths["codexAuth"] = join(directory, "auth.json")
            data["version"] = 3
            return data
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Agent configuration registry is invalid") from exc

    def save(self, state: dict) -> None:
        self.files.write(
            str(self.path), (json.dumps(deepcopy(state), ensure_ascii=False, indent=2) + "\n").encode(), 0o600
        )
