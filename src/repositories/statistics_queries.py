"""统计概览查询：日聚合提供历史用量，请求明细提供细粒度趋势与观测样本。"""

from __future__ import annotations

from datetime import datetime, timedelta

# 读取时统一新旧日志的口径；旧文件中的生成阶段 TPS 不参与展示和汇总。
REQUEST_DURATION_SQL = "COALESCE(l.duration_ms, (julianday(l.end_time) - julianday(l.start_time)) * 86400000.0)"
REQUEST_TPS_REASON_SQL = f"""CASE
    WHEN l.outcome IN ('error', 'cancelled') THEN 'unsuccessful'
    WHEN COALESCE(l.usage_status, 'unknown') NOT IN ('known', 'partial') THEN 'missing_usage'
    WHEN COALESCE(l.completion_tokens, 0) <= 0 THEN 'no_output'
    WHEN COALESCE({REQUEST_DURATION_SQL}, 0) <= 0 THEN 'invalid_duration'
    ELSE NULL END"""
REQUEST_TPS_SQL = f"CASE WHEN ({REQUEST_TPS_REASON_SQL}) IS NULL THEN ROUND(l.completion_tokens * 1000.0 / ({REQUEST_DURATION_SQL}), 2) ELSE NULL END"

OUTCOME_COUNTS_SQL = """
    COUNT(*) AS recorded_count,
    COALESCE(SUM(l.outcome = 'success'), 0) AS success_count,
    COALESCE(SUM(l.outcome = 'error'), 0) AS error_count,
    COALESCE(SUM(l.outcome = 'cancelled'), 0) AS cancelled_count,
    COALESCE(SUM(l.outcome IS NULL), 0) AS unknown_count
"""
SUCCESS_RATE_SQL = "SUM(l.outcome = 'success') * 1.0 / NULLIF(SUM(l.outcome IN ('success', 'error')), 0)"

TREND_BUCKETS = {
    "5m": (
        "SUBSTR(l.start_time, 1, 14) || PRINTF('%02d', CAST(SUBSTR(l.start_time, 15, 2) AS INTEGER) / 5 * 5) || ':00'",
        timedelta(minutes=5),
    ),
    "1h": ("SUBSTR(l.start_time, 1, 13) || ':00:00'", timedelta(hours=1)),
    "1d": ("SUBSTR(l.start_time, 1, 10)", timedelta(days=1)),
}


def _trend_interval(start_date, end_date, interval):
    """选择趋势粒度，并限制细粒度查询的时间范围。"""
    days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days + 1
    if interval == "auto":
        return "5m" if days == 1 else "1h" if days <= 7 else "1d"
    if interval not in TREND_BUCKETS:
        raise ValueError("interval must be auto, 5m, 1h or 1d")
    if interval == "5m" and days > 1:
        raise ValueError("5m interval supports a single day")
    if interval == "1h" and days > 7:
        raise ValueError("1h interval supports at most 7 days")
    return interval


def _filters(
    start_date,
    end_date,
    usernames,
    models,
    *,
    daily,
    response_model=None,
    ip_address=None,
    response_column="response_model",
    target_model_id=None,
    target_column="target_model_id",
):
    alias, date = ("d", "d.stat_date") if daily else ("l", "l.start_time")
    clauses, params = [], []
    if start_date:
        clauses.append(f"{date} >= ?")
        params.append(start_date)
    if end_date:
        clauses.append(f"{date} < DATE(?, '+1 day')")
        params.append(end_date)
    for column, values in (
        ("u.username", usernames),
        (f"{alias}.request_model", models),
        (f"{alias}.{target_column}", target_model_id),
        (f"{alias}.{response_column}", response_model),
        (f"{alias}.ip_address", ip_address),
    ):
        if isinstance(values, str):
            values = [values]
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    return " AND ".join(clauses) or "1=1", params


