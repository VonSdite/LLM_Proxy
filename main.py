#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from gevent import monkey

monkey.patch_all()

import urllib3  # noqa: E402

# 禁用 HTTPS 证书告警
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if TYPE_CHECKING:
    from src.application import Application


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Proxy Service")
    parser.add_argument(
        "--config", type=str, default=None, help="Configuration file path"
    )
    return parser.parse_args()


def app() -> "Application":
    from src.application import Application

    args = parse_args()
    config_path = args.config
    project_root = Path(__file__).resolve().parent
    if config_path is None:
        resolved_config_path = project_root / "config.yaml"
    else:
        config_candidate = Path(config_path)
        resolved_config_path = (
            config_candidate
            if config_candidate.is_absolute()
            else (Path.cwd() / config_candidate).resolve()
        )

    if not resolved_config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {resolved_config_path}")

    return Application(resolved_config_path)


def main() -> None:
    app().run()


if __name__ == "__main__":
    main()
