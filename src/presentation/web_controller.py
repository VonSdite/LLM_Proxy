#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面与统计控制器。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from io import BytesIO
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from flask import jsonify, make_response, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, LogService, SettingsService
from .controller_utils import require_json_object
from .decorators import require_authentication
from .ui import render_ui


class WebController:
    """处理页面渲染与统计相关 API。"""

    def __init__(
        self,
        ctx: AppContext,
        log_service: LogService,
        settings_service: SettingsService,
        auth_service: AuthenticationService,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._log_service = log_service
        self._settings_service = settings_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)

        self._app.route("/")(auth(self.home))
        self._app.route("/providers")(auth(self.providers_page))
        self._app.route("/oauth")(auth(self.oauth_page))
        self._app.route("/model-mappings")(auth(self.model_mappings_page))
        self._app.route("/api-keys")(auth(self.api_keys_page))
        self._app.route("/users")(auth(self.users_page))
        self._app.route("/statistics")(auth(self.statistics_page))
        self._app.route("/settings")(auth(self.settings_page))
        self._app.route("/extensions/<page_id>")(auth(self.extension_page))

        self._app.route("/api/statistics", methods=["GET"])(auth(self.get_statistics))
        self._app.route("/api/statistics/overview", methods=["GET"])(auth(self.get_statistics_overview))
        self._app.route("/api/statistics/user-usage-summary", methods=["GET"])(auth(self.get_user_usage_summary))
        self._app.route("/api/statistics/api-key-usage-summary", methods=["GET"])(auth(self.get_api_key_usage_summary))
        self._app.route("/api/statistics/export", methods=["GET"])(auth(self.export_statistics))
        self._app.route("/api/statistics/daily-stats/export", methods=["GET"])(auth(self.export_daily_stats))
        self._app.route("/api/statistics/daily-stats/import", methods=["POST"])(auth(self.import_daily_stats))
        self._app.route("/api/request-logs", methods=["GET"])(auth(self.get_request_logs))
        self._app.route("/api/usernames", methods=["GET"])(auth(self.get_usernames))
        self._app.route("/api/request-models", methods=["GET"])(auth(self.get_request_models))
        self._app.route("/api/statistics/filter-options", methods=["GET"])(auth(self.get_filter_options))
        self._app.route("/api/settings/system", methods=["GET"])(auth(self.get_system_settings))
        self._app.route("/api/settings/system", methods=["PUT"])(auth(self.update_system_settings))
        self._app.route("/api/settings/system/basic", methods=["PUT"])(auth(self.update_basic_settings))
        self._app.route("/api/settings/system/client-ip", methods=["PUT"])(auth(self.update_client_ip_settings))
        self._app.route("/api/settings/system/debug", methods=["PUT"])(auth(self.update_debug_settings))
        self._app.route("/api/settings/system/llm-recording", methods=["PUT"])(auth(self.update_llm_recording_settings))
        self._app.route("/api/settings/system/oauth", methods=["PUT"])(auth(self.update_oauth_settings))
        self._app.route("/api/settings/system/api-keys", methods=["PUT"])(auth(self.update_api_key_settings))
        self._app.route("/api/settings/system/model-mapping", methods=["PUT"])(auth(self.update_model_mapping_settings))

    def home(self) -> ResponseReturnValue:
        return self.providers_page()

    def statistics_page(self) -> ResponseReturnValue:
        return render_ui()

    def users_page(self) -> ResponseReturnValue:
        return render_ui()

    def oauth_page(self) -> ResponseReturnValue:
        return render_ui()

    def model_mappings_page(self) -> ResponseReturnValue:
        return render_ui()

    def api_keys_page(self) -> ResponseReturnValue:
        return render_ui()

    def providers_page(self) -> ResponseReturnValue:
        return render_ui()

    def settings_page(self) -> ResponseReturnValue:
        return render_ui()

    def extension_page(self, page_id: str) -> ResponseReturnValue:
        """扩展页面复用统一 Vue 入口，路径由前端注册表解析。"""
        del page_id
        return render_ui()

    @staticmethod
    def _get_multi_filter_values(name: str) -> list[str]:
        return [value.strip() for value in request.args.getlist(name) if isinstance(value, str) and value.strip()]

    @staticmethod
    def _parse_dashboard_date(value: str | None, field_name: str) -> datetime:
        """解析统计筛选日期。"""
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("start_date and end_date are required")

        try:
            return datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{field_name} must use YYYY-MM-DD") from exc

    @classmethod
    def _validate_dashboard_date_range(
        cls,
        start_date: str | None,
        end_date: str | None,
    ) -> None:
        """校验统计查询日期范围，避免无边界或超大范围查询。"""
        start = cls._parse_dashboard_date(start_date, "start_date")
        end = cls._parse_dashboard_date(end_date, "end_date")

        if end < start:
            raise ValueError("end_date must be on or after start_date")

        try:
            max_end = start.replace(year=start.year + 1)
        except ValueError:
            max_end = start.replace(year=start.year + 1, day=28)
        if end > max_end:
            raise ValueError("date range must not exceed one year")

    @staticmethod
    def _column_name(index: int) -> str:
        """把从 1 开始的列号转换为 Excel 列名。"""
        name = ""
        value = index
        while value > 0:
            value, remainder = divmod(value - 1, 26)
            name = chr(65 + remainder) + name
        return name

    @staticmethod
    def _clean_excel_text(value: Any) -> str:
        """清理 XML 1.0 不支持的控制字符。"""
        text = str(value if value is not None else "")
        return "".join(char for char in text if char in {"\t", "\n", "\r"} or ord(char) >= 32)

    @classmethod
    def _build_sheet_xml(cls, headers: list[str], rows: list[list[Any]]) -> str:
        """构造单工作表 XML。"""
        xml_rows = []
        for row_index, row_values in enumerate([headers, *rows], start=1):
            cells = []
            for column_index, value in enumerate(row_values, start=1):
                cell_ref = f"{cls._column_name(column_index)}{row_index}"
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cells.append(f'<c r="{cell_ref}"><v>{value}</v></c>')
                    continue

                text = escape(cls._clean_excel_text(value))
                cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>')
            xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheetData>{''.join(xml_rows)}</sheetData>"
            "</worksheet>"
        )

    @classmethod
    def _build_xlsx(cls, sheet_name: str, headers: list[str], rows: list[list[Any]]) -> bytes:
        """用标准库生成最小 xlsx 文件。"""
        safe_sheet_name = escape(sheet_name[:31] or "Sheet1")
        sheet_xml = cls._build_sheet_xml(headers, rows)
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
            )
            archive.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="xl/workbook.xml"/>