def usage_outcomes(
    conn,
    rows,
    dimension,
    start_date,
    end_date,
    username=None,
    request_model=None,
    *,
    sort_key=None,
    sort_direction=None,
    response_model=None,
    ip_address=None,
    target_model_id=None,
):
    """按相同筛选与维度关联结果样本，保留日聚合用量及仅有明细的分组。"""
    flow = {
        "request_model": "l.request_model",
        "target_model_id": "NULLIF(l.target_model_id, '')",
        "response_model": "NULLIF(l.response_model, '')",
    }
    dimensions = {
        "stats": {"ip_address": "l.ip_address", "username": "COALESCE(u.username, '-')", **flow},
        "model_usage": flow,
        "response_model_usage": {"response_model": "NULLIF(l.response_model, '')"},
        "ip_usage": {"ip_address": "COALESCE(l.ip_address, '未知')", "username": "COALESCE(u.username, '未登记')"},
        "user_usage": {"username": "COALESCE(NULLIF(u.username, ''), l.ip_address, '-')"},
    }
    fields = dimensions[dimension]
    extra_filters = {"response_model": response_model, "ip_address": ip_address, "target_model_id": target_model_id}
    where, params = _filters(start_date, end_date, username, request_model, daily=False, **extra_filters)
    extra = (
        ", COUNT(DISTINCT l.ip_address) AS ip_count, MAX(SUBSTR(l.start_time, 1, 10)) AS last_request_date"
        if dimension == "user_usage"
        else ""
    )
    observed = conn.execute(
        f"SELECT {', '.join(f'{sql} AS {key}' for key, sql in fields.items())}, {OUTCOME_COUNTS_SQL}{extra} "
        f"FROM request_logs l LEFT JOIN users u ON u.ip_address = l.ip_address WHERE {where} "
        f"GROUP BY {', '.join(fields.values())}",
        params,
    ).fetchall()

    def key(row):
        return tuple(row[field] for field in fields)

    metrics = {key(row): dict(row) for row in observed}
    result = []
    for row in rows:
        item = dict(row)
        counts = metrics.pop(key(item), {})
        # 用户的 IP 数与最近请求日期沿用用量汇总；仅补充结果计数。
        item.update(
            {
                name: counts.get(name, 0)
                for name in ("recorded_count", "success_count", "error_count", "cancelled_count", "unknown_count")
            }
        )
        result.append(_rates(item))
    for item in metrics.values():
        result.append(
            _rates(
                {
                    "request_count": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_known_prompt_tokens": 0,
                    "usage_status": "unknown",
                    "cache_usage_status": "unknown",
                    **item,
                }
            )
        )
    if sort_key == "success_rate":
        known = [row for row in result if row["success_rate"] is not None]
        missing = [row for row in result if row["success_rate"] is None]
        result = sorted(known, key=lambda row: row["success_rate"], reverse=sort_direction != "asc") + missing
    return upstream_rates(
        conn,
        result,
        dimension,
        start_date,
        end_date,
        username,
        request_model,
        sort_key=sort_key,
        sort_direction=sort_direction,
        **extra_filters,
    )


