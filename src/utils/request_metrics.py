"""请求与模型测试共用的全程平均输出速度。"""

import math


def output_tps(output_tokens, duration_ms, *, usage_status="known", outcome=None):
    """返回输出 Token / 总秒数及缺失原因，推理 Token 不重复相加。"""
    if outcome in {"error", "cancelled"}:
        return None, "unsuccessful"
    if usage_status not in {"known", "partial"}:
        return None, "missing_usage"
    if output_tokens is None or not math.isfinite(output_tokens) or output_tokens <= 0:
        return None, "no_output"
    if duration_ms is None or not math.isfinite(duration_ms) or duration_ms <= 0:
        return None, "invalid_duration"
    return round(output_tokens * 1000 / duration_ms, 2), None
