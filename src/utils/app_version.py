#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用版本读取工具。"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "000llm-proxy"
DEFAULT_APP_VERSION = "0.0.0"


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """读取应用版本，源码运行优先使用仓库里的 pyproject.toml。"""
    version = _read_pyproject_version(_project_root() / "pyproject.toml")
    if version:
        return version

    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return DEFAULT_APP_VERSION


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_pyproject_version(path: Path) -> str | None:
    if not path.is_file():
        return None

    try:
        pyproject = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None

    project = pyproject.get("project")
    if not isinstance(project, dict):
        return None

    version = project.get("version")
    if not isinstance(version, str):
        return None

    normalized = version.strip()
    return normalized or None
