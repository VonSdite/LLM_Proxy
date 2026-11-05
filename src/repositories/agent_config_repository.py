"""Agent 目标机器、配置路径与受管字段摘要的持久化。"""

import json
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
            return {"version": 2, "machines": {}, "paths": {}, "managed": {}}
        try:
            data = json.loads(raw)
            if data.get("version") not in (1, 2) or any(
                not isinstance(data.get(key), dict) for key in ("machines", "paths", "managed")
            ):
                raise ValueError
            for machine_id, paths in data["paths"].items():
                if isinstance(paths, str) and data["version"] == 1:
                    paths = {"opencode": paths} if paths else {}
                    data["paths"][machine_id] = paths
                if not isinstance(paths, dict) or any(
                    target not in ("claude", "codex", "opencode") or not isinstance(path, str)
                    for target, path in paths.items()
                ):
                    raise ValueError
            data["version"] = 2
            return data
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Agent configuration registry is invalid") from exc

    def save(self, state: dict) -> None:
        self.files.write(
            str(self.path), (json.dumps(deepcopy(state), ensure_ascii=False, indent=2) + "\n").encode(), 0o600
        )
