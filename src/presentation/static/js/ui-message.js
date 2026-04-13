(function () {
    const TOAST_CONTAINER_ID = "ui-toast-container";
    const CHINESE_TEXT_PATTERN = /[\u4e00-\u9fff]/;
    const EXACT_ERROR_MESSAGE_MAP = new Map([
        ["unauthorized", "登录状态已失效，请重新登录"],
        ["username and password are required", "请填写用户名和密码"],
        ["invalid username or password", "用户名或密码错误"],
        ["provider not found", "Provider 不存在"],
        ["user not found", "用户不存在"],
        ["username is required", "请填写用户名"],
        ["ip address is required", "请填写 IP 地址"],
        ["invalid ip address", "IP 地址格式无效"],
        ["failed to create user or ip already exists", "创建用户失败，用户名或 IP 可能已存在"],
        ["failed to update user", "更新用户失败"],
        ["failed to delete user", "删除用户失败"],
        ["provider api must be a valid absolute url", "API 地址必须是完整 URL"],
        ["provider proxy must be a valid absolute url", "代理地址必须是完整 URL"],
        ["websocket upstream currently only supports http/https proxy", "WebSocket 上游当前仅支持 HTTP/HTTPS 代理"],
        ["model_list must be a list or newline-separated string", "模型列表格式无效，请使用数组或按行分隔的文本"],
        ["model test models must be a non-empty list", "请至少选择一个要测试的模型"],
        ["model test must use either auth_group or api_key, not both", "模型测试时 Auth Group 与 API Key 只能二选一"],
        ["model test auth_entry_id requires auth_group", "模型测试时只有选择了 Auth Group 才能指定 Auth Entry"],
        ["model test auth_group requires auth_entry_id", "模型测试时请选择一个 Auth Entry"],
        ["model fetch auth_entry_id requires auth_group", "拉取模型时只有选择了 Auth Group 才能指定 Auth Entry"],
        ["model fetch auth_group requires auth_entry_id", "拉取模型时请选择一个 Auth Entry"],
        ["auth entry id is required", "请选择一个 Auth Entry"],
        ["server host is required", "请填写监听 IP"],
        ["server host must be a valid ip address", "监听 IP 格式无效，请填写合法 IP 地址"],
        ["server port must be an integer", "监听端口必须是整数"],
        ["server port must be between 1 and 65535", "监听端口必须在 1 到 65535 之间"],
        ["log path is required", "请填写日志路径"],
    ]);

    function normalizeText(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
    }

    function containsChineseText(value) {
        return CHINESE_TEXT_PATTERN.test(String(value || ""));
    }

    function extractErrorText(errorLike) {
        if (!errorLike) {
            return "";
        }
        if (typeof errorLike === "string") {
            return normalizeText(errorLike);
        }
        if (typeof errorLike === "object") {
            if (typeof errorLike.message === "string") {
                return normalizeText(errorLike.message);
            }
            if (typeof errorLike.error === "string") {
                return normalizeText(errorLike.error);
            }
        }
        return normalizeText(errorLike);
    }

    function truncateDetail(value, maxLength = 180) {
        const normalized = normalizeText(value);
        if (normalized.length <= maxLength) {
            return normalized;
        }
        return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
    }

    function formatStatusErrorMessage(statusCode) {
        const code = Number.parseInt(statusCode, 10);
        if (code === 400) return "上游接口请求无效（400），请检查地址和请求参数";
        if (code === 401) return "上游接口鉴权失败（401），请检查 API Key 或可能不支持获取";
        if (code === 403) return "上游接口拒绝访问（403），请确认账号权限";
        if (code === 404) return "未找到上游接口（404），请检查 API 地址";
        if (code === 408) return "上游接口响应超时（408），请稍后重试";
        if (code === 409) return "上游接口返回冲突（409），请检查当前配置";
        if (code === 422) return "上游接口无法处理当前请求（422），请检查请求参数";
        if (code === 429) return "上游接口触发限流（429），请稍后重试";
        if (code >= 500 && code < 600) return `上游接口暂时不可用（${code}），请稍后重试`;
        if (Number.isFinite(code)) return `上游接口返回异常状态（${code}）`;
        return "上游接口返回异常状态";
    }

    function formatRequestFailureMessage(detail) {
        const normalizedDetail = normalizeText(detail);
        const lowerDetail = normalizedDetail.toLowerCase();
        if (!normalizedDetail) {
            return "请求上游接口失败，请检查网络和配置";
        }
        if (lowerDetail.includes("timed out") || lowerDetail.includes("timeout")) {
            return "请求上游接口超时，请检查网络或适当增大超时时间";
        }
        if (lowerDetail.includes("certificate") || lowerDetail.includes("ssl")) {
            return "上游证书校验失败，请检查证书或调整 Verify SSL 设置";
        }
        if (lowerDetail.includes("proxy")) {
            return "代理连接失败，请检查代理地址和代理可用性";
        }
        if (
            lowerDetail.includes("nameresolutionerror")
            || lowerDetail.includes("getaddrinfo")
            || lowerDetail.includes("name or service not known")
            || lowerDetail.includes("temporary failure in name resolution")
        ) {
            return "无法解析上游域名，请检查地址是否正确";
        }
        if (lowerDetail.includes("connection refused")) {
            return "上游拒绝连接，请检查服务是否可达";
        }
        if (
            lowerDetail.includes("remotedisconnected")
            || lowerDetail.includes("connection reset")
            || lowerDetail.includes("eof occurred")
        ) {
            return "与上游的连接被中断，请稍后重试";
        }
        if (lowerDetail.includes("max retries exceeded")) {
            return "请求上游接口失败，请检查网络连通性和接口地址";
        }
        return "请求上游接口失败，请检查网络连通性和接口地址";
    }

    function buildTranslatedError(message, appendOriginal = false) {
        return {
            message,
            appendOriginal,
        };
    }

    function appendOriginalDetail(summary, rawMessage, maxLength) {
        const detail = truncateDetail(rawMessage, maxLength);
        if (!detail) {
            return summary;
        }
        return `${summary}\n原始信息：${detail}`;
    }

    function translateBackendErrorMessage(message) {
        const normalizedMessage = normalizeText(message);
        if (!normalizedMessage) {
            return null;
        }
        if (containsChineseText(normalizedMessage)) {
            return buildTranslatedError(normalizedMessage);
        }

        const exactMatch = EXACT_ERROR_MESSAGE_MAP.get(normalizedMessage.toLowerCase());
        if (exactMatch) {
            return buildTranslatedError(exactMatch);
        }

        let matched = normalizedMessage.match(/^Provider already exists:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`Provider 已存在：${matched[1]}`);

        matched = normalizedMessage.match(/^Provider not found:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`Provider 不存在：${matched[1]}`);

        matched = normalizedMessage.match(/^Auth entry not found:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`Auth Entry 不存在：${matched[1]}`);

        matched = normalizedMessage.match(/^Duplicate provider name detected:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`Provider 名称重复：${matched[1]}`);

        matched = normalizedMessage.match(/^Duplicate provider model mapping detected:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`Provider 模型映射重复：${matched[1]}`);

        matched = normalizedMessage.match(/^Provider entry at index (\d+) must be an object$/i);
        if (matched) return buildTranslatedError(`第 ${Number.parseInt(matched[1], 10) + 1} 个 Provider 配置必须是对象`);

        matched = normalizedMessage.match(/^Provider api must use one of:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`API 地址协议不受支持，支持：${matched[1]}`);

        matched = normalizedMessage.match(/^Provider transport must be one of:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`传输方式无效，支持：${matched[1]}`);

        matched = normalizedMessage.match(/^Provider transport 'http' requires api to use http:\/\/ or https:\/\/$/i);
        if (matched) return buildTranslatedError("HTTP 传输仅支持 http:// 或 https:// 地址");

        matched = normalizedMessage.match(/^Configuration file not found:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`配置文件不存在：${matched[1]}`);

        matched = normalizedMessage.match(/^(https?:\/\/\S+|wss?:\/\/\S+) returned (\d{3})$/i);
        if (matched) return buildTranslatedError(formatStatusErrorMessage(matched[2]), true);

        matched = normalizedMessage.match(/^(https?:\/\/\S+|wss?:\/\/\S+) returned no models$/i);
        if (matched) return buildTranslatedError("上游未返回可用模型列表", true);

        matched = normalizedMessage.match(/^(https?:\/\/\S+|wss?:\/\/\S+) returned invalid json: (.+)$/i);
        if (matched) return buildTranslatedError("上游返回的数据格式无效，无法解析模型列表", true);

        matched = normalizedMessage.match(/^(https?:\/\/\S+|wss?:\/\/\S+) request failed: (.+)$/i);
        if (matched) return buildTranslatedError(formatRequestFailureMessage(matched[2]), true);

        matched = normalizedMessage.match(/^Log level must be one of:\s*(.+)$/i);
        if (matched) return buildTranslatedError(`日志级别无效，支持：${matched[1]}`);

        return null;
    }

    function formatActionErrorMessage(actionLabel, errorLike, options) {
        const opts = options || {};
        const fallback = opts.fallback || `${actionLabel || "操作"}失败`;
        const rawMessage = extractErrorText(errorLike);
        if (!rawMessage) {
            return fallback;
        }

        const translatedResult = translateBackendErrorMessage(rawMessage);
        if (translatedResult) {
            if (translatedResult.appendOriginal) {
                return appendOriginalDetail(translatedResult.message, rawMessage, opts.detailMaxLength);
            }
            return translatedResult.message;
        }

        if (containsChineseText(rawMessage)) {
            return rawMessage;
        }

        return `${fallback}，请检查配置后重试\n原始信息：${truncateDetail(rawMessage, opts.detailMaxLength)}`;
    }

    function ensureToastContainer() {
        let container = document.getElementById(TOAST_CONTAINER_ID);
        if (container) return container;

        container = document.createElement("div");
        container.id = TOAST_CONTAINER_ID;
        container.className = "toast-container position-fixed top-0 end-0 p-3";
        container.style.zIndex = "1080";
        document.body.appendChild(container);
        return container;
    }

    function createToastElement(message, toastTypeClass) {
        const toast = document.createElement("div");
        toast.className = `toast align-items-center border-0 ${toastTypeClass}`;
        toast.setAttribute("role", "alert");
        toast.setAttribute("aria-live", "assertive");
        toast.setAttribute("aria-atomic", "true");

        const wrapper = document.createElement("div");
        wrapper.className = "d-flex";

        const body = document.createElement("div");
        body.className = "toast-body";
        body.textContent = String(message || "");

        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "btn-close btn-close-white me-2 m-auto";
        closeBtn.setAttribute("data-bs-dismiss", "toast");
        closeBtn.setAttribute("aria-label", "Close");

        wrapper.appendChild(body);
        wrapper.appendChild(closeBtn);
        toast.appendChild(wrapper);
        return toast;
    }

    function showMessage(message, type, options) {
        const opts = options || {};
        const level = type || "error";
        const dismissMs = Number.isFinite(opts.dismissMs) ? opts.dismissMs : 3000;
        const container = ensureToastContainer();

        const typeMap = {
            success: "text-bg-success",
            error: "text-bg-danger",
            warning: "text-bg-warning",
            info: "text-bg-info",
        };
        const toastTypeClass = typeMap[level] || typeMap.error;

        const toastEl = createToastElement(message, toastTypeClass);
        container.appendChild(toastEl);

        const delay = dismissMs > 0 ? dismissMs : 600000;
        if (window.bootstrap && window.bootstrap.Toast) {
            const toast = window.bootstrap.Toast.getOrCreateInstance(toastEl, {
                autohide: dismissMs > 0,
                delay,
            });
            toast.show();
            if (!toastEl.hasAttribute("data-bs-dismiss-bound")) {
                toastEl.setAttribute("data-bs-dismiss-bound", "true");
                toastEl.addEventListener("hidden.bs.toast", function () {
                    toastEl.remove();
                });
            }
            return;
        }

        if (dismissMs > 0) {
            window.setTimeout(function () {
                toastEl.remove();
            }, dismissMs);
        }
    }

    function showActionError(actionLabel, errorLike, options) {
        showMessage(formatActionErrorMessage(actionLabel, errorLike, options), "error", options);
    }

    window.formatActionErrorMessage = formatActionErrorMessage;
    window.showActionError = showActionError;
    window.showMessage = showMessage;
})();
