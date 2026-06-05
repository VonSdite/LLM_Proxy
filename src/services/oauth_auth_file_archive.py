#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OAuth 认证文件归档与导出工具。"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from ..utils.local_time import now_local_datetime


@dataclass(frozen=True)
class OAuthAuthFileExport:
    """认证文件 ZIP 导出结果。"""

    filename: str
    content: bytes
    count: int
    names: tuple[str, ...]


@dataclass(frozen=True)
class OAuthAuthFileImportItem:
    """待导入的认证文件内容。"""

    name: str
    content: bytes
    source: str


@dataclass(frozen=True)
class OAuthAuthFileImportResult:
    """认证文件导入结果。"""

    imported: int
    failed: int
    total: int
    imported_files: tuple[str, ...]
    failed_files: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 响应字典。"""
        return {
            "status": "ok",
            "imported": self.imported,
            "failed": self.failed,
            "total": self.total,
            "imported_files": list(self.imported_files),
            "failed_files": [dict(item) for item in self.failed_files],
        }


def move_auth_file_to_deleted(auth_file: Path, deleted_dir: Path) -> Path:
    """把认证文件移动到删除归档目录，并返回新路径。"""
    deleted_dir.mkdir(parents=True, exist_ok=True)
    deleted_at = now_local_datetime()
    target = _build_deleted_auth_file_path(deleted_dir, auth_file.name, deleted_at)
    shutil.move(str(auth_file), str(target))
    return target


def build_auth_files_zip(auth_files: list[Path], provider: str) -> OAuthAuthFileExport:
    """把认证文件打包成 ZIP 字节内容。"""
    timestamp = now_local_datetime().strftime("%Y%m%d%H%M%S")
    safe_provider = "".join(char for char in str(provider or "").strip().lower() if char.isalnum() or char == "-")
    archive_name = f"{safe_provider or 'oauth'}-oauth-auth-files-{timestamp}.zip"
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for auth_file in auth_files:
            archive.write(auth_file, arcname=auth_file.name)
    return OAuthAuthFileExport(
        filename=archive_name,
        content=output.getvalue(),
        count=len(auth_files),
        names=tuple(auth_file.name for auth_file in auth_files),
    )


def expand_auth_file_import_sources(
    sources: list[tuple[str, bytes]],
) -> tuple[list[OAuthAuthFileImportItem], list[dict[str, str]]]:
    """展开导入源文件，支持 JSON 文件和导出结构一致的 ZIP。"""
    items: list[OAuthAuthFileImportItem] = []
    failures: list[dict[str, str]] = []
    for raw_name, content in sources:
        source_name = _normalize_import_file_name(raw_name)
        if not source_name:
            failures.append({"name": str(raw_name or "<empty>"), "reason": "Invalid file name"})
            continue
        if source_name.lower().endswith(".zip"):
            _append_zip_import_items(source_name, content, items, failures)
            continue
        if source_name.lower().endswith(".json"):
            items.append(OAuthAuthFileImportItem(name=source_name, content=content, source=source_name))
            continue
        failures.append({"name": source_name, "reason": "Unsupported file type"})
    return items, failures


def _build_deleted_auth_file_path(deleted_dir: Path, original_name: str, deleted_at: datetime) -> Path:
    """构造删除归档文件名；同秒重名时添加序号避免覆盖。"""
    timestamp = deleted_at.strftime("%Y%m%d%H%M%S")
    target = deleted_dir / f"{timestamp}_{original_name}"
    if not target.exists():
        return target
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    index = 1
    while True:
        next_target = deleted_dir / f"{timestamp}_{stem}-{index}{suffix}"
        if not next_target.exists():
            return next_target
        index += 1


def _append_zip_import_items(
    source_name: str,
    content: bytes,
    items: list[OAuthAuthFileImportItem],
    failures: list[dict[str, str]],
) -> None:
    """读取 ZIP 中的认证文件，ZIP 内部必须是导出时的扁平 JSON 结构。"""
    try:
        with ZipFile(io.BytesIO(content), "r") as archive:
            entries = archive.infolist()
            if not entries:
                failures.append({"name": source_name, "reason": "ZIP file is empty"})
                return
            for entry in entries:
                entry_name = _normalize_zip_entry_name(entry.filename)
                if not entry_name:
                    failures.append({"name": entry.filename or source_name, "reason": "Invalid ZIP structure"})
                    continue
                try:
                    entry_content = archive.read(entry)
                except Exception as exc:
                    failures.append({"name": entry_name, "reason": f"Failed to read ZIP entry: {exc}"})
                    continue
                items.append(OAuthAuthFileImportItem(name=entry_name, content=entry_content, source=source_name))
    except BadZipFile:
        failures.append({"name": source_name, "reason": "Invalid ZIP file"})


def _normalize_import_file_name(name: str) -> str:
    """只允许普通文件名，禁止路径穿越。"""
    text = str(name or "").strip()
    cleaned_name = Path(text).name
    return cleaned_name if cleaned_name and cleaned_name == text else ""


def _normalize_zip_entry_name(name: str) -> str:
    """校验 ZIP entry 必须是导出结构中的根目录 JSON 文件。"""
    text = str(name or "").strip()
    if not text or text.endswith("/"):
        return ""
    if "/" in text or "\\" in text:
        return ""
    cleaned_name = Path(text).name
    if cleaned_name != text or not cleaned_name.lower().endswith(".json"):
        return ""
    return cleaned_name
