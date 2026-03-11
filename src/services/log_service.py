#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志服务。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..application.app_context import AppContext
from ..repositories import LogRepository


class LogService:
    """封装请求日志相关业务逻辑。"""

    def __init__(self, ctx: AppContext, repository: LogRepository):
        self._ctx = ctx
        self._logger = ctx.logger
        self._repository = repository

    def log_request(
        self,
        request_model: str,
        response_model: Optional[str],
        total_tokens: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        ip_address: Optional[str] = None,
    ) -> Optional[int]:
        """记录一次请求日志。"""
        try:
            log_id = self._repository.insert(
                request_model=request_model,
                response_model=response_model,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                start_time=start_time,
                end_time=end_time,
                ip_address=ip_address,
            )
            self._logger.info(
                "Request log saved: id=%s model=%s response_model=%s total_tokens=%s ip=%s",
                log_id,
                request_model,
                response_model,
                total_tokens,
                ip_address,
            )
            return log_id
        except Exception as exc:
            self._logger.error(f"Failed to log request: {exc}")
            return None

    def get_statistics(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        username: Optional[str] = None,
        request_model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取统计聚合数据。"""
        try:
            rows = self._repository.get_statistics(
                start_date,
                end_date,
                username=username,
                request_model=request_model,
            )
            result = [
                {
                    "request_model": row["request_model"],
                    "response_model": row["response_model"],
                    "ip_address": row["ip_address"],
                    "username": row["username"],
                    "request_count": row["request_count"],
                    "total_tokens": row["total_tokens"],
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                }
                for row in rows
            ]
            self._logger.debug(
                "Statistics queried: start_date=%s end_date=%s username=%s rows=%s",
                start_date,
                end_date,
                username,
                len(result),
            )
            return result
        except Exception as exc:
            self._logger.error(f"Failed to get statistics: {exc}")
            return []

    def get_request_logs(
        self,
        page: int = 1,
        page_size: int = 50,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        username: Optional[str] = None,
        request_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取请求日志分页数据。"""
        try:
            result = self._repository.get_logs(
                page,
                page_size,
                start_date,
                end_date,
                username=username,
                request_model=request_model,
            )
            return result
        except Exception as exc:
            self._logger.error(f"Failed to get request logs: {exc}")
            return {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 0,
                "logs": [],
            }

    def get_unique_usernames(self) -> List[str]:
        """获取有日志记录的用户名列表。"""
        try:
            usernames = self._repository.get_unique_usernames()
            self._logger.debug(f"Unique usernames queried: count={len(usernames)}")
            return usernames
        except Exception as exc:
            self._logger.error(f"Failed to get unique usernames: {exc}")
            return []

    def get_unique_request_models(self) -> List[str]:
        """获取有日志记录的请求模型列表。"""
        try:
            models = self._repository.get_unique_request_models()
            self._logger.debug(f"Unique request models queried: count={len(models)}")
            return models
        except Exception as exc:
            self._logger.error(f"Failed to get unique request models: {exc}")
            return []
