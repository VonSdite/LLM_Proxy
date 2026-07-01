#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面与统计控制器。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from io import BytesIO
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from flask import jsonify, make_response, redirect, render_template, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, LogService, SettingsService
from .controller_utils import require_json_object
from .decorators import require_authentication


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
        self._app.route("/api-keys")(auth(self.api_keys_page))
        self._app.route("/users")(auth(self.users_page))
        self._app.route("/statistics")(auth(self.statistics_page))
        self._app.route("/settings")(auth(self.settings_page))

        self._app.route("/api/statistics", methods=["GET"])(auth(self.get_statistics))
        self._app.route("/api/statistics/user-usage-summary", methods=["GET"])(auth(self.get_user_usage_summary))
        self._app.route("/api/statistics/export", methods=["GET"])(auth(self.export_statistics))
        self._app.route("/api/statistics/daily-stats/export", methods=["GET"])(auth(self.export_daily_stats))
        self._app.route("/api/statistics/daily-stats/import", methods=["POST"])(auth(self.import_daily_stats))
        self._app.route("/api/request-logs", methods=["GET"])(auth(self.get_request_logs))
        self._app.route("/api/usernames", methods=["GET"])(auth(self.get_usernames))
        self._app.route("/api/request-models", methods=["GET"])(auth(self.get_request_models))
        self._app.route("/api/settings/system", methods=["GET"])(auth(self.get_system_settings))
        self._app.route("/api/settings/system", methods=["PUT"])(auth(self.update_system_settings))
        self._app.route("/api/settings/system/basic", methods=["PUT"])(auth(self.update_basic_settings))
        self._app.route("/api/settings/system/client-ip", methods=["PUT"])(auth(self.update_client_ip_settings))
        self._app.route("/api/settings/system/debug", methods=["PUT"])(auth(self.update_debug_settings))
        self._app.route("/api/settings/system/oauth", methods=["PUT"])(auth(self.update_oauth_settings))
        self._app.route("/api/settings/system/api-keys", methods=["PUT"])(auth(self.update_api_key_settings))

    def home(self) -> str:
        return self.providers_page()

    def statistics_page(self) -> str:
        return render_template(
            "index.html",
            active_page="index",
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
            oauth_enabled=self._is_oauth_enabled(),
            api_key_management_enabled=self._is_api_key_management_enabled(),
        )

    def users_page(self) -> str:
        return render_template(
            "users.html",
            active_page="users",
            chat_whitelist_enabled=self._config_manager.is_chat_whitelist_enabled(),
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
            oauth_enabled=self._is_oauth_enabled(),
            api_key_management_enabled=self._is_api_key_management_enabled(),
        )

    def oauth_page(self) -> str:
        return render_template(
            "oauth.html",
            active_page="oauth",
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
            oauth_enabled=self._is_oauth_enabled(),
            api_key_management_enabled=self._is_api_key_management_enabled(),
        )

    def api_keys_page(self) -> ResponseReturnValue:
        if not self._is_api_key_management_enabled():
            return redirect("/settings")
        return render_template(
            "api_keys.html",
            active_page="api_keys",
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
            oauth_enabled=self._is_oauth_enabled(),
            api_key_management_enabled=self._is_api_key_management_enabled(),
        )

    def providers_page(self) -> str:
        return render_template(
            "providers.html",
            active_page="providers",
            chat_whitelist_enabled=self._config_manager.is_chat_whitelist_enabled(),
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
            oauth_enabled=self._is_oauth_enabled(),
            api_key_management_enabled=self._is_api_key_management_enabled(),
        )

    def settings_page(self) -> str:
        return render_template(
            "settings.html",
            active_page="settings",
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
            oauth_enabled=self._is_oauth_enabled(),
            api_key_management_enabled=self._is_api_key_management_enabled(),
        )

    def _get_current_username(self) -> str:
        if not self._auth_service.is_auth_enabled():
            return ""

        session_token = request.cookies.get("session_token")
        return self._auth_service.get_session_username(session_token) or ""

    def _is_oauth_enabled(self) -> bool:
        if self._config_manager is None:
            return False
        return self._config_manager.is_oauth_enabled()

    def _is_api_key_management_enabled(self) -> bool:
        if self._config_manager is None:
            return False
        read_enabled = getattr(self._config_manager, "is_api_key_management_enabled", None)
        if read_enabled is None:
            return False
        return bool(read_enabled())

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
            )
            return jsonify(summary)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting user usage summary: %s", exc)
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

            if tab == "user_usage":
                data = self._log_service.get_user_usage_summary(
                    request.args.get("start_date"),
                    request.args.get("end_date"),
                    usernames or None,
                    request_models or None,
                    sort_key=sort_key,
                    sort_direction=sort_direction,
                )
                headers = ["用户名", "请求数", "输入token", "输出token", "总 Token", "关联 IP 数", "最近请求日期"]
                rows = [
                    [
                        item["username"],
                        item["request_count"],
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
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
                )
                headers = [
                    "IP",
                    "用户名",
                    "请求模型",
                    "响应模型",
                    "输入token",
                    "输出token",
                    "总 Token",
                    "开始时间",
                    "结束时间",
                    "耗时(秒)",
                ]
                rows = [
                    [
                        item["ip_address"],
                        item["username"],
                        item["request_model"],
                        item["response_model"],
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
                        item["start_time"],
                        item["end_time"],
                        self._calculate_log_duration(item["start_time"], item["end_time"]),
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
                )
                headers = ["IP", "用户名", "请求模型", "响应模型", "输入token", "输出token", "总 Token", "请求数"]
                rows = [
                    [
                        item["ip_address"],
                        item["username"],
                        item["request_model"],
                        item["response_model"],
                        item["prompt_tokens"],
                        item["completion_tokens"],
                        item["total_tokens"],
                        item["request_count"],
                    ]
                    for item in data
                ]
                sheet_name = "调用汇总"
                file_prefix = "call-summary"

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
            if start_date or end_date:
                self._validate_dashboard_date_range(start_date, end_date)
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            result = self._log_service.export_daily_stats(
                start_date,
                end_date,
                usernames or None,
                request_models or None,
            )
            self._logger.debug(
                "Daily stats JSON exported: rows=%s",
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
                "Daily stats JSON imported: count=%s inserted=%s updated=%s",
                result.get("count", 0),
                result.get("inserted_count", 0),
                result.get("updated_count", 0),
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
            )
            return jsonify(logs)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error getting request logs: %s", exc)
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
        if result.get("auth_config_changed"):
            self._auth_service.clear_sessions()

        response = make_response(jsonify(result))
        if result.get("auth_config_changed"):
            response.delete_cookie("session_token")
        return response

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