def upstream_rates(
    conn,
    rows,
    dimension,
    start_date,
    end_date,
    username=None,
    request_model=None,
    *,
    sort_key=None,
    sort_direction=None,
    response_model=None,
    ip_address=None,
    target_model_id=None,
    series_bucket="SUBSTR(l.start_time, 1, 10)",
):
    """展开真实上游调用，重试各计一次；内部处理与传输异常不进入成功率分母。"""
    flow = {
        "request_model": "l.request_model",
        "target_model_id": "NULLIF(l.target_model_id, '')",
        "response_model": "NULLIF(l.response_model, '')",
    }
    dimensions = {
        "totals": {},
        "models": {"request_model": "l.request_model"},
        "series": {"date": series_bucket},
        "stats": {"ip_address": "l.ip_address", "username": "COALESCE(u.username, '-')", **flow},
        "model_usage": flow,
        "response_model_usage": {"response_model": "NULLIF(l.response_model, '')"},
        "ip_usage": {"ip_address": "COALESCE(l.ip_address, '未知')", "username": "COALESCE(u.username, '未登记')"},
        "user_usage": {"username": "COALESCE(NULLIF(u.username, ''), l.ip_address, '-')"},
        "api_key_usage": {"api_key_id": "l.api_key_id"},
    }
    fields = dimensions[dimension]
    # 按客户端请求的最终映射目标和响应模型筛选，其所有重试仍参与上游结果统计。
    where, params = _filters(
        start_date,
        end_date,
        username,
        request_model,
        daily=False,
        response_model=response_model,
        ip_address=ip_address,
        response_column="request_response_model",
        target_model_id=target_model_id,
        target_column="request_target_model_id",
    )
    if dimension == "api_key_usage":
        where += " AND l.api_key_id IS NOT NULL"
    columns = ", ".join(f"{value} AS {key}" for key, value in fields.items())
    group = f"GROUP BY {', '.join(fields.values())}" if fields else ""
    # 旧调用明细曾省略与请求 ID 相同的目标；最终记录有映射目标时保留该映射阶段。
    observed = conn.execute(
        f"""
        WITH l AS (
            SELECT r.id, r.start_time, r.ip_address, r.api_key_id, r.request_model,
                r.response_model AS request_response_model,
                r.target_model_id AS request_target_model_id,
                CASE WHEN r.upstream_attempts IS NULL OR r.upstream_attempts = '[]' THEN r.target_model_id
                     ELSE COALESCE(NULLIF(json_extract(a.value, '$.target_model_id'), ''),
                         CASE WHEN NULLIF(r.target_model_id, '') IS NOT NULL THEN r.request_model END)
                     END AS target_model_id,
                CASE WHEN r.upstream_attempts IS NULL OR r.upstream_attempts = '[]' THEN r.response_model
                     ELSE json_extract(a.value, '$.response_model') END AS response_model,
                json_extract(a.value, '$.outcome') AS attempt_outcome
            FROM request_logs r, json_each(CASE WHEN r.upstream_attempts IS NULL THEN '[{{"outcome":"unknown"}}]'
                WHEN r.upstream_attempts = '[]' THEN '[{{"outcome":"excluded"}}]' ELSE r.upstream_attempts END) a
        )
        SELECT {columns + "," if columns else ""} COUNT(DISTINCT l.id) AS observed_requests,
            COALESCE(SUM(l.attempt_outcome = 'success'), 0) AS upstream_success_count,
            COALESCE(SUM(l.attempt_outcome = 'error'), 0) AS upstream_error_count,
            COALESCE(SUM(l.attempt_outcome = 'exception'), 0) AS upstream_exception_count,
            COALESCE(SUM(l.attempt_outcome = 'cancelled'), 0) AS upstream_cancelled_count,
            COALESCE(SUM(l.attempt_outcome = 'unknown'), 0) AS upstream_unknown_count
        FROM l LEFT JOIN users u ON u.ip_address = l.ip_address WHERE {where} {group}
    """,
        params,
    ).fetchall()

    def key(row):
        return tuple(row[field] for field in fields)

    metrics = {key(row): dict(row) for row in observed}
    result = []

    def apply(row, counts):
        item = dict(row)
        for field in ("success", "error", "exception", "cancelled", "unknown"):
            name = f"upstream_{field}_count"
            item[name] = counts.get(name, 0)
        # 日用量对应最终流向，重试的实际流向可以不同；缺失历史按最终请求明细覆盖量判断。
        recorded = item.get("recorded_count", counts.get("observed_requests", 0))
        item["upstream_unknown_count"] += max(0, item.get("request_count", 0) - recorded)
        decided = item["upstream_success_count"] + item["upstream_error_count"]
        item["upstream_success_rate"] = item["upstream_success_count"] / decided if decided else None
        return item

    for row in rows:
        result.append(apply(row, metrics.pop(key(row), {})))
    for counts in metrics.values():
        if not counts.get("observed_requests"):
            continue
        result.append(
            apply(
                {
                    "request_count": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_known_prompt_tokens": 0,
                    "usage_status": "unknown",
                    "cache_usage_status": "unknown",
                    "cache_hit_rate": None,
                    "success_count": 0,
                    "error_count": 0,
                    "cancelled_count": 0,
                    "unknown_count": 0,
                    "recorded_count": 0,
                    "success_rate": None,
                    **{field: counts[field] for field in fields},
                },
                counts,
            )
        )
    if sort_key == "upstream_success_rate":
        known = [row for row in result if row["upstream_success_rate"] is not None]
        missing = [row for row in result if row["upstream_success_rate"] is None]
        result = sorted(known, key=lambda row: row["upstream_success_rate"], reverse=sort_direction != "asc") + missing
    return result


