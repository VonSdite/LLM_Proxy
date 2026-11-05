"""校验项目模型参数表的结构、命名和默认候选一致性。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "src" / "config" / "agent_model_limits.json"
CANDIDATE_KINDS = {"official", "client-default", "derived", "fallback"}


def require(condition: bool, message: str) -> None:
    """校验失败时提供可定位到表项的说明。"""
    if not condition:
        raise ValueError(message)


def unique_object(pairs: list[tuple]) -> dict:
    """拒绝被 JSON 默认解析行为覆盖的重复字段。"""
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


def validate(path: Path) -> tuple[int, int, int]:
    """读取参数表并返回模型组、模型 ID 和别名数量。"""
    raw = path.read_bytes()
    require(not raw.startswith(b"\xef\xbb\xbf"), "Use UTF-8 without BOM")
    require(b"\r" not in raw, "Use LF line endings")
    entries = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    require(isinstance(entries, list) and bool(entries), "Expected a non-empty array")
    names = {}
    counts = {"ids": 0, "aliases": 0}
    for index, entry in enumerate(entries):
        location = f"entry[{index}]"
        require(isinstance(entry, dict), f"{location}: expected an object")
        require(isinstance(entry.get("label"), str) and bool(entry["label"].strip()), f"{location}: missing label")
        for key in counts:
            values = entry.get(key, [])
            require(isinstance(values, list) and (key != "ids" or bool(values)), f"{location}.{key}: invalid list")
            for name in values:
                require(isinstance(name, str) and bool(name.strip()), f"{location}.{key}: expected a model name")
                require(name == name.strip().lower(), f"{location}: model names must be trimmed and lowercase: {name}")
                require("/" not in name and not name.endswith("[1m]"), f"{location}: use the base model name: {name}")
                require(name not in names, f"{location}: duplicate model name {name}, first in {names.get(name)}")
                names[name] = location
                counts[key] += 1
        location += f" ({entry['ids'][0]})"
        candidates = entry.get("candidates")
        require(isinstance(candidates, dict), f"{location}: missing candidates")
        for key in ("context", "compact", "output"):
            default = entry.get(key)
            if key != "compact" or key in entry:
                require(positive_integer(default), f"{location}.{key}: expected a positive integer")
            options = candidates.get(key)
            require(isinstance(options, list) and bool(options), f"{location}.candidates.{key}: empty or missing")
            seen = set()
            for position, option in enumerate(options):
                field = f"{location}.candidates.{key}[{position}]"
                require(isinstance(option, dict), f"{field}: expected an object")
                value = option.get("value")
                require(positive_integer(value), f"{field}: expected a positive integer value")
                require(key != "compact" or value <= 100, f"{field}: percentage must be between 1 and 100")
                require(value not in seen, f"{field}: duplicate candidate value {value}")
                seen.add(value)
                require(
                    isinstance(option.get("kind"), str) and option["kind"] in CANDIDATE_KINDS, f"{field}: invalid kind"
                )
                require(
                    isinstance(option.get("label"), str) and bool(option["label"].strip()), f"{field}: missing label"
                )
                if "sourceUrl" in option:
                    url = option["sourceUrl"]
                    require(isinstance(url, str), f"{field}: invalid sourceUrl")
                    parsed = urlsplit(url)
                    require(parsed.scheme in ("http", "https") and bool(parsed.hostname), f"{field}: invalid sourceUrl")
                if "checkedAt" in option:
                    checked = option["checkedAt"]
                    require(isinstance(checked, str), f"{field}: invalid checkedAt")
                    try:
                        require(date.fromisoformat(checked).isoformat() == checked, f"{field}: use YYYY-MM-DD")
                    except ValueError as error:
                        raise ValueError(f"{field}: checkedAt must be a valid YYYY-MM-DD date") from error
            if default is not None:
                require(default == options[0]["value"], f"{location}.{key}: must match the first candidate")
    return len(entries), counts["ids"], counts["aliases"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Agent model parameter table.")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    arguments = parser.parse_args()
    try:
        groups, ids, aliases = validate(arguments.path)
    except (OSError, ValueError) as error:
        print(f"Model limits validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Model limits valid: {groups} groups, {ids} model IDs, {aliases} aliases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
