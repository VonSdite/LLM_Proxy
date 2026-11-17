"""使用有界内存和独立原生线程保存 LLM 训练记录。"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gevent.monkey import get_original
from gevent.threadpool import ThreadPool

from .llm_record_builder import build_record

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_ACTIVE_BYTES = 64 * 1024 * 1024
MAX_QUEUE_RECORDS = 256
MAX_QUEUE_BYTES = 64 * 1024 * 1024
MAX_SHARD_BYTES = 128 * 1024 * 1024
MAX_PROMPT_CACHE = 1024
_LOG_INTERVAL_SECONDS = 30.0
_TRUNCATED_MARKER = "$llm_recording_truncated"
_SUPPORTED_FORMATS = {"openai_chat", "openai_responses", "claude_chat"}


class _BoundedCopy:
    """递归复制 JSON 数据，达到字节预算后保留明确的截断结构。"""

    def __init__(self, budget: int) -> None:
        self.remaining = budget
        self.truncated = False

    @property
    def used(self) -> int:
        return MAX_INPUT_BYTES - self.remaining

    def copy(self, value: Any, depth: int = 0) -> Any:
        if depth > 64:
            self.truncated = True
            return {_TRUNCATED_MARKER: True}
        if value is None or isinstance(value, (bool, int, float)):
            self._charge(16)
            return value
        if isinstance(value, str):
            return self._copy_string(value)
        if isinstance(value, list):
            self._charge(32)
            result = []
            for item in value:
                if self.remaining <= 0:
                    self.truncated = True
                    result.append({_TRUNCATED_MARKER: True})
                    break
                result.append(self.copy(item, depth + 1))
            return result
        if isinstance(value, dict):
            self._charge(32)
            result = {}
            for key, item in value.items():
                key_text = str(key)
                key_size = len(key_text.encode("utf-8"))
                if self.remaining <= key_size:
                    self.truncated = True
                    self.remaining = 0
                    result[_TRUNCATED_MARKER] = True
                    break
                self.remaining -= key_size
                result[key_text] = self.copy(item, depth + 1)
            return result
        self.truncated = True
        return {_TRUNCATED_MARKER: True, "type": type(value).__name__}

    def _charge(self, size: int) -> None:
        if size > self.remaining:
            self.remaining = 0
            self.truncated = True
        else:
            self.remaining -= size

    def _copy_string(self, value: str) -> Any:
        encoded = value.encode("utf-8")
        if len(encoded) <= self.remaining:
            self.remaining -= len(encoded)
            return value
        budget = max(self.remaining, 0)
        prefix = encoded[:budget]
        while prefix:
            try:
                text = prefix.decode("utf-8")
                break
            except UnicodeDecodeError:
                prefix = prefix[:-1]
        else:
            text = ""
        self.remaining = 0
        self.truncated = True
        return {_TRUNCATED_MARKER: True, "content_prefix": text}


@dataclass(slots=True)
class _CaptureJob:
    request_data: dict[str, Any]
    source_format: str
    output_data: bytes
    input_truncated: bool
    output_truncated: bool
    request_model: str | None
    response_model: str | None
    upstream_protocol: str | None
    started_at: datetime
    outcome: str
    directory: Path
    size: int


class _ShardWriter:
    """单线程使用的提示词存储和排他创建 JSONL 分片。"""

    def __init__(self, directory: Path, instance: str) -> None:
        self.directory = directory
        self.instance = instance
        self.prompts_directory = directory / "prompts"
        self.records_directory = directory / "records"
        self.prompts_directory.mkdir(parents=True, exist_ok=True)
        self.records_directory.mkdir(parents=True, exist_ok=True)
        self._prompt_cache: OrderedDict[str, None] = OrderedDict()
        self._file = None
        self._file_size = 0
        self._file_date: str | None = None
        self._next_sequence = 1

    def write(self, job: _CaptureJob) -> None:
        record, prompts = build_record(
            request_data=job.request_data,
            source_format=job.source_format,
            output_data=job.output_data,
            input_truncated=job.input_truncated,
            output_truncated=job.output_truncated,
            request_model=job.request_model,
            response_model=job.response_model,
            upstream_protocol=job.upstream_protocol,
            started_at=job.started_at,
            outcome=job.outcome,
        )
        for reference in sorted(prompts):
            self._ensure_prompt(reference, prompts[reference])
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        self._write_line(job.started_at.date().isoformat(), line)

    def _ensure_prompt(self, reference: str, prompt: dict[str, Any]) -> None:
        if reference in self._prompt_cache:
            self._prompt_cache.move_to_end(reference)
            return
        digest = reference.removeprefix("sha256:")
        path = self.prompts_directory / f"{digest}.json"
        if path.exists() and path.stat().st_size > 0:
            self._remember_prompt(reference)
            return
        content = (
            json.dumps(prompt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            + b"\n"
        )
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=self.prompts_directory, delete=False) as temporary:
                temporary_path = temporary.name
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass
        self._remember_prompt(reference)

    def _remember_prompt(self, reference: str) -> None:
        self._prompt_cache[reference] = None
        self._prompt_cache.move_to_end(reference)
        while len(self._prompt_cache) > MAX_PROMPT_CACHE:
            self._prompt_cache.popitem(last=False)

    def _write_line(self, date: str, line: bytes) -> None:
        if self._file is None or self._file_date != date or self._file_size + len(line) > MAX_SHARD_BYTES:
            self._open_shard(date)
        assert self._file is not None
        self._file.write(line)
        self._file.flush()
        self._file_size += len(line)

    def _open_shard(self, date: str) -> None:
        self._close_file()
        if self._file_date != date:
            self._next_sequence = 1
        while True:
            path = self.records_directory / f"{date}-{self.instance}-{self._next_sequence:06d}.jsonl"
            self._next_sequence += 1
            try:
                # 已有文件永不续写，崩溃留下的残缺尾行因此保持可识别。
                self._file = open(path, "xb")
                break
            except FileExistsError:
                continue
        self._file_date = date
        self._file_size = 0

    def _close_file(self) -> None:
        if self._file is None:
            return
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        finally:
            self._file.close()
            self._file = None

    def close(self) -> None:
        self._close_file()


class LLMRequestCapture:
    """一次下游请求的有界采集上下文，完成提交幂等。"""

    def __init__(
        self,
        service: "LLMRecordingService",
        *,
        request_data: dict[str, Any],
        source_format: str,
        directory: Path,
        started_at: datetime,
        input_size: int,
        input_truncated: bool,
    ) -> None:
        self._service = service
        self._request_data = request_data
        self._source_format = source_format
        self._directory = directory
        self._started_at = started_at
        self._input_size = input_size
        self._input_truncated = input_truncated
        self._output = bytearray()
        self._output_truncated = False
        self._request_model: str | None = None
        self._response_model: str | None = None
        self._upstream_protocol: str | None = None
        self._finished = False

    def observe_attempt(self, item: dict[str, Any]) -> None:
        """记录最后一次上游尝试的实际模型，不从下游载荷推测。"""
        if not isinstance(item, dict):
            return
        with self._service._lock:
            if self._finished:
                return
            actual_model = item.get("actual_request_model")
            if isinstance(actual_model, str) and actual_model.strip():
                self._request_model = actual_model
            response_model = item.get("response_model")
            self._response_model = response_model if isinstance(response_model, str) and response_model else None
            protocol = item.get("upstream_protocol")
            self._upstream_protocol = protocol if protocol in _SUPPORTED_FORMATS else None

    def feed(self, chunk: bytes | str) -> None:
        """仅追加有界下游字节，不执行协议解析或磁盘操作。"""
        if isinstance(chunk, str):
            data = chunk.encode("utf-8")
        elif isinstance(chunk, (bytes, bytearray, memoryview)):
            data = bytes(chunk)
        else:
            return
        if not data:
            return
        with self._service._lock:
            if self._finished:
                return
            output_remaining = MAX_OUTPUT_BYTES - len(self._output)
            active_remaining = MAX_ACTIVE_BYTES - self._service._active_bytes
            accepted = min(len(data), max(output_remaining, 0), max(active_remaining, 0))
            if accepted > 0:
                self._output.extend(data[:accepted])
                self._service._active_bytes += accepted
            if accepted < len(data):
                self._output_truncated = True

    def finish(self, outcome: str) -> None:
        """将唯一完成快照移入队列，并释放活跃采集预算。"""
        with self._service._lock:
            if self._finished:
                return
            self._finished = True
            output_data = bytes(self._output)
            self._output.clear()
            self._service._active_bytes = max(
                0,
                self._service._active_bytes - self._input_size - len(output_data),
            )
            job = _CaptureJob(
                request_data=self._request_data,
                source_format=self._source_format,
                output_data=output_data,
                input_truncated=self._input_truncated,
                output_truncated=self._output_truncated,
                request_model=self._request_model,
                response_model=self._response_model,
                upstream_protocol=self._upstream_protocol,
                started_at=self._started_at,
                outcome=outcome if outcome in {"success", "error", "cancelled"} else "error",
                directory=self._directory,
                size=self._input_size + len(output_data),
            )
        self._service._submit(job)


class LLMRecordingService:
    """按请求快照配置，以单原生线程完成序列化和文件写入。"""

    def __init__(self, config_manager: Any, root_path: Path, logger: logging.Logger) -> None:
        self._config_manager = config_manager
        self._root_path = Path(root_path)
        self._logger = logger
        self._lock = get_original("_thread", "allocate_lock")()
        self._queue = get_original("queue", "SimpleQueue")()
        self._active_bytes = 0
        self._queued_records = 0
        self._queued_bytes = 0
        self._closed = False
        self._pool: ThreadPool | None = None
        self._thread = None
        self._writers: dict[Path, _ShardWriter] = {}
        self._instance = f"{os.getpid():x}-{uuid.uuid4().hex[:12]}"
        self._dropped = 0
        self._last_logs: dict[str, float] = {}
        self._atexit_registered = True
        atexit.register(self.close)

    def start_request(self, request_data: dict, source_format: str) -> LLMRequestCapture | None:
        """读取本次绝对目录快照；关闭时不复制正文也不创建目录。"""
        if not self._config_manager.is_llm_recording_enabled():
            return None
        if source_format not in _SUPPORTED_FORMATS or not isinstance(request_data, dict):
            return None
        directory_value = str(self._config_manager.get_llm_recording_directory() or "").strip()
        if directory_value:
            directory = Path(directory_value)
            if not directory.is_absolute():
                self._drop("relative_directory")
                return None
            directory = directory.resolve()
        else:
            directory = (self._root_path / "data/llm-records").resolve()
        snapshot = _BoundedCopy(MAX_INPUT_BYTES)
        try:
            request_snapshot = snapshot.copy(request_data)
        except (RecursionError, TypeError, ValueError):
            request_snapshot = {_TRUNCATED_MARKER: True}
            snapshot.truncated = True
        if not isinstance(request_snapshot, dict):
            request_snapshot = {"snapshot": request_snapshot}
            snapshot.truncated = True
        with self._lock:
            if self._closed:
                return None
            if self._active_bytes + snapshot.used > MAX_ACTIVE_BYTES:
                self._drop_locked("active_memory_limit")
                return None
            self._active_bytes += snapshot.used
        return LLMRequestCapture(
            self,
            request_data=request_snapshot,
            source_format=source_format,
            directory=directory,
            started_at=datetime.now().astimezone(),
            input_size=snapshot.used,
            input_truncated=snapshot.truncated,
        )

    def _submit(self, job: _CaptureJob) -> None:
        with self._lock:
            if self._closed:
                self._drop_locked("service_closed")
                return
            if self._queued_records >= MAX_QUEUE_RECORDS or self._queued_bytes + job.size > MAX_QUEUE_BYTES:
                self._drop_locked("queue_full")
                return
            if self._pool is None:
                self._pool = ThreadPool(1)
                self._thread = self._pool.spawn(self._run)
            self._queued_records += 1
            self._queued_bytes += job.size
            self._queue.put((job, job.size))

    def _run(self) -> None:
        try:
            while True:
                job, size = self._queue.get()
                if job is None:
                    break
                try:
                    writer = self._writers.get(job.directory)
                    if writer is None:
                        writer = _ShardWriter(job.directory, self._instance)
                        self._writers[job.directory] = writer
                    writer.write(job)
                except Exception as exc:
                    self._drop("write_failure", error=exc)
                finally:
                    with self._lock:
                        self._queued_records = max(0, self._queued_records - 1)
                        self._queued_bytes = max(0, self._queued_bytes - size)
        finally:
            for writer in self._writers.values():
                try:
                    writer.close()
                except Exception as exc:
                    self._log_limited("writer_close", logging.WARNING, "LLM record writer close failed: %s", exc)

    def _drop_locked(self, reason: str) -> None:
        self._dropped += 1
        dropped = self._dropped
        self._log_limited(
            f"drop:{reason}",
            logging.WARNING,
            "LLM record dropped: reason=%s total_dropped=%s",
            reason,
            dropped,
        )

    def _drop(self, reason: str, *, error: Exception | None = None) -> None:
        with self._lock:
            self._drop_locked(reason)
        if error is not None:
            self._log_limited(
                f"error:{reason}",
                logging.ERROR,
                "LLM record write failed: reason=%s error=%s",
                reason,
                error,
            )

    def _log_limited(self, key: str, level: int, message: str, *args: Any) -> None:
        now = time.monotonic()
        last = self._last_logs.get(key)
        if last is not None and now - last < _LOG_INTERVAL_SECONDS:
            return
        self._last_logs[key] = now
        self._logger.log(level, message, *args)

    def close(self) -> None:
        """停止新提交，等待队列和当前分片完整落盘。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            pool = self._pool
            if thread is not None:
                self._queue.put((None, 0))
        if thread is not None:
            try:
                thread.get()
            except Exception as exc:
                self._logger.error("LLM record writer stopped with an error: %s", exc)
        if pool is not None:
            pool.kill()
        with self._lock:
            self._thread = None
            self._pool = None
            self._writers.clear()
        if self._atexit_registered:
            self._atexit_registered = False
            try:
                atexit.unregister(self.close)
            except Exception:
                pass


__all__ = [
    "LLMRecordingService",
    "LLMRequestCapture",
    "MAX_ACTIVE_BYTES",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_QUEUE_BYTES",
    "MAX_QUEUE_RECORDS",
    "MAX_SHARD_BYTES",
]