def _rates(row):
    result = dict(row)
    result["unknown_count"] = max(
        result.get("unknown_count", 0),
        result.get("request_count", 0)
        - result.get("success_count", 0)
        - result.get("error_count", 0)
        - result.get("cancelled_count", 0),
    )
    decided = result.get("success_count", 0) + result.get("error_count", 0)
    result["success_rate"] = result.get("success_count", 0) / decided if decided else None
    cache_prompt = result.get("cache_known_prompt_tokens", 0)
    result["cache_hit_rate"] = result.get("cache_read_input_tokens", 0) / cache_prompt if cache_prompt else None
    cache_count = result.get("cache_sample_count", 0)
    result["cache_request_hit_rate"] = result.get("cache_hit_count", 0) / cache_count if cache_count else None
    return result


def overview(
    conn,
    start_date,
    end_date,
    username=None,
    request_model=None,
    *,
    response_model=None,
    ip_address=None,
    target_model_id=None,
    interval="1d",
):
    """在同一个读取事务中获取总量、时间趋势、模型表现和 IP 汇总。"""
    interval = _trend_interval(start_date, end_date, interval)
    bucket, step = TREND_BUCKETS[interval]
    extra_filters = {"response_model": response_model, "ip_address": ip_address, "target_model_id": target_model_id}
    daily_where, daily_params = _filters(start_date, end_date, username, request_model, daily=True, **extra_filters)
    log_where, log_params = _filters(start_date, end_date, username, request_model, daily=False, **extra_filters)
    usage = """
        COALESCE(SUM(d.request_count), 0) AS request_count,
        COALESCE(SUM(d.total_tokens), 0) AS total_tokens,
        COALESCE(SUM(d.prompt_tokens), 0) AS prompt_tokens,
        COALESCE(SUM(d.completion_tokens), 0) AS completion_tokens,
        COALESCE(SUM(d.cache_read_input_tokens), 0) AS cache_read_input_tokens,
        COALESCE(SUM(d.cache_known_prompt_tokens), 0) AS cache_known_prompt_tokens,
        CASE WHEN COUNT(*) = 0 OR MAX(d.cache_usage_status) = 'unknown' AND MIN(d.cache_usage_status) = 'unknown'
             THEN 'unknown' WHEN MIN(d.cache_usage_status) = 'known' AND MAX(d.cache_usage_status) = 'known'
             THEN 'known' ELSE 'partial' END AS cache_usage_status
    """
    performance = f"""
        {OUTCOME_COUNTS_SQL},
        COALESCE(SUM(l.cache_usage_status = 'known'), 0) AS cache_sample_count,
        COALESCE(SUM(l.cache_usage_status = 'known' AND l.cache_read_input_tokens > 0), 0) AS cache_hit_count,
        AVG(CASE WHEN l.outcome = 'success' THEN l.first_token_latency_ms END) AS first_token_latency_ms,
        COUNT(CASE WHEN l.outcome = 'success' THEN l.first_token_latency_ms END) AS latency_sample_count,
        AVG(CASE WHEN l.outcome = 'success' THEN {REQUEST_TPS_SQL} END) AS tps,
        COUNT(CASE WHEN l.outcome = 'success' THEN {REQUEST_TPS_SQL} END) AS tps_sample_count,
        AVG(l.duration_ms) AS duration_ms
    """

    def daily(extra="", group=""):
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT {extra}{usage} FROM daily_request_stats d LEFT JOIN users u ON u.ip_address = d.ip_address "
                f"WHERE {daily_where} {group}",
                daily_params,
            ).fetchall()
        ]

    def observed(extra="", group=""):
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT {extra}{performance} FROM request_logs l LEFT JOIN users u ON u.ip_address = l.ip_address "
                f"WHERE {log_where} {group}",
                log_params,
            ).fetchall()
        ]

    def combine(usage_rows, metric_rows, key):
        metrics = {row[key]: row for row in metric_rows}
        result = []
        for row in usage_rows:
            result.append(_rates({**row, **metrics.pop(row[key], {})}))
        # 仅导入请求明细时，也保留其结果样本，不把明细用量再叠加到日聚合。
        result.extend(_rates({"request_count": 0, "total_tokens": 0, **row}) for row in metrics.values())
        return result

    totals = _rates({**daily()[0], **observed()[0]})
    models = combine(
        daily("d.request_model, ", "GROUP BY d.request_model"),
        observed("l.request_model, ", "GROUP BY l.request_model"),
        "request_model",
    )
    models.sort(key=lambda row: (-row.get("request_count", 0), row["request_model"]))
    model_flows = [
        _rates(row)
        for row in daily(
            "d.request_model, NULLIF(d.target_model_id, '') AS target_model_id, "
            "NULLIF(d.response_model, '') AS response_model, ",
            "GROUP BY d.request_model, d.target_model_id, d.response_model "
            "ORDER BY SUM(d.total_tokens) DESC, d.request_model, d.target_model_id, d.response_model",
        )
    ]
    response_models = [
        _rates(row)
        for row in daily(
            "NULLIF(d.response_model, '') AS response_model, ",
            "GROUP BY NULLIF(d.response_model, '') ORDER BY SUM(d.total_tokens) DESC, d.response_model",
        )
    ]
    daily_series = daily("d.stat_date AS date, ", "GROUP BY d.stat_date")
    partial = False
    if interval == "1d":
        series = combine(
            daily_series,
            observed(f"{bucket} AS date, ", f"GROUP BY {bucket}"),
            "date",
        )
    else:
        series = [
            _rates(dict(row))
            for row in conn.execute(
                f"""
                SELECT {bucket} AS date, COUNT(*) AS request_count,
                    COALESCE(SUM(l.total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(l.prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(l.completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(l.cache_read_input_tokens), 0) AS cache_read_input_tokens,
                    COALESCE(SUM(CASE WHEN l.cache_usage_status = 'known' THEN l.prompt_tokens ELSE 0 END), 0)
                        AS cache_known_prompt_tokens,
                    CASE WHEN MIN(l.cache_usage_status) = 'known' AND MAX(l.cache_usage_status) = 'known'
                         THEN 'known' WHEN MIN(l.cache_usage_status) = 'unknown' AND MAX(l.cache_usage_status) = 'unknown'
                         THEN 'unknown' ELSE 'partial' END AS cache_usage_status,
                    {performance}
                FROM request_logs l LEFT JOIN users u ON u.ip_address = l.ip_address
                WHERE {log_where} GROUP BY {bucket}
                """,
                log_params,
            ).fetchall()
        ]
        coverage = {}
        coverage_fields = (
            "request_count",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "cache_read_input_tokens",
            "cache_known_prompt_tokens",
        )
        for row in series:
            day = coverage.setdefault(row["date"][:10], {})
            for field in coverage_fields:
                day[field] = day.get(field, 0) + row[field]
        partial = any(
            row.get(field, 0) > coverage.get(row["date"], {}).get(field, 0)
            for row in daily_series
            for field in coverage_fields
        )
    dates = {row["date"]: row for row in series}
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    if interval != "1d":
        # 当前时间之后尚未发生请求，不把未来时段绘成零用量。
        end = min(end, datetime.now())
    series = []
    while current < end:
        moment = current.strftime("%Y-%m-%d" if interval == "1d" else "%Y-%m-%d %H:%M:%S")
        series.append(
            dates.get(
                moment,
                {"date": moment, "request_count": 0, "total_tokens": 0, "success_rate": None, "cache_hit_rate": None},
            )
        )
        current += step
    ips = [
        _rates(row)
        for row in daily(
            "COALESCE(d.ip_address, '未知') AS ip_address, COALESCE(u.username, '未登记') AS username, ",
            "GROUP BY d.ip_address, u.username ORDER BY SUM(d.total_tokens) DESC",
        )
    ]
    model_flows = usage_outcomes(
        conn, model_flows, "model_usage", start_date, end_date, username, request_model, **extra_filters
    )
    response_models = usage_outcomes(
        conn, response_models, "response_model_usage", start_date, end_date, username, request_model, **extra_filters
    )
    ips = usage_outcomes(conn, ips, "ip_usage", start_date, end_date, username, request_model, **extra_filters)
    totals = upstream_rates(conn, [totals], "totals", start_date, end_date, username, request_model, **extra_filters)[0]
    models = upstream_rates(conn, models, "models", start_date, end_date, username, request_model, **extra_filters)
    series_with_outcomes = {
        row["date"]: row
        for row in upstream_rates(
            conn, series, "series", start_date, end_date, username, request_model, series_bucket=bucket, **extra_filters
        )
    }
    series = [series_with_outcomes[row["date"]] for row in series]
    return {
        "totals": totals,
        "series": series,
        "series_interval": interval,
        "series_partial": partial,
        "models": models,
        "model_flows": model_flows,
        "response_models": response_models,
        "ips": ips,
    }