</Relationships>""",
            )
            archive.writestr(
                "xl/workbook.xml",
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{safe_sheet_name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
 Target="worksheets/sheet1.xml"/>
</Relationships>""",
            )
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        return output.getvalue()

    @staticmethod
    def _dashboard_export_response(filename: str, content: bytes) -> ResponseReturnValue:
        response = make_response(content)
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}; filename*=UTF-8''{quote(filename)}"
        return response

    @staticmethod
    def _parse_log_time(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def _calculate_log_duration(cls, start_time: Any, end_time: Any) -> float:
        start = cls._parse_log_time(start_time)
        end = cls._parse_log_time(end_time)
        if start is None or end is None:
            return 0
        return round(max((end - start).total_seconds(), 0), 2)

    @staticmethod
    def _format_usage_status(value: Any) -> str:
        """将 token 采集状态转换为管理端展示文本。"""
        return {
            "known": "完整",
            "partial": "部分",
            "unknown": "未知",
        }.get(str(value or "unknown").strip().lower(), "未知")

    @staticmethod
    def _format_cache_hit_rate(value: Any) -> str:
        """将缓存 Token 命中率转换为百分比文本。"""
        if value is None:
            return "未知"
        try:
            return f"{max(float(value), 0) * 100:.2f}%"
        except (TypeError, ValueError):
            return "未知"

    @staticmethod
    def _format_tps_unavailable_reason(item: dict[str, Any]) -> str:
        if item.get("tps") is not None:
            return ""
        return {
            "missing_usage": "缺少有效输出 Token 用量",
            "no_output": "没有正数的输出 Token 用量",
            "invalid_duration": "缺少有效请求总耗时",
            "unsuccessful": "请求失败或取消",
        }.get(item.get("tps_unavailable_reason"), "缺少有效输出用量或总耗时")

    @staticmethod
    def _format_request_model(item: dict[str, Any]) -> str:
        """请求列展示原始模型及映射目标，响应模型独立成列。"""
        parts = [
            str(item.get("request_model") or "").strip(),
            str(item.get("target_model_id") or "").strip(),
        ]
        return " -> ".join(part for part in parts if part)

    def get_statistics_overview(self) -> ResponseReturnValue:
        try:
            self._validate_dashboard_date_range(request.args.get("start_date"), request.args.get("end_date"))
            return jsonify(
                self._log_service.get_overview(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    self._get_multi_filter_values("username") or None,
                    self._get_multi_filter_values("request_model") or None,
                    response_model=self._get_multi_filter_values("response_model") or None,
                    ip_address=self._get_multi_filter_values("ip_address") or None,
                )
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting statistics overview: %s", exc)
            return jsonify({"error": "Unable to load statistics overview"}), 500

    def get_statistics(self) -> ResponseReturnValue:
        try:
            self._validate_dashboard_date_range(
                request.args.get("start_date"),
                request.args.get("end_date"),
            )
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            self._logger.debug(
                "Statistics queried: start_date=%s end_date=%s usernames=%s request_models=%s",
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames,
                request_models,
            )
            stats = self._log_service.get_statistics(
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames or None,
                request_models or None,
                sort_key=request.args.get("sort_key"),
                sort_direction=request.args.get("sort_direction"),
                response_model=self._get_multi_filter_values("response_model") or None,
                ip_address=self._get_multi_filter_values("ip_address") or None,
            )
            return jsonify(stats)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting statistics: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_user_usage_summary(self) -> ResponseReturnValue:
        try:
            self._validate_dashboard_date_range(
                request.args.get("start_date"),
                request.args.get("end_date"),
            )
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            self._logger.debug(
                "User usage summary queried: start_date=%s end_date=%s usernames=%s request_models=%s",
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames,
                request_models,
            )
            summary = self._log_service.get_user_usage_summary(
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames or None,
                request_models or None,
                sort_key=request.args.get("sort_key"),
                sort_direction=request.args.get("sort_direction"),
                response_model=self._get_multi_filter_values("response_model") or None,
                ip_address=self._get_multi_filter_values("ip_address") or None,
            )
            return jsonify(summary)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting user usage summary: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_api_key_usage_summary(self) -> ResponseReturnValue:
        try:
            self._validate_dashboard_date_range(
                request.args.get("start_date"),
                request.args.get("end_date"),
            )
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            self._logger.debug(
                "API key usage summary queried: start_date=%s end_date=%s usernames=%s request_models=%s",
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames,
                request_models,
            )
            summary = self._log_service.get_api_key_usage_summary(
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames or None,
                request_models or None,
                sort_key=request.args.get("sort_key"),
                sort_direction=request.args.get("sort_direction"),
                response_model=self._get_multi_filter_values("response_model") or None,
                ip_address=self._get_multi_filter_values("ip_address") or None,
            )
            return jsonify(summary)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting API key usage summary: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def export_statistics(self) -> ResponseReturnValue:
        try:
            self._validate_dashboard_date_range(
                request.args.get("start_date"),
                request.args.get("end_date"),
            )
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            tab = str(request.args.get("tab") or "stats").strip()
            sort_key = request.args.get("sort_key")
            sort_direction = request.args.get("sort_direction")

            if tab in {"models", "model_usage", "response_model_usage", "ip_usage"}:
                overview = self._log_service.get_overview(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    usernames or None,
                    request_models or None,
                    response_model=self._get_multi_filter_values("response_model") or None,
                    ip_address=self._get_multi_filter_values("ip_address") or None,
                )
                if tab == "models":
                    data = overview["models"]
                    columns = [
                        ("request_model", "请求模型"),
                        ("request_count", "请求数"),
                        ("upstream_success_count", "上游成功"),
                        ("upstream_error_count", "上游失败"),
                        ("upstream_exception_count", "上游异常"),
                        ("upstream_cancelled_count", "中止"),
                        ("upstream_unknown_count", "未知调用结果"),
                        ("upstream_success_rate", "上游成功率"),
                        ("cache_hit_rate", "缓存 Token 命中率"),
                        ("cache_request_hit_rate", "缓存请求命中率"),
                        ("first_token_latency_ms", "平均首字延迟(ms)"),
                        ("latency_sample_count", "首字样本数"),
                        ("tps", "平均 TPS(tok/s)"),
                        ("tps_sample_count", "TPS 样本数"),
                        ("total_tokens", "总 Token"),
                    ]
                    sheet_name, file_prefix = "模型表现", "model-performance"
                elif tab in {"model_usage", "response_model_usage"}:
                    data = (
                        [{**item, "model_flow": self._format_request_model(item)} for item in overview["model_flows"]]
                        if tab == "model_usage"
                        else overview["response_models"]
                    )
                    columns = [
                        *([("model_flow", "请求模型")] if tab == "model_usage" else []),
                        ("response_model", "响应模型"),
                        ("prompt_tokens", "输入 Token"),
                        ("completion_tokens", "输出 Token"),
                        ("total_tokens", "总 Token"),
                        ("cache_read_input_tokens", "缓存读取 Token"),
                        ("cache_hit_rate", "缓存命中率"),
                        ("request_count", "请求数"),
                    ]
                    sheet_name, file_prefix = (
                        ("请求模型用量", "model-usage")
                        if tab == "model_usage"
                        else ("响应模型用量", "response-model-usage")
                    )
                else:
                    data = overview["ips"]
                    columns = [
                        ("ip_address", "IP"),
                        ("username", "用户名"),
                        ("request_count", "请求数"),
                        ("prompt_tokens", "输入 Token"),
                        ("completion_tokens", "输出 Token"),
                        ("total_tokens", "总 Token"),
                        ("cache_read_input_tokens", "缓存读取 Token"),
                        ("cache_hit_rate", "缓存命中率"),
                    ]
                    sheet_name, file_prefix = "IP 用量", "ip-usage"
                if sort_key in {key for key, _ in columns} | {"upstream_success_rate"}:
                    present = [row for row in data if row.get(sort_key) is not None]
                    missing = [row for row in data if row.get(sort_key) is None]
                    data = sorted(present, key=lambda row: row[sort_key], reverse=sort_direction != "asc") + missing
                headers = [label for _, label in columns]
                rows = [
                    [
                        "未知"
                        if key == "response_model" and not item.get(key)
                        else "—"
                        if key == "upstream_success_rate" and item.get(key) is None
                        else self._format_cache_hit_rate(item.get(key))
                        if key.endswith("_rate")
                        else item.get(key)
                        for key, _ in columns
                    ]
                    for item in data
                ]
            elif tab == "api_key_usage":
                data = self._log_service.get_api_key_usage_summary(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    usernames or None,
                    request_models or None,
                    sort_key=sort_key,
                    sort_direction=sort_direction,
                    response_model=self._get_multi_filter_values("response_model") or None,
                    ip_address=self._get_multi_filter_values("ip_address") or None,
                )
                headers = [
                    "KEY 名称",
                    "KEY",
                    "状态",
                    "输入token",
                    "输出token",
                    "总 Token",
                    "Token 状态",
                    "缓存读取 Token",
                    "缓存写入 Token",
                    "缓存命中率",
                    "请求数",
                ]
                status_labels = {
                    "enabled": "启用",
                    "disabled": "停用",
                    "deleted": "已删除",
                }
                rows = [
                    [
                        item["key_name"],
                        item["key_preview"],
                        status_labels.get(str(item.get("status") or ""), "未知"),
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
                        self._format_usage_status(item.get("usage_status")),
                        item["cache_read_input_tokens"],
                        item["cache_creation_input_tokens"],
                        self._format_cache_hit_rate(item.get("cache_hit_rate")),
                        item["request_count"],
                    ]
                    for item in data
                ]
                sheet_name = "KEY 用量"
                file_prefix = "api-key-usage"
            elif tab == "user_usage":
                data = self._log_service.get_user_usage_summary(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    usernames or None,
                    request_models or None,
                    sort_key=sort_key,
                    sort_direction=sort_direction,
                    response_model=self._get_multi_filter_values("response_model") or None,
                    ip_address=self._get_multi_filter_values("ip_address") or None,
                )
                headers = [
                    "用户名",
                    "请求数",
                    "输入token",
                    "输出token",
                    "总 Token",
                    "Token 状态",
                    "缓存读取 Token",
                    "缓存写入 Token",
                    "缓存命中率",
                    "关联 IP 数",
                    "最近请求日期",
                ]
                rows = [
                    [
                        item["username"],
                        item["request_count"],
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
                        self._format_usage_status(item.get("usage_status")),
                        item["cache_read_input_tokens"],
                        item["cache_creation_input_tokens"],
                        self._format_cache_hit_rate(item.get("cache_hit_rate")),
                        item["ip_count"],
                        item["last_request_date"],
                    ]
                    for item in data
                ]
                sheet_name = "用户用量"
                file_prefix = "user-usage"
            elif tab == "logs":
                data = self._log_service.get_all_request_logs(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    usernames or None,
                    request_models or None,
                    sort_key=sort_key,
                    sort_direction=sort_direction,
                    response_model=self._get_multi_filter_values("response_model") or None,
                    ip_address=self._get_multi_filter_values("ip_address") or None,
                )
                headers = [
                    "IP",
                    "用户名",
                    "请求模型",
                    "响应模型",
                    "输入token",
                    "输出token",
                    "总 Token",
                    "Token 状态",
                    "缓存读取 Token",
                    "缓存写入 Token",
                    "缓存命中率",
                    "开始时间",
                    "结束时间",
                    "耗时(秒)",
                    "结果",
                    "HTTP 状态",
                    "错误类型",
                    "首字延迟(ms)",
                    "TPS(tok/s)",
                    "流式响应",
                    "TPS 缺失原因",
                ]
                rows = [
                    [
                        item["ip_address"],
                        item["username"],
                        self._format_request_model(item),
                        item.get("response_model") or "未知",
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
                        self._format_usage_status(item.get("usage_status")),
                        item["cache_read_input_tokens"],
                        item["cache_creation_input_tokens"],
                        self._format_cache_hit_rate(item.get("cache_hit_rate")),
                        item["start_time"],
                        item["end_time"],
                        item["duration_ms"] / 1000
                        if item.get("duration_ms") is not None
                        else self._calculate_log_duration(item["start_time"], item["end_time"]),
                        {"success": "成功", "error": "失败", "cancelled": "取消"}.get(item.get("outcome"), "未知"),
                        item.get("http_status"),
                        item.get("error_type"),
                        item.get("first_token_latency_ms"),
                        item.get("tps"),
                        item.get("is_stream"),
                        self._format_tps_unavailable_reason(item),
                    ]
                    for item in data
                ]
                sheet_name = "请求明细"
                file_prefix = "request-logs"
            else:
                data = self._log_service.get_statistics(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    usernames or None,
                    request_models or None,
                    sort_key=sort_key,
                    sort_direction=sort_direction,
                    response_model=self._get_multi_filter_values("response_model") or None,
                    ip_address=self._get_multi_filter_values("ip_address") or None,
                )
                headers = [
                    "IP",
                    "用户名",
                    "请求模型",
                    "响应模型",
                    "输入token",
                    "输出token",
                    "总 Token",
                    "Token 状态",
                    "缓存读取 Token",
                    "缓存写入 Token",
                    "缓存命中率",
                    "请求数",
                ]
                rows = [
                    [
                        item["ip_address"],
                        item["username"],
                        self._format_request_model(item),
                        item.get("response_model") or "未知",
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
                        self._format_usage_status(item.get("usage_status")),
                        item["cache_read_input_tokens"],
                        item["cache_creation_input_tokens"],
                        self._format_cache_hit_rate(item.get("cache_hit_rate")),
                        item["request_count"],
                    ]
                    for item in data
                ]
                sheet_name = "来源与模型"
                file_prefix = "call-summary"

            if tab in {"stats", "model_usage", "response_model_usage", "ip_usage", "user_usage", "api_key_usage"}:
                index = headers.index("请求数") + 1
                headers[index:index] = ["上游成功率", "上游成功", "上游失败", "上游异常", "中止", "未知调用结果"]
                for row, item in zip(rows, data):
                    rate = item.get("upstream_success_rate")
                    row[index:index] = [
                        self._format_cache_hit_rate(rate) if rate is not None else "—",
                        *(
                            item.get(key, 0)
                            for key in (
                                "upstream_success_count",
                                "upstream_error_count",
                                "upstream_exception_count",
                                "upstream_cancelled_count",
                                "upstream_unknown_count",
                            )
                        ),
                    ]
            content = self._build_xlsx(sheet_name, headers, rows)
            filename = f"{file_prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
            self._logger.debug("Statistics exported: tab=%s rows=%s filename=%s", tab, len(rows), filename)
            return self._dashboard_export_response(filename, content)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error exporting statistics: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def export_daily_stats(self) -> ResponseReturnValue:
        try:
            start_date = request.args.get("start_date")
            end_date = request.args.get("end_date")
            self._validate_dashboard_date_range(start_date, end_date)
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            result = self._log_service.export_daily_stats(
                start_date,
                end_date,
                usernames or None,
                request_models or None,
                response_model=self._get_multi_filter_values("response_model") or None,
                ip_address=self._get_multi_filter_values("ip_address") or None,
            )
            self._logger.debug(
                "Statistics JSON exported: request_logs=%s daily_request_stats=%s",
                len(result.get("request_logs", [])),
                len(result.get("daily_request_stats", [])),
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error exporting daily stats: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def import_daily_stats(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            result = self._log_service.import_daily_stats(payload)
            self._logger.info(
                "Statistics JSON imported: count=%s request_logs_inserted=%s request_logs_skipped=%s "
                "daily_request_stats_inserted=%s daily_request_stats_merged=%s",
                result.get("count", 0),
                result.get("request_logs_inserted_count", 0),
                result.get("request_logs_skipped_count", 0),
                result.get("daily_request_stats_inserted_count", 0),
                result.get("daily_request_stats_merged_count", 0),
            )
            return jsonify(result), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error importing daily stats: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_request_logs(self) -> ResponseReturnValue:
        try:
            page = max(int(request.args.get("page", 1)), 1)
            page_size = min(max(int(request.args.get("page_size", 50)), 1), 200)
        except ValueError:
            return jsonify({"error": "page and page_size must be integers"}), 400

        try:
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            self._validate_dashboard_date_range(
                request.args.get("start_date"),
                request.args.get("end_date"),
            )
            self._logger.debug("Request logs queried: page=%s, page_size=%s", page, page_size)

            logs = self._log_service.get_request_logs(
                page,
                page_size,
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames or None,
                request_models or None,
                sort_key=request.args.get("sort_key"),
                sort_direction=request.args.get("sort_direction"),
                response_model=self._get_multi_filter_values("response_model") or None,
                ip_address=self._get_multi_filter_values("ip_address") or None,
            )
            return jsonify(logs)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting request logs: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_filter_options(self) -> ResponseReturnValue:
        try:
            return jsonify(self._log_service.get_filter_options())
        except Exception as exc:
            self._logger.error("Error getting statistics filter options: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_usernames(self) -> ResponseReturnValue:
        try:
            usernames = self._log_service.get_unique_usernames()
            self._logger.debug("Usernames queried: count=%s", len(usernames))
            return jsonify(usernames)
        except Exception as exc:
            self._logger.error("Error getting usernames: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_request_models(self) -> ResponseReturnValue:
        try:
            models = self._log_service.get_unique_request_models()
            self._logger.debug("Request models queried: count=%s", len(models))
            return jsonify(models)
        except Exception as exc:
            self._logger.error("Error getting request models: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_system_settings(self) -> ResponseReturnValue:
        try:
            settings = self._settings_service.get_system_settings()
            return jsonify(settings)
        except Exception as exc:
            self._logger.error("Error getting system settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def _apply_settings_update(
        self,
        update_func: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> ResponseReturnValue:
        payload = require_json_object()
        result = update_func(payload)
        if result.get("server_listener_changed"):
            result["server_access_url"] = self._listener_access_url(result["settings"]["server"])
        if result.get("auth_config_changed"):
            self._auth_service.clear_sessions()

        response = make_response(jsonify(result))
        if result.get("auth_config_changed"):
            response.delete_cookie("session_token")
        return response

    @staticmethod
    def _listener_access_url(server: dict[str, Any]) -> str | None:
        """仅对可识别的直接访问生成入口，不推测代理或端口映射。"""
        if request.scheme != "http" or any(
            name in request.headers
            for name in (
                "Forwarded",
                "X-Forwarded-Host",
                "X-Forwarded-Port",
                "X-Forwarded-Proto",
                "X-Forwarded-For",
                "X-Real-IP",
            )
        ):
            return None
        try:
            origin = urlsplit(request.host_url)
            current_host = origin.hostname or ""
            if (origin.port or 80) != int(request.environ.get("SERVER_PORT", 0)):
                return None
            current_ip = ip_address("127.0.0.1" if current_host == "localhost" else current_host)
            target_ip = ip_address(server["host"])
        except ValueError:
            return None
        if target_ip.is_loopback and not current_ip.is_loopback:
            return None
        if target_ip.is_unspecified and target_ip.version != current_ip.version:
            return None
        target_host = current_host if target_ip.is_unspecified else str(target_ip)
        if ":" in target_host:
            target_host = f"[{target_host}]"
        return f"http://{target_host}:{server['port']}/settings"

    def update_system_settings(self) -> ResponseReturnValue:
        try:
            return self._apply_settings_update(self._settings_service.update_system_settings)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating system settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_basic_settings(self) -> ResponseReturnValue:
        try:
            return self._apply_settings_update(self._settings_service.update_basic_settings)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating basic settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_client_ip_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_client_ip_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating client IP settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_debug_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_debug_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating debug settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_llm_recording_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_llm_recording_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating LLM recording settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_oauth_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_oauth_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating OAuth settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_api_key_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_api_key_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating API key settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_model_mapping_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_model_mapping_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating model mapping settings: %s", exc)
            return jsonify({"error": str(exc)}), 500
