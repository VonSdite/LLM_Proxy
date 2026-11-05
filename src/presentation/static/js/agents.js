"use strict";

var CODEX_DEFAULT_AUTO_COMPACT_TOKEN_LIMIT = 350000;
var LOCAL_INTERFACES = [];
var SERVER_BIND_HOST = "";
var SERVER_PORT = "";
var SERVER_ORIGIN = "";
var API_KEY_MANAGEMENT_ENABLED = false;
var API_KEYS = [];
function serverBaseUrls() {
    var urls = [SERVER_ORIGIN];
    var hosts = ["127.0.0.1", "localhost", "::1"].concat(LOCAL_INTERFACES.map(function (iface) { return iface.ip; }));
    if (SERVER_BIND_HOST && SERVER_BIND_HOST !== "0.0.0.0" && SERVER_BIND_HOST !== "::") hosts.push(SERVER_BIND_HOST);
    machines.forEach(function (machine) { if (machine.serviceHost) hosts.push(machine.serviceHost); });
    hosts.forEach(function (ip) {
        urls.push(proxyUrlFor(ip, "claude"));
    });
    return urls.concat(urls.map(function (url) { return url + "/v1"; }));
}

function normalizedApiUrl(value) {
    try {
        var parsed = new URL(String(value || "").trim());
        if (["http:", "https:"].indexOf(parsed.protocol) === -1 || parsed.username || parsed.password || parsed.search || parsed.hash) return "";
        return parsed.origin + parsed.pathname.replace(/\/+$/, "");
    } catch (error) { return ""; }
}

/* 本服务地址用于模型目录和下游 Key 候选，完整匹配协议、主机、端口和路径。 */
function isOwnServiceUrl(url) {
    var normalized = normalizedApiUrl(url);
    return !!normalized && serverBaseUrls().some(function (base) { return normalizedApiUrl(base) === normalized; });
}

/* 当前监听 IP 对目标机器是否可达：仅绑定 0.0.0.0 或具体网卡地址时远程可访问 */
function bindReachableFor(machine) {
    if (machine.local) return true;
    if (SERVER_BIND_HOST === "0.0.0.0" || SERVER_BIND_HOST === "::") return true;
    if (!SERVER_BIND_HOST || SERVER_BIND_HOST === "127.0.0.1" || SERVER_BIND_HOST === "::1") return false;
    return true;
}

/* 远程机器访问仅监听回环的本服务时显示提示。 */
function apiUnreachable(machine, baseUrl) {
    if (machine.local) return false;
    if (bindReachableFor(machine)) return false;
    return isOwnServiceUrl(baseUrl);
}

/* 模型参数候选与实际配置分离；只有用户选择候选时才写入草稿。 */
var MODEL_LIMIT_PRESETS = [];

function baseModelKey(modelId) {
    return String(modelId || "").trim().toLowerCase().replace(/\[1m\]$/i, "").replace(/^.*\//, "");
}

function lookupModelLimits(modelId) {
    var base = baseModelKey(modelId);
    if (!base) return null;
    var lookups = [];
    [base, base.replace(/\./g, "-"), base.replace(/-/g, ".")].forEach(function (v) {
        if (v && lookups.indexOf(v) === -1) lookups.push(v);
    });
    var index, preset, id;
    for (index = 0; index < lookups.length; index += 1) {
        for (preset = 0; preset < MODEL_LIMIT_PRESETS.length; preset += 1) {
            if (MODEL_LIMIT_PRESETS[preset].ids.indexOf(lookups[index]) !== -1) return MODEL_LIMIT_PRESETS[preset];
        }
    }
    for (index = 0; index < lookups.length; index += 1) {
        for (preset = 0; preset < MODEL_LIMIT_PRESETS.length; preset += 1) {
            if (MODEL_LIMIT_PRESETS[preset].aliases && MODEL_LIMIT_PRESETS[preset].aliases.indexOf(lookups[index]) !== -1) return MODEL_LIMIT_PRESETS[preset];
        }
    }
    /* 日期快照：如 claude-sonnet-5-5-20261001 命中 claude-sonnet-5-5 */
    for (index = 0; index < lookups.length; index += 1) {
        for (preset = 0; preset < MODEL_LIMIT_PRESETS.length; preset += 1) {
            for (id = 0; id < MODEL_LIMIT_PRESETS[preset].ids.length; id += 1) {
                var snapshotId = MODEL_LIMIT_PRESETS[preset].ids[id];
                if (lookups[index].indexOf(snapshotId + "-") === 0 && /^\d{4}(-?\d{2}){0,2}$/.test(lookups[index].slice(snapshotId.length + 1))) {
                    return MODEL_LIMIT_PRESETS[preset];
                }
            }
        }
    }
    return null;
}

function positiveInt(value) {
    var num = parseInt(value, 10);
    return Number.isInteger(num) && num > 0 ? num : null;
}

function configString(value) { return typeof value === "string" ? value : ""; }

function markClear(draft, field, cleared) {
    var fields = (draft.clearFields || []).filter(function (name) { return name !== field; });
    if (cleared) fields.push(field);
    if (fields.length) draft.clearFields = fields; else delete draft.clearFields;
}

/* 限制值解析优先级：Provider 模型配置 > 预置表；source 标记用于侧栏来源说明 */
function modelParameterCandidates(modelId) {
    var preset = lookupModelLimits(modelId);
    var result = { context: [], compact: [], output: [], matched: !!preset };
    if (!modelId) return result;
    var cfg = findProviderModel(providerViewForModel(modelId), modelId);
    var fields = { context: "contextWindow", compact: "compactThresholdPercent", output: "maxOutputTokens" };
    var fallback = { context: 200000, compact: 60, output: 32000 };
    Object.keys(fields).forEach(function (key) {
        var candidates = preset && preset.candidates && preset.candidates[key];
        result[key] = candidates && candidates.length ? candidates.slice() : [{
            value: preset && preset[key] || fallback[key], label: preset && preset[key] ? "模型参考值" : "通用参考值", kind: "fallback"
        }];
        if (cfg && positiveInt(cfg[fields[key]])) {
            result[key].unshift({ value: positiveInt(cfg[fields[key]]), label: "Provider 设置", kind: "configured" });
            result.matched = true;
        }
        result[key] = result[key].filter(function (candidate, index, all) {
            return positiveInt(candidate.value) && all.findIndex(function (item) { return item.value === candidate.value; }) === index;
        });
    });
    return result;
}

var CLAUDE_MODEL_KEYS = [
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_MODEL"
];
var CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max", ["auto", "auto（API 默认）"]];
var CLAUDE_PERMISSION_MODES = [
    ["default", "default（手动确认）"],
    ["acceptEdits", "acceptEdits（自动接受编辑）"],
    ["plan", "plan（规划）"],
    ["auto", "auto（自动审核）"],
    ["dontAsk", "dontAsk（仅预授权）"],
    ["bypassPermissions", "bypassPermissions（跳过确认）"]
];
var REASONING_EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"];
var OPENCODE_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
var CODEX_SERVICE_TIERS = [["auto", "auto（自动）"], ["default", "default（标准）"], ["flex", "flex"], ["priority", "priority（Fast）"]];
var CODEX_SANDBOX_MODES = [["read-only", "read-only（只读）"], ["workspace-write", "workspace-write（工作区可写）"], ["danger-full-access", "danger-full-access（完全访问）"]];
var CODEX_APPROVAL_POLICIES = [["untrusted", "untrusted（非信任命令需确认）"], ["on-request", "on-request（按需确认）"], ["never", "never（不请求确认）"]];
var CODEX_MEMORY_SETTING = { key: "memories", label: "记忆", helpTopic: "codex_memories" };
var CLAUDE_MODE_SETTINGS = [
    { key: "attributionHeader", label: "Attribution Header", env: "CLAUDE_CODE_ATTRIBUTION_HEADER", enabled: "1", disabled: "0", description: "系统提示开头的客户端版本与提示指纹标识。当前版本仍支持此项，关闭后通常省略该标识；部分直连场景的自动审核请求仍会保留。" },
    { key: "disableNonessentialTraffic", label: "非必要流量", env: "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", enabled: "1", disabled: null, enabledLabel: "禁用", disabledLabel: "允许", description: "禁用后关闭自动更新、遥测、错误报告等，也会影响依赖功能开关获取的功能。任意非空值（包括 0、false）都会禁用；选择「允许」会移除该变量，进程环境中的同名变量仍可能生效。" },
    { key: "experimentalAgentTeams", label: "实验性 Agent Teams", env: "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", enabled: "1", disabled: "0", description: "启用实验性的多 Agent 协作功能，默认关闭；实际可用性取决于目标机器的 Claude Code 版本。" },
    { key: "enableToolSearch", label: "Tool Search", env: "ENABLE_TOOL_SEARCH", enabled: "true", disabled: "false", description: "按需加载 MCP 工具。启用需要模型和网关支持工具搜索；禁用则提前加载全部工具。文件还支持 auto、auto:N 自动阈值，本页未选择时保留已有的这类值。" },
    { key: "sandbox", label: "隔离环境标记", env: "IS_SANDBOX", enabled: "1", disabled: "0", description: "向 Claude 声明当前进程已处于隔离环境，本身不建立隔离。当前版本在 Linux/macOS 的 root 用户启用 bypassPermissions 时仍检查此标记是否为 1 等隔离条件；普通用户和原生 Windows 无需此标记。它与 sandbox.enabled 独立，命令沙箱不能替代该启动环境标记。" }
];
var CLAUDE_SANDBOX_SETTING = { key: "sandboxEnabled", label: "命令沙箱", helpTopic: "claude_sandbox" };
var CLAUDE_WEBFETCH_SETTING = { key: "skipWebFetchPreflight", label: "关闭 WebFetch 预检", helpTopic: "claude_webfetch_preflight" };

var providers = [];
var catalogModels = [];

function availableModelIds() { return catalogModels.slice(); }

var machines = [];
var selectedMachineId = "local";

/* 每机器状态：files = 磁盘快照（受管字段），drafts = 编辑草稿 */
var machineState = {};

var activeAgent = "claude";

/* ---------- 基础工具 ---------- */
function esc(value) {
    return String(value == null ? "" : value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
/* Provider 信息从模型 ID 的 "provider/model" 前缀推导，不再单独选择 Provider */
function providerPrefixForModel(modelId) {
    var s = String(modelId || "").trim();
    var idx = s.indexOf("/");
    return idx > 0 ? s.slice(0, idx) : "main";
}

function providerViewForModel(modelId) {
    var prefix = providerPrefixForModel(modelId);
    var p = providers.filter(function (x) { return x.enabled && (x.name === prefix || x.key === prefix); })[0]
        || providers.filter(function (x) { return x.name === prefix || x.key === prefix; })[0];
    if (p) return p;
    return {
        id: prefix, key: prefix, name: prefix, enabled: true, apiKey: "",
        claudeBaseUrl: "", codexBaseUrl: "", codexWireApi: "responses",
        opencodeBaseUrl: "", opencodeNpm: "@ai-sdk/openai-compatible", models: []
    };
}
function findProviderModel(provider, modelId) {
    if (!provider || !modelId) return null;
    var base = String(modelId).replace(/\[1m\]$/i, "");
    var hit = provider.models.filter(function (m) { return String(m.id).replace(/\[1m\]$/i, "") === base; })[0];
    if (!hit) {
        var lower = base.toLowerCase();
        hit = provider.models.filter(function (m) {
            return String(m.id).replace(/\[1m\]$/i, "").toLowerCase() === lower;
        })[0] || null;
    }
    return hit || null;
}
function selectedMachine() {
    return machines.filter(function (m) { return m.id === selectedMachineId; })[0] || machines[0];
}
function proxyUrlFor(ip, agent) {
    var host = ip.indexOf(":") !== -1 ? "[" + ip.replace(/^\[|\]$/g, "") + "]" : ip;
    return "http://" + host + ":" + SERVER_PORT + (agent === "claude" ? "" : "/v1");
}

function defaultBaseUrl(machine, agent) {
    if (machine.local) return proxyUrlFor("127.0.0.1", agent);
    var origin = new URL(SERVER_ORIGIN);
    if (origin.hostname !== "localhost" && origin.hostname !== "127.0.0.1" && origin.hostname !== "[::1]") {
        return SERVER_ORIGIN + (agent === "claude" ? "" : "/v1");
    }
    return proxyUrlFor(machine.serviceHost || (LOCAL_INTERFACES[0] && LOCAL_INTERFACES[0].ip) || "127.0.0.1", agent);
}

function displayValue(value) {
    if (value === undefined || value === null || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}
function pathOf(target) {
    var f = mFiles(target);
    return f.path;
}

/* ---------- 每机器状态 ---------- */

function ensureMachineState(machine) {
    if (!machineState[machine.id]) machineState[machine.id] = { files: null, drafts: null, baselines: {} };
    return machineState[machine.id];
}
function mFiles(agent) {
    return ensureMachineState(selectedMachine()).files[agent];
}
function mDrafts() {
    var st = ensureMachineState(selectedMachine());
    if (!st.drafts) st.drafts = { claude: null, codex: null, opencode: null };
    return st.drafts;
}
function mDraft(agent) {
    var drafts = mDrafts();
    if (!drafts[agent]) initDraft(selectedMachine(), agent);
    return drafts[agent];
}



/* ---------- 草稿初始化 / 刷新 ---------- */
function defaultCatalogModel(agent) {
    var catalog = availableModelIds();
    var mainPreferred = catalog.filter(function (id) { return id.indexOf("main/") === 0; })[0];
    if (agent === "claude") {
        return catalog.filter(function (id) { return /claude-sonnet/.test(id); })[0] || mainPreferred || catalog[0] || "";
    }
    return mainPreferred || catalog[0] || "";
}

function initDraft(machine, agent) {
    var st = ensureMachineState(machine);
    if (!st.drafts) st.drafts = { claude: null, codex: null, opencode: null };
    var files = st.files;
    var baseUrlDefault = defaultBaseUrl(machine, agent);
    if (agent === "claude") {
        var m = files.claude.managed;
        var models = {};
        CLAUDE_MODEL_KEYS.forEach(function (key) { models[key] = configString(m["env." + key]); });
        var effort = configString(m["env.CLAUDE_CODE_EFFORT_LEVEL"]);
        var permissionMode = configString(m["permissions.defaultMode"]);
        var runtime = {
            effortLevel: effort === "unset" ? "auto" : effort,
            permissionMode: permissionMode === "manual" ? "default" : permissionMode,
            sandboxEnabled: m["sandbox.enabled"] === true ? "enabled" : m["sandbox.enabled"] === false ? "disabled" : "unset",
            skipWebFetchPreflight: m.skipWebFetchPreflight === true ? "enabled" : m.skipWebFetchPreflight === false ? "disabled" : "unset"
        };
        CLAUDE_MODE_SETTINGS.forEach(function (setting) {
            var v = m["env." + setting.env];
            runtime[setting.key] = setting.disabled === null && typeof v === "string"
                ? (v ? "enabled" : "disabled")
                : v === setting.enabled ? "enabled" : v === setting.disabled ? "disabled" : "unset";
        });
        /* 新建文件的默认值只用于初始草稿，实际写入由保存动作完成。 */
        if (!files.claude.exists) {
            Object.assign(runtime, {
                sandboxEnabled: "unset",
                attributionHeader: "disabled",
                disableNonessentialTraffic: "enabled",
                experimentalAgentTeams: "enabled",
                enableToolSearch: "enabled",
                sandbox: "enabled",
                skipWebFetchPreflight: "enabled"
            });
        }
        if (!CLAUDE_MODEL_KEYS.some(function (key) { return models[key].trim(); }) && !files.claude.exists) {
            models.ANTHROPIC_MODEL = defaultCatalogModel("claude");
        }
        var limits = {
            context: positiveInt(m["env.CLAUDE_CODE_AUTO_COMPACT_WINDOW"]),
            compact: positiveInt(m["env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"]),
            output: positiveInt(m["env.CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        };
        st.drafts.claude = {
            baseUrl: configString(m["env.ANTHROPIC_BASE_URL"]) || baseUrlDefault,
            models: models, runtime: runtime, limits: limits,
            apiKey: configString(m["env.ANTHROPIC_AUTH_TOKEN"])
        };
    } else if (agent === "codex") {
        var m = files.codex.managed;
        var providerName = configString(m["model_provider"]);
        var codexBaseUrl = configString(m["model_providers." + providerName + ".base_url"]) || baseUrlDefault;
        var codexModel = configString(m["model"]) || (isOwnServiceUrl(codexBaseUrl) ? defaultCatalogModel("codex") : "");
        var codexContext = positiveInt(m["model_context_window"]);
        var codexCompactAt = positiveInt(m["model_auto_compact_token_limit"]);
        var codexLimits = { context: codexContext, compact: null, output: null };
        if (codexCompactAt != null) codexLimits.compactTokens = codexCompactAt;
        if (codexContext != null && codexCompactAt != null) {
            codexLimits.compact = Math.round(codexCompactAt / codexContext * 100);
        }
        st.drafts.codex = {
            baseUrl: codexBaseUrl,
            model: codexModel,
            runtime: {
                serviceTier: configString(m["service_tier"]),
                modelReasoningEffort: configString(m["model_reasoning_effort"]),
                sandboxMode: configString(m["sandbox_mode"]),
                approvalPolicy: configString(m["approval_policy"]),
                memories: m["features.memories"] === true ? "enabled" : m["features.memories"] === false ? "disabled" : "unset"
            },
            limits: codexLimits,
            apiKey: configString(files.codexAuth.managed.OPENAI_API_KEY)
        };
    } else {
        var m = files.opencode.managed;
        var storedModel = configString(m["model"]);
        var storedProvider = storedModel.split("/")[0];
        var candidate = storedModel.slice(storedProvider.length + 1);
        var model = m["provider." + storedProvider + ".models." + candidate] ? candidate : storedModel;
        model = model || defaultCatalogModel("opencode");
        var provider = providerViewForModel(model);
        var opencodeBaseUrl = configString(m["provider." + provider.key + ".options.baseURL"]) || baseUrlDefault;
        if (!storedModel && !isOwnServiceUrl(opencodeBaseUrl)) model = "";
        var modelEntry = m["provider." + provider.key + ".models." + model];
        var effort = modelEntry && modelEntry.options ? modelEntry.options.reasoningEffort : "";
        st.drafts.opencode = {
            baseUrl: opencodeBaseUrl,
            model: model,
            runtime: { reasoningEffort: configString(effort) },
            limits: modelEntry && modelEntry.limit ? {
                context: modelEntry.limit.context || null,
                output: modelEntry.limit.output || null,
                reserved: m["compaction.reserved"],
                compact: modelEntry.limit.context && m["compaction.reserved"] != null ? Math.round((1 - m["compaction.reserved"] / modelEntry.limit.context) * 100) : null
            } : { context: null, compact: null, output: null, reserved: m["compaction.reserved"] },
            apiKey: configString(m["provider." + provider.key + ".options.apiKey"])
        };
    }
}

var ICON_MONITOR = '<svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"></rect><path d="M8 21h8M12 17v4"></path></svg>';
var ICON_SERVER = '<svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="7" rx="2"></rect><rect x="2" y="14" width="20" height="7" rx="2"></rect><path d="M6 6.5h.01M6 17.5h.01"></path></svg>';

/* 代理 URL 规范化与解析：与系统设置页逻辑一致，凭据写在 URL 的 userinfo 中 */
function normalizeProxyUrl(url) {
    var text = (url || "").trim();
    if (!text) return null;
    var parsed;
    try {
        parsed = new URL(text);
    } catch (e) {
        return null;
    }
    if (["http:", "https:", "socks5:", "socks5h:"].indexOf(parsed.protocol) === -1) return null;
    if (!parsed.hostname || parsed.search || parsed.hash || (parsed.pathname && parsed.pathname !== "/")) return null;
    var userinfo = parsed.username ? parsed.username + (parsed.password ? ":" + parsed.password : "") + "@" : "";
    var port = parsed.port ? ":" + parsed.port : "";
    return parsed.protocol + "//" + userinfo + parsed.hostname + port;
}

function parseProxyUrl(url) {
    var normalized = normalizeProxyUrl(url);
    if (!normalized) return null;
    var parsed = new URL(normalized);
    var scheme = parsed.protocol.replace(/:$/, "");
    var defaultPort = scheme === "http" ? 80 : scheme === "https" ? 443 : 1080;
    var username = "";
    var password = "";
    try {
        username = parsed.username ? decodeURIComponent(parsed.username) : "";
        password = parsed.password ? decodeURIComponent(parsed.password) : "";
    } catch (e) {
        return null;
    }
    return {
        scheme: scheme,
        host: parsed.hostname,
        port: parsed.port ? Number(parsed.port) : defaultPort,
        username: username,
        password: password
    };
}
function connText(machine) {
    if (machine.local) return (machine.platform || "本机") + " · 服务运行用户";
    return machine.user + "@" + machine.host + ":" + (machine.port || "22");
}

function machineDetailHtml(machine) {
    var statusHtml = machine.connecting
        ? '<span class="machine-dot"></span><span>连接中…</span>'
        : machine.connected
            ? ""
            : '<span class="machine-dot"></span><span>未连接</span>';
    var mainBtn = machine.connecting
        ? '<button type="button" class="btn btn-primary btn-sm" disabled>连接中…</button>'
        : machine.connected
            ? '<button type="button" class="btn btn-primary btn-sm" data-action="disconnect-machine" data-machine-id="' + machine.id + '">断开</button>'
            : '<button type="button" class="btn btn-primary btn-sm" data-action="connect-machine" data-machine-id="' + machine.id + '">连接</button>';
    return (statusHtml ? '<div class="bar-status">' + statusHtml + "</div>" : "") +
        '<div class="bar-buttons">' + mainBtn +
        '<button type="button" class="btn btn-secondary btn-sm" data-action="edit-machine" data-machine-id="' + machine.id + '">编辑</button>' +
        '<button type="button" class="btn btn-danger-quiet btn-sm" data-action="remove-machine" data-machine-id="' + machine.id + '">删除</button>' +
        "</div>";
}

function renderMachines() {
    var searchEl = document.getElementById("machineSearch");
    var keyword = ((searchEl && searchEl.value) || "").trim().toLowerCase();
    var visible = machines.filter(function (m) {
        if (!keyword) return true;
        return (m.name + " " + m.host + " " + m.user).toLowerCase().indexOf(keyword) !== -1;
    });
    var html = "";
    visible.forEach(function (m) {
        var selected = m.id === selectedMachineId;
        var dot = m.connected ? '<span class="machine-dot machine-dot-on" title="已连接"></span>'
            : '<span class="machine-dot" title="' + (m.connecting ? "连接中…" : "未连接") + '"></span>';
        var item = '<button type="button" class="machine-item' + (selected ? " is-selected" : "") + '" data-select-machine="' + m.id + '" role="option" aria-selected="' + selected + '">' +
            '<span class="machine-icon">' + (m.local ? ICON_MONITOR : ICON_SERVER) + "</span>" +
            '<span class="machine-copy"><span class="machine-name">' + esc(m.name) + '</span>' +
            '<span class="machine-sub">' + esc(connText(m)) + "</span></span>" + dot + "</button>";
        if (selected && !m.local) {
            html += '<div class="machine-group is-selected">' + item +
                '<div class="machine-item-detail">' + machineDetailHtml(m) + "</div></div>";
        } else {
            html += item;
        }
    });
    document.getElementById("machineList").innerHTML = html || '<div class="machine-empty">没有匹配的机器</div>';
}

/* ---------- 渲染：Agent 面板 ---------- */
var panel = document.getElementById("agentPanel");

function render() {
    closeLimitCandidates();
    closeCustomSelects();
    closeApiKeyCandidates();
    closeHelp();
    renderMachines();
    var machine = selectedMachine();
    if (!machine) { panel.innerHTML = '<div class="locked-state"><p>正在读取配置…</p></div>'; return; }
    var st = ensureMachineState(machine);
    var ready = machine.connected && st.files;
    document.querySelectorAll(".agent-toolbar-actions button").forEach(function (button) { button.disabled = !ready || saving; });
    if (!machine.connected) panel.innerHTML = renderLocked(machine);
    else if (!st.files) panel.innerHTML = '<div class="locked-state"><p>' + esc(st.error || "正在读取配置…") + '</p><button class="btn btn-secondary" data-action="refresh">重新读取</button></div>';
    else {
        panel.innerHTML = activeAgent === "claude" ? renderClaude(mDraft("claude")) : activeAgent === "codex" ? renderCodex(mDraft("codex")) : renderOpencode(mDraft("opencode"));
        updatePreview();
    }
    labelControls();
}

function renderLocked(machine) {
    var action = machine.connecting
        ? '<button type="button" class="btn btn-primary" disabled>连接中…</button>'
        : '<button type="button" class="btn btn-primary" data-action="connect-machine" data-machine-id="' + machine.id + '">连接</button>';
    return '<div class="locked-state">' +
        '<svg viewBox="0 0 24 24"><rect x="4" y="11" width="16" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path></svg>' +
        "<p>" + esc(machine.name) + " 未连接</p>" +
        '<p class="locked-hint">连接后读取该机器上的 Agent 配置文件</p>' + action + "</div>";
}

function renderPathRow(label, target) {
    var f = mFiles(target);
    var customBtn = target !== "codexAuth"
        ? '<button type="button" class="path-settings-button' + (f.customPath ? ' is-custom' : '') + '" data-action="change-path" data-path-target="' + target + '" title="配置路径" aria-label="设置 ' + target + ' 配置路径"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M8 4v6M16 14v6"></path></svg></button>'
        : "";
    var pathHtml = f.exists
        ? '<button type="button" class="path-value path-link" data-action="open-config" data-file-target="' + target + '" aria-label="打开 ' + esc(label) + '" title="打开配置文件"><code>' + esc(pathOf(target)) + "</code></button>"
        : '<code class="path-value">' + esc(pathOf(target)) + "</code>";
    return '<div class="path-row">' +
        (f.exists
            ? '<span class="path-badge path-badge-ok">存在</span>'
            : '<span class="path-badge path-badge-missing">不存在，保存时创建</span>') +
        '<span class="path-label">' + esc(label) + '</span>' +
        pathHtml +
        customBtn +
        "</div>";
}

function renderParseNotice(target) {
    var f = mFiles(target);
    if (!f.parseError) return "";
    return '<div class="notice" role="alert"><span>⚠</span><span>配置文件解析失败：' + esc(f.parseError) + "。修复文件内容后才能保存。</span></div>";
}

/* ---------- 可搜索下拉（Provider / 模型） ---------- */
var comboState = null;

function comboOptionsFor(kind, key) {
    var draft = mDraft(activeAgent);
    if (kind === "claudeAllModels") {
        var allOptions = comboOptionsFor("claudeModel", "ANTHROPIC_MODEL");
        allOptions.value = uniformClaudeModel();
        allOptions.placeholder = "选择并应用到全部";
        allOptions.options = allOptions.options.filter(function (option) { return option.value !== ""; });
        return allOptions;
    }
    if (!isOwnServiceUrl(draft.baseUrl)) {
        return { value: kind === "claudeModel" ? draft.models[key] || "" : draft.model || "", placeholder: "输入模型 ID", options: [] };
    }
    if (kind === "claudeModel") {
        /* 全部可用模型 ID 平铺，Claude 每个模型额外提供 [1m] 变体（标签显示 " [1M]"） */
        var seen = {};
        var options = [{ value: "", label: "不设置" }];
        availableModelIds().forEach(function (id) {
            var base = String(id).replace(/\[1m\]$/i, "");
            [base, base + "[1m]"].forEach(function (v) {
                if (!seen[v]) {
                    seen[v] = true;
                    options.push({ value: v, label: v.replace(/\[1m\]$/i, " [1M]") });
                }
            });
        });
        var current = draft.models[key] || "";
        if (current && !seen[current]) {
            options.push({ value: current, label: "当前：" + current + "（不在可用模型列表）" });
        }
        return { value: current, placeholder: "不设置", options: options };
    }
    var modelList = availableModelIds().map(function (id) { return { value: id, label: id }; });
    var current = draft.model || "";
    if (current && modelList.filter(function (o) { return o.value === current; }).length === 0) {
        modelList.push({ value: current, label: "当前：" + current + "（不在可用模型列表）" });
    }
    return {
        value: current,
        placeholder: "选择模型",
        options: modelList
    };
}

function comboTriggerHtml(kind, key) {
    var cfg = comboOptionsFor(kind, key);
    if (!isOwnServiceUrl(mDraft(activeAgent).baseUrl)) {
        return '<input type="text" class="form-control model-id-input" data-field="model"' +
            (key ? ' data-model-key="' + esc(key) + '"' : "") + ' value="' + esc(cfg.value) +
            '" aria-label="' + esc(key || "模型") + '" placeholder="输入模型 ID" spellcheck="false" autocomplete="off">';
    }
    var current = cfg.options.filter(function (o) { return o.value === cfg.value; })[0];
    var text = current ? current.label : (cfg.value || cfg.placeholder);
    var textCls = current ? "" : ' class="trigger-placeholder"';
    return '<div class="custom-select">' +
        '<button type="button" class="custom-select-trigger" data-combo="' + kind + '"' +
        (key ? ' data-combo-key="' + esc(key) + '"' : "") + ' aria-haspopup="listbox" aria-label="' + esc(kind === "claudeAllModels" ? "一键设置模型" : key || "模型") + '">' +
        "<span" + textCls + ">" + esc(text) + "</span></button>" +
        '<div class="custom-select-menu"></div></div>';
}

function openCustomSelect(trigger) {
    if (!isOwnServiceUrl(mDraft(activeAgent).baseUrl)) { closeCustomSelects(); return; }
    var wrap = trigger.closest(".custom-select");
    if (!wrap) return;
    if (wrap.classList.contains("open")) {
        closeCustomSelects();
        return;
    }
    closeCustomSelects();
    closeHelp();
    comboState = {
        kind: trigger.getAttribute("data-combo"),
        key: trigger.getAttribute("data-combo-key") || "",
        wrap: wrap
    };
    var menu = wrap.querySelector(".custom-select-menu");
    menu.innerHTML = '<div class="custom-select-search"><input class="form-control" type="text" placeholder="搜索" autocomplete="off" aria-label="搜索"></div>' +
        '<div class="custom-select-options"></div>';
    renderCustomSelectOptions("");
    var rect = trigger.getBoundingClientRect();
    wrap.classList.toggle("drop-up", rect.bottom + 260 > window.innerHeight && rect.top > 280);
    wrap.classList.add("open");
    var input = menu.querySelector("input");
    if (input && input.focus) input.focus();
    if (input && input.addEventListener) {
        input.addEventListener("input", function () { renderCustomSelectOptions(input.value); });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                var first = menu.querySelector(".custom-select-option");
                if (first) commitCustomSelect(first.getAttribute("data-value"));
            } else if (event.key === "Escape") {
                closeCustomSelects();
            }
        });
    }
}

function renderCustomSelectOptions(filter) {
    if (!comboState) return;
    var menu = comboState.wrap.querySelector(".custom-select-menu");
    var mount = menu.querySelector(".custom-select-options");
    if (!mount) return;
    var cfg = comboOptionsFor(comboState.kind, comboState.key);
    var keyword = (filter || "").trim().toLowerCase();
    var visible = cfg.options.filter(function (o) {
        return !keyword || o.label.toLowerCase().indexOf(keyword) !== -1 || o.value.toLowerCase().indexOf(keyword) !== -1;
    });
    mount.innerHTML = visible.map(function (o) {
        var selected = o.value === cfg.value;
        return '<button type="button" class="custom-select-option' + (selected ? " selected" : "") + '" data-value="' + esc(o.value) + '" role="option" aria-selected="' + selected + '">' + esc(o.label) + "</button>";
    }).join("") || '<div class="custom-select-empty">无匹配项</div>';
}

function closeCustomSelects() {
    if (typeof document.querySelectorAll === "function") {
        document.querySelectorAll(".custom-select.open").forEach(function (wrap) {
            wrap.classList.remove("open");
        });
    }
    comboState = null;
}

function claudeMainModel(draft) {
    var main = (draft.models.ANTHROPIC_MODEL || "").trim();
    var models = claudeConfiguredModels(draft);
    return main || (models.length === 1 ? models[0] : "");
}

function claudeConfiguredModels(draft) {
    return CLAUDE_MODEL_KEYS.map(function (key) { return (draft.models[key] || "").trim(); })
        .filter(function (value, index, all) { return value && all.indexOf(value) === index; });
}

function limitReferenceModels(draft) {
    var model = activeAgent === "claude" ? claudeMainModel(draft) : (draft.model || "").trim();
    return model ? [model] : activeAgent === "claude" ? claudeConfiguredModels(draft) : [];
}

function syncLimitSuggestions() {
    var draft = mDraft(activeAgent);
    document.querySelectorAll("[data-limit-options]").forEach(function (menu) {
        var key = menu.getAttribute("data-limit-options");
        menu.innerHTML = limitCandidateOptions(key, limitCandidates(key, draft), draft.limits[key]);
    });
}

function uniformClaudeModel() {
    var models = mDraft("claude").models, value = models[CLAUDE_MODEL_KEYS[0]] || "";
    return CLAUDE_MODEL_KEYS.every(function (key) { return (models[key] || "") === value; }) ? value : "";
}

function applyClaudeModelToAll(value) {
    value = String(value || "").trim();
    if (activeAgent !== "claude" || saving) return;
    var draft = mDraft("claude");
    var own = isOwnServiceUrl(draft.baseUrl);
    if (own && !value) return;
    CLAUDE_MODEL_KEYS.forEach(function (key) {
        draft.models[key] = value;
        markClear(draft, "models." + key, !value);
    });
    if (own) {
        syncModelControls();
    } else {
        /* 输入时只同步模型值，保留当前输入框与光标。 */
        document.querySelectorAll('#modelFieldsMount [data-field="model"][data-model-key]').forEach(function (input) {
            input.value = value;
        });
        syncLimitSuggestions();
        updatePreview();
    }
}

function syncModelControls() {
    document.querySelectorAll("#modelFieldsMount [data-combo]").forEach(function (trigger) {
        var cfg = comboOptionsFor(trigger.getAttribute("data-combo"), trigger.getAttribute("data-combo-key"));
        var current = cfg.options.find(function (option) { return option.value === cfg.value; });
        var label = trigger.querySelector("span");
        setPreviewText(label, current ? current.label : (cfg.value || cfg.placeholder));
        label.classList.toggle("trigger-placeholder", !current);
    });
    syncLimitSuggestions();
    labelControls();
    updatePreview();
}

function commitCustomSelect(value) {
    if (!comboState) return;
    var state = comboState;
    closeCustomSelects();
    if (!isOwnServiceUrl(mDraft(activeAgent).baseUrl)) return;
    if (state.kind === "claudeAllModels") {
        applyClaudeModelToAll(value);
    } else if (state.kind === "claudeModel") {
        var draft = mDraft("claude");
        draft.models[state.key] = value;
        markClear(draft, "models." + state.key, !value);
        syncModelControls();
    } else if (state.kind === "codexModel") {
        var draft = mDraft("codex");
        draft.model = value;
        syncModelControls();
    } else if (state.kind === "opencodeModel") {
        var draft = mDraft("opencode");
        draft.model = value;
        syncModelControls();
    }
    var trigger = state.wrap && state.wrap.querySelector(".custom-select-trigger");
    if (trigger) trigger.focus({ preventScroll: true });
}

function apiAddressField(value) {
    var machine = selectedMachine();
    /* 监听回环且填的仍是本服务地址时给出警示（不禁用输入，可手动填其他可达地址） */
    var warn = '<button type="button" class="field-help-button field-help-warning" id="apiAddressWarning" data-help-topic="api_unreachable" aria-label="地址可能不可达"' +
        (apiUnreachable(machine, value) ? "" : " hidden") + '>!</button>';
    var placeholder = activeAgent === "claude" ? "http://host:8080" : "http://host:8080/v1";
    return '<div class="form-field"><label class="form-label"><span>API 地址</span>' +
        '<button type="button" class="field-help-button" data-help-topic="api_address" aria-label="查看说明">?</button>' + warn + "</label>" +
        '<input class="form-control" type="url" name="agent_api_url" data-field="baseUrl" value="' + esc(value) + '" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="' + placeholder + '"></div>';
}

function updateBaseUrl(value) {
    var draft = mDraft(activeAgent);
    if (draft.baseUrl === value) return;
    var wasOwn = isOwnServiceUrl(draft.baseUrl);
    draft.baseUrl = value;
    if (wasOwn !== isOwnServiceUrl(value)) {
        closeCustomSelects();
        closeApiKeyCandidates();
        closeHelp();
        var keyMount = document.getElementById("apiKeyMount");
        if (keyMount) keyMount.innerHTML = apiKeyFieldHtml(draft);
        var modelMount = document.getElementById("modelFieldsMount");
        if (modelMount) modelMount.innerHTML = modelFieldsHtml(activeAgent);
        labelControls();
    }
    var warning = document.getElementById("apiAddressWarning");
    if (warning) warning.hidden = !apiUnreachable(selectedMachine(), value);
    updatePreview();
}

/* API Key 字段始终展示（可留空）；识别为本服务时聚焦输入框给出候选 */
function apiKeyFieldHtml(draft) {
    var own = API_KEY_MANAGEMENT_ENABLED && isOwnServiceUrl(draft.baseUrl);
    /* 配置密钥使用文本遮罩，保持与 Provider 页一致的输入行为。 */
    var shell = '<span class="sensitive-shell"><input class="form-control sensitive-input-control is-masked" id="apiKeyInput" type="text" name="agent_api_key" data-field="apiKey" value="' + esc(draft.apiKey || "") + '" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="可选"' + (own ? ' data-apikey-candidates="1"' : "") + '>' +
        '<button type="button" class="sensitive-toggle" data-action="toggle-apikey" aria-controls="apiKeyInput" aria-pressed="false" aria-label="显示 API Key">' + ICON_EYE + "</button></span>";
    return '<div class="form-field"><label class="form-label"><span>API Key</span>' +
        '<button type="button" class="field-help-button" data-help-topic="' + (own ? "api_key" : "api_key_external") + '" aria-label="查看说明">?</button></label>' +
        shell + "</div>";
}

/* API Key 候选：聚焦输入框时在本服务已创建的 Key 中联想 */
var apikeyMenuEl = null;
function openApiKeyCandidates(input) {
    closeApiKeyCandidates();
    if (!API_KEY_MANAGEMENT_ENABLED || !isOwnServiceUrl(mDraft(activeAgent).baseUrl)) return;
    var rect = input.getBoundingClientRect();
    var root = document.getElementById("apikeyCandidateRoot");
    root.innerHTML = '<div class="apikey-candidates" role="listbox" style="left:' + Math.max(12, rect.left) + "px;top:" + (rect.bottom + 4) + "px;width:" + Math.max(rect.width, 240) + 'px">' +
        API_KEYS.map(function (k) {
            return '<button type="button" class="apikey-candidate" role="option" data-key="' + esc(k.key) + '">' +
                '<span>' + esc(k.name) + '</span><code>' + esc(k.key.slice(0, 10) + "…") + "</code></button>";
        }).join("") + "</div>";
    apikeyMenuEl = root.querySelector(".apikey-candidates");
}
function closeApiKeyCandidates() {
    var root = document.getElementById("apikeyCandidateRoot");
    if (root) root.innerHTML = "";
    apikeyMenuEl = null;
}

function limitInputHtml(label, key, value, candidates, labelExtra) {
    return '<div class="limit-edit-row"><label class="form-label"><span>' + esc(label) + '</span>' +
        '<button type="button" class="field-help-button" data-help-topic="limit_' + key + '" aria-label="查看' + esc(label) + '说明">?</button>' + (labelExtra || "") + '</label>' +
        '<div class="limit-input-shell"><input class="form-control" type="number" min="1" autocomplete="off" data-limit="' + key + '" value="' +
        (value == null ? "" : esc(value)) + '" placeholder="不设置">' +
        '<details class="limit-candidates"><summary title="推荐值" aria-label="选择' + esc(label) + '推荐值"></summary>' +
        '<div class="custom-select-menu"><div class="custom-select-options" data-limit-options="' + key + '" role="group" aria-label="' + esc(label) + '推荐值">' +
        limitCandidateOptions(key, candidates, value) + '</div></div></details></div></div>';
}

function limitCandidates(key, draft) {
    return limitReferenceModels(draft).flatMap(function (model) {
        return modelParameterCandidates(model)[key].map(function (candidate) {
            return Object.assign({}, candidate, { referenceModel: model });
        });
    });
}

function limitCandidateOptions(key, candidates, value) {
    if (!candidates.length) return '<div class="custom-select-empty">先选择模型</div>';
    return candidates.map(function (candidate) {
        var selected = Number(value) === candidate.value;
        return '<button type="button" class="custom-select-option limit-candidate' + (selected ? ' selected' : '') + '" data-action="apply-limit" data-limit-key="' + key + '" data-limit-value="' + candidate.value + '" aria-pressed="' + selected + '">' +
            '<strong>' + Number(candidate.value).toLocaleString() + (key === "compact" ? "%" : "") + '</strong><span>' + esc(candidate.referenceModel) + ' · ' + esc(candidate.label) + '</span></button>';
    }).join("");
}

function closeLimitCandidates(except) {
    document.querySelectorAll(".limit-candidates[open]").forEach(function (menu) {
        if (menu !== except) menu.removeAttribute("open");
    });
}

function changeLimit(key, value) {
    var draft = mDraft(activeAgent), limits = draft.limits;
    var previous = limits[key] == null ? "" : String(limits[key]).trim();
    value = String(value).trim();
    if (previous === value || (previous && value && Number(previous) === Number(value))) return;
    limits[key] = value;
    markClear(draft, "limits." + key, !value);
    if (key === "context" || key === "compact") { delete limits.compactTokens; delete limits.reserved; }
    document.querySelectorAll('.limit-candidate[data-limit-key="' + key + '"]').forEach(function (candidate) {
        var selected = Number(value) === Number(candidate.getAttribute("data-limit-value"));
        candidate.classList.toggle("selected", selected);
        candidate.setAttribute("aria-pressed", String(selected));
    });
    var derived = document.getElementById("limitDerivedMount");
    var derivedHtml = limitDerivedHtml(draft);
    if (derived && derived.innerHTML !== derivedHtml) derived.innerHTML = derivedHtml;
    updatePreview();
}

function limitDerivedHtml(draft) {
    var limits = draft.limits || {}, agent = activeAgent;
    var label, value;
    var ctx = positiveInt(limits.context);
    var pct = positiveInt(limits.compact);
    if (agent === "codex") {
        label = "上限";
        value = limits.compactTokens || (ctx ? (pct != null ? Math.floor(ctx * pct / 100) : Math.min(CODEX_DEFAULT_AUTO_COMPACT_TOKEN_LIMIT, Math.floor(ctx * 0.95))) : null);
    } else if (agent === "opencode") {
        label = "预留";
        value = limits.reserved != null ? limits.reserved : (ctx && pct != null ? ctx - Math.floor(ctx * pct / 100) : null);
    }
    return value == null ? "" : '· ' + label + ' <strong>' + Number(value).toLocaleString() + '</strong>';
}

/* 模型参数按文件值显示，推荐值由用户选择应用。 */
function limitFieldsHtml(draft) {
    var limits = draft.limits || {};
    var agent = activeAgent;
    var effort = agent === "claude"
        ? selectField("思考强度", "claudeEffort", CLAUDE_EFFORTS, draft.runtime.effortLevel, "claude_effort")
        : agent === "codex"
            ? selectField("思考强度", "codexEffort", REASONING_EFFORTS, draft.runtime.modelReasoningEffort, "codex_effort")
            : selectField("思考强度", "opencodeEffort", OPENCODE_EFFORTS, draft.runtime.reasoningEffort, "opencode_effort");
    var derived = agent === "claude" ? "" : '<span class="limit-derived-mount" id="limitDerivedMount" aria-live="polite">' + limitDerivedHtml(draft) + '</span>';
    return '<div class="limits-list" role="group" aria-label="模型参数">' +
        effort +
        limitInputHtml(agent === "claude" ? "压缩窗口" : "上下文窗口", "context", limits.context, limitCandidates("context", draft)) +
        limitInputHtml("压缩阈值 %", "compact", limits.compact, limitCandidates("compact", draft), derived) +
        (agent === "codex" ? "" : limitInputHtml("最大输出", "output", limits.output, limitCandidates("output", draft))) +
        "</div>";
}

function selectField(label, name, options, value, helpTopic) {
    var help = helpTopic ? '<button type="button" class="field-help-button" data-help-topic="' + helpTopic + '" aria-label="查看说明">?</button>' : "";
    return '<div class="runtime-field"><label class="form-label"><span>' + esc(label) + "</span>" + help + "</label>" +
        '<select class="form-control" data-field="' + name + '">' +
        '<option value="">不设置</option>' +
        options.map(function (opt) {
            var v = Array.isArray(opt) ? opt[0] : opt;
            var t = Array.isArray(opt) ? opt[1] : opt;
            return '<option value="' + esc(v) + '"' + (v === value ? " selected" : "") + ">" + esc(t) + "</option>";
        }).join("") + "</select></div>";
}

function triStateField(setting, mode) {
    var enabledLabel = setting.enabledLabel || "启用";
    var disabledLabel = setting.disabledLabel || "禁用";
    var helpAttribute = setting.helpTopic ? 'data-help-topic="' + setting.helpTopic + '"' : 'data-help-env="' + setting.env + '"';
    function btn(value, cls, label) {
        return '<button type="button" class="' + (mode === value && cls ? "is-" + cls : "") + '" data-mode-field="' + setting.key + '" data-mode="' + value + '" aria-pressed="' + (mode === value) + '">' + label + "</button>";
    }
    return '<div class="runtime-field"><label class="form-label"><span>' + esc(setting.label) + "</span>" +
        '<button type="button" class="field-help-button" ' + helpAttribute + ' aria-label="查看说明">?</button></label>' +
        '<div class="tri-state" role="group" aria-label="' + esc(setting.label) + '">' +
        btn("enabled", "on", enabledLabel) + btn("disabled", "off", disabledLabel) + btn("unset", "", "不设置") +
        "</div></div>";
}

function previewView(agent) {
    var model = agent === "claude" ? claudeMainModel(mDraft(agent)) : mDraft(agent).model;
    var preview = previewCache[previewId(selectedMachineId, agent)];
    var valid = preview && preview.key === formKey(agent);
    var pending = !valid || !!preview.pending;
    var error = valid && preview.error || "";
    return {
        rows: valid && preview.rows || [],
        pending: pending,
        error: error,
        provider: "Provider: " + providerPrefixForModel(model),
        feedback: pending ? "更新中…" : error ? "预览失败：" + error : "",
        message: pending ? "正在生成保存预览…" : error ? "预览不可用" : "没有需要写入的字段",
        canSave: !!(valid && preview.token && preview.hasChanges && !saving && !pending && !error)
    };
}

function previewHtml(agent) {
    var view = previewView(agent);
    return '<div class="preview-section"><div class="preview-head"><span class="preview-heading"><span class="preview-title">保存预览</span>' +
        '<span class="preview-provider">' + esc(view.provider) + '</span>' +
        '<span class="preview-feedback' + (view.error ? ' is-error' : '') + '" role="status" title="' + esc(view.feedback) + '">' + esc(view.feedback) + '</span></span>' +
        '<button type="button" class="btn btn-primary" data-action="save" id="saveBtn"' + (view.canSave ? "" : " disabled") + '>' + (saving ? "保存中…" : "保存") + '</button></div>' +
        '<div class="preview-list" aria-live="polite" aria-busy="' + view.pending + '">' + previewRowsHtml(view.rows, agent) + '</div>' +
        '<div class="preview-message"' + (view.rows.length ? " hidden" : "") + '>' + esc(view.message) + '</div></div>';
}

function setPreviewText(element, text) {
    if (element.textContent !== text) element.textContent = text;
}

function syncPreview(agent) {
    var mount = document.getElementById("previewMount");
    if (!mount) return;
    var view = previewView(agent), list = mount.querySelector(".preview-list");
    if (!list) return;
    setPreviewText(mount.querySelector(".preview-provider"), view.provider);
    var feedback = mount.querySelector(".preview-feedback");
    setPreviewText(feedback, view.feedback);
    feedback.title = view.feedback;
    feedback.classList.toggle("is-error", !!view.error);
    var button = mount.querySelector("#saveBtn");
    button.disabled = !view.canSave;
    setPreviewText(button, saving ? "保存中…" : "保存");
    /* 等待和失败时保留已挂载的行，避免列表高度收缩导致页面跳动。 */
    if (!view.pending && !view.error) syncPreviewRows(list, view.rows, agent);
    list.setAttribute("aria-busy", String(view.pending));
    var message = mount.querySelector(".preview-message");
    message.hidden = !!list.querySelector(".preview-row");
    setPreviewText(message, view.message);
}

function modelFieldsHtml(agent) {
    if (agent !== "claude") {
        return '<div class="model-fields-grid"><div class="form-field"><label class="form-label"><span>模型</span>' +
            '<button type="button" class="field-help-button" data-help-topic="' + agent + '_model" aria-label="查看模型说明">?</button></label>' +
            comboTriggerHtml(agent + "Model") + "</div></div>";
    }
    var rows = CLAUDE_MODEL_KEYS.map(function (key) {
        return '<div class="form-field"><label class="form-label"><code>' + key + '</code>' +
            '<button type="button" class="field-help-button" data-help-model="' + key + '" aria-label="查看 ' + key + ' 说明">?</button></label>' +
            comboTriggerHtml("claudeModel", key) + "</div>";
    }).join("");
    var value = uniformClaudeModel();
    var uniformControl = isOwnServiceUrl(mDraft("claude").baseUrl)
        ? comboTriggerHtml("claudeAllModels")
        : '<input class="form-control model-id-input" id="allModelsInput" type="text" value="' + esc(value) + '" placeholder="输入模型 ID" autocomplete="off" spellcheck="false">';
    return '<div class="model-settings-group" role="group" aria-label="Claude 模型设置">' +
        '<div class="form-field model-batch-field"><label class="form-label"><span>一键设置模型</span>' +
        '<button type="button" class="field-help-button" data-help-topic="claude_all_models" aria-label="查看一键设置模型说明">?</button></label>' + uniformControl + "</div>" +
        '<div class="model-fields-grid">' + rows + "</div></div>";
}

function renderClaude(draft) {
    var notices = renderParseNotice("claude");
    var runtimeHtml = selectField("权限模式", "claudePermission", CLAUDE_PERMISSION_MODES, draft.runtime.permissionMode, "claude_permission") +
        triStateField(CLAUDE_SANDBOX_SETTING, draft.runtime.sandboxEnabled) +
        CLAUDE_MODE_SETTINGS.map(function (s) { return triStateField(s, draft.runtime[s.key]); }).join("") +
        triStateField(CLAUDE_WEBFETCH_SETTING, draft.runtime.skipWebFetchPreflight);
    return '<div class="section-header"><div><h2 class="section-title">Claude · ' + esc(selectedMachine().name) + '</h2></div></div>' +
        '<div class="path-strip">' + renderPathRow("settings.json", "claude") + "</div>" + notices +
        '<div class="agent-form"><div class="api-fields-grid">' +
        apiAddressField(draft.baseUrl) +
        '<div id="apiKeyMount">' + apiKeyFieldHtml(draft) + "</div>" +
        "</div>" +
        '<div id="modelFieldsMount">' + modelFieldsHtml("claude") + "</div>" +
        limitFieldsHtml(draft) +
        "</div>" +
        '<div class="runtime-section" role="group" aria-label="运行设置"><div class="runtime-grid">' + runtimeHtml + "</div></div>" +
        '<div id="previewMount">' + previewHtml("claude") + "</div>";
}

function renderCodex(draft) {
    var notices = renderParseNotice("codex") + renderParseNotice("codexAuth");
    var runtimeHtml =
        selectField("Service tier", "codexTier", CODEX_SERVICE_TIERS, draft.runtime.serviceTier, "codex_tier") +
        selectField("Sandbox mode", "codexSandbox", CODEX_SANDBOX_MODES, draft.runtime.sandboxMode, "codex_sandbox") +
        selectField("Approval policy", "codexApproval", CODEX_APPROVAL_POLICIES, draft.runtime.approvalPolicy, "codex_approval") +
        triStateField(CODEX_MEMORY_SETTING, draft.runtime.memories);
    return '<div class="section-header"><div><h2 class="section-title">Codex · ' + esc(selectedMachine().name) + '</h2></div></div>' +
        '<div class="path-strip">' + renderPathRow("config.toml", "codex") + renderPathRow("auth.json", "codexAuth") + "</div>" + notices +
        '<div class="agent-form"><div class="api-fields-grid">' +
        apiAddressField(draft.baseUrl) +
        '<div id="apiKeyMount">' + apiKeyFieldHtml(draft) + "</div>" +
        "</div>" +
        '<div id="modelFieldsMount">' + modelFieldsHtml("codex") + "</div>" +
        limitFieldsHtml(draft) +
        "</div>" +
        '<div class="runtime-section" role="group" aria-label="运行设置"><div class="runtime-grid">' + runtimeHtml + "</div></div>" +
        '<div id="previewMount">' + previewHtml("codex") + "</div>";
}

function renderOpencode(draft) {
    var notices = renderParseNotice("opencode");
    return '<div class="section-header"><div><h2 class="section-title">opencode · ' + esc(selectedMachine().name) + '</h2></div></div>' +
        '<div class="path-strip">' + renderPathRow("opencode.json", "opencode") + "</div>" + notices +
        '<div class="agent-form"><div class="api-fields-grid">' +
        apiAddressField(draft.baseUrl) +
        '<div id="apiKeyMount">' + apiKeyFieldHtml(draft) + "</div>" +
        "</div>" +
        '<div id="modelFieldsMount">' + modelFieldsHtml("opencode") + "</div>" +
        limitFieldsHtml(draft) +
        "</div>" +
        '<div id="previewMount">' + previewHtml("opencode") + "</div>";
}

function updatePreview() {
    if (!selectedMachine() || !selectedMachine().connected || !ensureMachineState(selectedMachine()).files) return;
    var id = selectedMachineId, agent = activeAgent, key = formKey(agent), cacheId = previewId(id, agent);
    var cached = previewCache[cacheId];
    if (!cached || cached.key !== key) {
        clearTimeout(previewTimer);
        previewCache[cacheId] = { key: key, pending: true };
        previewTimer = setTimeout(function () { requestFormPreview(id, agent, key); }, 240);
    } else if (cached.pending && !cached.started) {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(function () { requestFormPreview(id, agent, key); }, 240);
    }
    syncPreview(agent);
}

/* ---------- 帮助浮窗 ---------- */
var HELP_TOPICS = {
    api_address: {
        title: "API 地址",
        html: "<p>Agent 访问模型服务的地址，写入 Agent 配置文件：</p><ul>" +
            "<li>Claude 不带路径后缀，例如 <code>http://127.0.0.1:8080</code></li>" +
            "<li>Codex / opencode 带 <code>/v1</code> 后缀</li>" +
            "<li>地址可自由填写，远程机器请选择可访问的网卡或域名地址</li>" +
            "<li>本服务地址使用模型下拉，外部地址手动输入对应服务的模型 ID</li></ul>"
    },
    api_unreachable: {
        title: "地址可能不可达",
        html: function () {
            return "<p>系统设置的监听 IP 当前为 <code>" + esc(SERVER_BIND_HOST) + "</code>，目标机器无法直接访问本服务。</p>" +
                "<ul><li>到 系统设置 将监听 IP 改为 <code>0.0.0.0</code> 或目标机器可达的网卡地址</li>" +
                "<li>也可填写目标机器可访问的反向代理地址</li></ul>";
        }
    },
    api_key: {
        title: "API Key",
        html: "<p>当前地址识别为本服务（LLM Proxy），且服务已启用 API Key 管理，Agent 访问需要凭据：</p>" +
            "<ul><li>聚焦输入框会从已创建的 Key 中给出候选，点击即填入</li>" +
            "<li>或到 API Key 管理页查看并自行粘贴到这里</li></ul>" +
            "<p>留空则不写入，保存前请确认服务端的校验要求。</p>"
    },
    api_key_external: {
        title: "API Key",
        html: "<p>该地址上游服务需要的访问凭据，写入 Agent 配置文件：</p>" +
            "<ul><li>留空则不写入（上游不校验时可留空）</li>" +
            "<li>输入始终以密文展示，保存预览中打码</li></ul>"
    },
    proxy_url: {
        title: "自定义代理",
        html: "<p>格式 <code>http://user:pass@host:port</code>，账号密码可选，支持 <code>http</code> / <code>https</code> / <code>socks5</code> / <code>socks5h</code>。</p>" +
            "<p>账号密码中的 @、: 等特殊字符使用百分号编码。SSH 密码与代理凭据仅在当前浏览器会话中暂存。</p>"
    },
    claude_all_models: { title: "一键设置模型", html: "<p>一次设置全部四个模型变量。本服务地址下选择即同步；外部地址下输入模型 ID 即同步，清空输入也会清空四项。</p><p>单独修改模型后，四项不一致时此处留空；全部一致时显示共同的 ID。点击保存后写入 <code>settings.json</code>。</p>" },
    claude_permission: {
        title: "权限模式",
        html: "<p>写入 <code>permissions.defaultMode</code>，作为新会话的启动模式；命令行 <code>--permission-mode</code> 优先。</p>" +
            "<ul><li><code>default</code>：需要时由你确认</li><li><code>acceptEdits</code>：自动接受文件编辑及部分文件操作</li>" +
            "<li><code>plan</code>：先分析和规划，批准计划后执行</li><li><code>auto</code>：由后台分类器审核操作，依赖版本和服务支持，可能产生额外请求</li>" +
            "<li><code>dontAsk</code>：拒绝需要确认且未获授权的操作</li><li><code>bypassPermissions</code>：跳过权限确认，拒绝规则及部分强制检查仍生效</li></ul>" +
            "<p>当前版本的 <code>auto</code> 和 <code>bypassPermissions</code> 应设置在用户级 <code>~/.claude/settings.json</code>；项目级文件不生效。组织策略仍可限制模式使用。「不设置」的操作见保存预览。</p>" +
            "<p>Linux/macOS 的 root 用户还受启动环境检查：<code>IS_SANDBOX=1</code> 等标记参与隔离环境识别，<code>sandbox.enabled</code> 不能替代它。普通用户与原生 Windows 不受这项 root 检查影响。</p>"
    },
    claude_sandbox: {
        title: "命令沙箱",
        html: "<p>写入布尔值 <code>sandbox.enabled</code>，控制 Bash 命令的文件系统与网络隔离。</p>" +
            "<p>支持 macOS、Linux、WSL2；原生 Windows 不支持。Linux / WSL2 需要 bubblewrap、socat 等运行依赖，启动失败时是否拒绝命令取决于已有的 <code>sandbox.failIfUnavailable</code> 设置。</p>" +
            "<p>权限模式控制操作是否需要批准；命令沙箱控制执行边界。<code>IS_SANDBOX</code> 只是运行环境标记，不会开启此沙箱。</p>"
    },
    claude_webfetch_preflight: {
        title: "关闭 WebFetch 预检",
        html: "<p>写入 <code>settings.json</code> 顶层的布尔值 <code>skipWebFetchPreflight</code>。启用写 <code>true</code>，禁用写 <code>false</code>，不设置则移除该字段。</p>" +
            "<p>启用后跳过抓取网页前向 <code>api.anthropic.com</code> 发起的域名安全检查，适用于无法访问 Anthropic 的网络环境。</p>" +
            "<p>WebFetch 仍可抓取网页，访问范围由权限规则控制；此项不控制 WebSearch。</p>"
    },
    limit_context: {
        title: function () { return activeAgent === "claude" ? "压缩窗口" : "上下文窗口"; },
        html: function () {
            if (activeAgent === "claude") return "<p>自动压缩使用的窗口，单位为 Token，写入 <code>env.CLAUDE_CODE_AUTO_COMPACT_WINDOW</code>。此项不会扩展模型的真实上下文容量。</p>" +
                "<p>当前版本支持 100,000–1,000,000；小于下限会被忽略，超过上限按 1,000,000 处理（例如 1,048,576），且不超过模型自身窗口。文件中的原值保留在输入框，保存预览显示实际写入值。</p>";
            var details = {
                codex: "Codex 使用的上下文预算，写入 <code>model_context_window</code>。",
                opencode: "当前模型的上下文容量，写入模型配置的 <code>limit.context</code>。"
            };
            return "<p>单位为 Token。历史消息、工具结果和模型输出共同占用上下文，应按模型实际支持的容量填写。</p><p>" + details[activeAgent] + "</p>";
        }
    },
    limit_compact: {
        title: "压缩阈值",
        html: function () {
            if (activeAgent === "claude") return "<p>自动压缩窗口的触发比例，范围 1–100%，写入 <code>env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE</code>。</p>" +
                "<p>只能提前压缩，不能提高默认触发阈值；高于默认值时由 Claude 忽略。仅在模型上下文耗尽前自动压缩的会话中适用，实际 Token 预算还受输出预留影响。</p>";
            var details = {
                codex: "按「上下文窗口 × 百分比」计算压缩上限，写入 <code>model_auto_compact_token_limit</code>。未修改时保留文件原有的精确 Token 值。",
                opencode: "按「上下文窗口 × (1 − 百分比)」计算压缩预留空间，写入 <code>compaction.reserved</code>。"
            };
            return "<p>自动压缩使用的上下文比例，范围为 1–99%。例如 200,000 Token 的窗口设为 85%，对应约 170,000 Token 的预算；数值越小，越早压缩。</p><p>" + details[activeAgent] + "</p>";
        }
    },
    limit_output: {
        title: "最大输出",
        html: function () {
            return "<p>单次回复允许生成的最大 Token 数，实际输出受模型与上游服务的限制。</p><p>" +
                (activeAgent === "claude"
                    ? "写入 <code>env.CLAUDE_CODE_MAX_OUTPUT_TOKENS</code>，按填写值保存。当前 Claude Code 按模型上限裁剪；无法识别的模型默认 32,000、上限 128,000。增大输出预算会提前占用压缩前的可用空间。"
                    : "写入当前模型配置的 <code>limit.output</code>。") + "</p>";
        }
    },
    codex_model: { title: "Codex 模型", html: "<p>Codex 默认使用的模型，写入 <code>config.toml</code> 的 <code>model</code>。</p><p>本服务使用完整模型 ID；外部 API 填写对应服务的模型 ID。</p>" },
    opencode_model: { title: "opencode 模型", html: "<p>opencode 默认使用的模型，写入 <code>opencode.json</code> 的 <code>model</code>，并关联对应 Provider。</p><p>本服务使用完整模型 ID；外部 API 填写对应服务的模型 ID。</p>" },
    claude_effort: {
        title: "思考强度",
        html: "<p>控制模型投入推理的程度。可用档位取决于模型和上游服务，第三方模型未必支持。</p><p>写入 <code>env.CLAUDE_CODE_EFFORT_LEVEL</code>，优先于 <code>/effort</code> 和启动参数；<code>auto</code> 表示不发送强度，由 API 决定默认值。「不设置」的操作见保存预览。</p>"
    },
    codex_tier: {
        title: "Service tier",
        html: "<p>写入 <code>service_tier</code>。实际可用档位取决于模型和上游 Provider。</p><ul><li>auto：使用项目配置档位</li><li>default：标准处理</li><li>flex：弹性处理，成本更低、响应更慢</li><li>priority：优先处理（Fast），延迟更低、成本更高</li></ul>"
    },
    codex_effort: { title: "思考强度", html: "<p>控制模型投入推理的程度。更高档位通常需要更多时间与 Token，可用档位取决于模型。</p><p>写入 <code>model_reasoning_effort</code>。「不设置」的具体操作见保存预览。</p>" },
    codex_sandbox: { title: "Sandbox mode", html: "<p>写入 <code>sandbox_mode</code>，控制 Codex 执行命令时的文件系统权限。</p>" },
    codex_approval: { title: "Approval policy", html: "<p>写入 <code>approval_policy</code>，控制命令执行前的确认策略。</p>" },
    codex_memories: {
        title: "Codex 记忆",
        html: "<p>允许 Codex 生成和使用跨会话记忆，复用工作偏好与项目经验。</p><p>写入 <code>config.toml</code> 的 <code>features.memories</code>：启用为 <code>true</code>，禁用为 <code>false</code>，选择「不设置」后保存会移除该字段。</p><p>未设置时沿用 Codex 的默认行为，当前默认关闭。</p>"
    },
    opencode_effort: { title: "思考强度", html: "<p>控制模型投入推理的程度。更高档位通常需要更多时间与 Token，可用档位取决于模型。</p><p>写入所选模型的 <code>options.reasoningEffort</code>。「不设置」的具体操作见保存预览。</p>" }
};
var helpPopoverEl = null;
var helpTriggerEl = null;
var helpHideTimer = null;
var CLAUDE_MODEL_HELP = {
    ANTHROPIC_DEFAULT_HAIKU_MODEL: "Haiku 别名对应的模型，通常用于快速、轻量任务。",
    ANTHROPIC_DEFAULT_OPUS_MODEL: "Opus 别名对应的模型，通常用于复杂分析和编程任务。",
    ANTHROPIC_DEFAULT_SONNET_MODEL: "Sonnet 别名对应的模型，通常用于日常编程，兼顾速度与能力。",
    ANTHROPIC_MODEL: "Claude Code 的默认主模型。启动参数或会话中的模型选择可以覆盖此值。"
};

function modelParameterHelp(modelId) {
    if (!modelId) return "<p>选择模型后可查看上下文和最大输出参考值。</p>";
    var candidates = modelParameterCandidates(modelId);
    function values(key) { return candidates[key].map(function (item) { return Number(item.value).toLocaleString(); }).join(" / "); }
    return '<p><code>' + esc(modelId) + '</code></p><dl class="model-parameter-help"><dt>上下文</dt><dd>' + values("context") +
        '</dd><dt>最大输出</dt><dd>' + values("output") + '</dd></dl>' + (candidates.matched ? "" : "<p>模型未识别，以上为通用参考值。</p>");
}

function showHelp(button) {
    clearHelpHideTimer();
    if (helpPopoverEl && helpTriggerEl === button) return;
    var topicKey = button.getAttribute("data-help-topic");
    var env = button.getAttribute("data-help-env");
    var model = button.getAttribute("data-help-model");
    var topic = topicKey ? HELP_TOPICS[topicKey] : null;
    if (!topic && model && CLAUDE_MODEL_HELP[model]) {
        topic = {
            title: model,
            html: "<p>" + CLAUDE_MODEL_HELP[model] + "</p>" + modelParameterHelp(mDraft("claude").models[model]) + "<p>写入 <code>settings.json</code> 的 <code>env." + model + "</code>。</p><p>选择「不设置」时，保存预览会列出该覆盖项的移除操作。</p>"
        };
    }
    if (!topic && env) {
        var setting = CLAUDE_MODE_SETTINGS.filter(function (s) { return s.env === env; })[0];
        topic = setting ? {
            title: setting.label,
            html: "<p>" + esc(setting.description) + "</p><p>写入 <code>env." + setting.env + "</code>。</p><ul><li>" +
                (setting.enabledLabel || "启用") + " → <code>" + setting.enabled + "</code></li><li>" +
                (setting.disabledLabel || "禁用") + " → " + (setting.disabled === null ? "移除变量" : "<code>" + setting.disabled + "</code>") + "</li></ul><p>「不设置」的具体操作见保存预览。</p>"
        } : null;
    }
    if (!topic) return;
    closeHelp();
    var bodyHtml = typeof topic.html === "function" ? topic.html() : topic.html;
    if (topicKey === "codex_model" || topicKey === "opencode_model") bodyHtml += modelParameterHelp(mDraft(activeAgent).model);
    var titleText = typeof topic.title === "function" ? topic.title() : topic.title;
    var root = document.getElementById("helpPopoverRoot");
    root.innerHTML = '<div class="help-popover" id="agentHelpPopover" role="dialog" aria-labelledby="agentHelpTitle" data-topic="' + esc(topicKey || env || model) + '">' +
        '<div class="help-popover-header"><span class="help-popover-title" id="agentHelpTitle">' + esc(titleText) + '</span>' +
        '<button type="button" class="help-popover-close" data-action="close-help" aria-label="关闭">×</button></div>' +
        '<div class="help-popover-body">' + bodyHtml + "</div></div>";
    helpPopoverEl = root.querySelector(".help-popover");
    helpTriggerEl = button;
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-controls", "agentHelpPopover");
    var rect = button.getBoundingClientRect();
    var width = Math.min(460, window.innerWidth - 24);
    var left = Math.min(Math.max(12, rect.left + (rect.width - width) / 2), window.innerWidth - width - 12);
    var height = helpPopoverEl.getBoundingClientRect().height;
    var placeBottom = rect.bottom + 10 + height <= window.innerHeight - 12 || rect.top < height + 22;
    var top = placeBottom
        ? Math.max(12, Math.min(window.innerHeight - height - 12, rect.bottom + 10))
        : Math.max(12, rect.top - 10 - height);
    var arrowLeft = Math.min(width - 18, Math.max(18, rect.left + rect.width / 2 - left));
    helpPopoverEl.dataset.placement = placeBottom ? "bottom" : "top";
    helpPopoverEl.style.left = left + "px";
    helpPopoverEl.style.top = top + "px";
    helpPopoverEl.style.setProperty("--popover-arrow-left", arrowLeft + "px");
}
function clearHelpHideTimer() {
    clearTimeout(helpHideTimer);
    helpHideTimer = null;
}
function scheduleHelpHide() {
    clearHelpHideTimer();
    /* 留出从问号移入说明框的时间。 */
    helpHideTimer = setTimeout(closeHelp, 160);
}
function isHelpRelatedTargetWithin(element, event) {
    return element && event.relatedTarget instanceof Node && element.contains(event.relatedTarget);
}
function closeHelp() {
    clearHelpHideTimer();
    if (helpTriggerEl) {
        helpTriggerEl.setAttribute("aria-expanded", "false");
        helpTriggerEl.removeAttribute("aria-controls");
    }
    document.getElementById("helpPopoverRoot").innerHTML = "";
    helpPopoverEl = null;
    helpTriggerEl = null;
}

/* ---------- 模态框 ---------- */
var ICON_EYE = '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
var ICON_EYE_OFF = '<svg viewBox="0 0 24 24"><path d="M17.94 17.94A10.6 10.6 0 0 1 12 19c-6.5 0-10-7-10-7a17.6 17.6 0 0 1 4.06-4.94M9.9 4.24A9.5 9.5 0 0 1 12 4c6.5 0 10 7 10 7a17.7 17.7 0 0 1-2.16 3.19"></path><path d="M14.12 14.12A3 3 0 1 1 9.88 9.88"></path><path d="M2 2l20 20"></path></svg>';

function closeModal() {
    if (modalPending) return;
    closeHelp();
    if (rawEditor && rawEditor.view) rawEditor.view.destroy();
    if (rawEditor && rawEditor.themeObserver) rawEditor.themeObserver.disconnect();
    document.getElementById("modalRoot").innerHTML = "";
    document.body.style.overflow = "";
    machineModal = null;
    rawEditor = null;
    if (modalResolve) { var resolve = modalResolve; modalResolve = null; resolve(false); }
    if (modalReturnFocus && modalReturnFocus.isConnected) modalReturnFocus.focus();
    modalReturnFocus = null;
    if (selectedMachine() && selectedMachine().connected) updatePreview();
}

function requestCloseModal() {
    if (modalPending) return;
    if (!rawEditor || !rawEditor.dirty) { closeModal(); return; }
    if (rawEditor.confirmClosing) return;
    rawEditor.confirmClosing = true;
    var dialog = document.getElementById("rawEditorDialog");
    dialog.inert = true;
    dialog.setAttribute("aria-hidden", "true");
    document.getElementById("rawClosePrompt").hidden = false;
    document.getElementById("rawContinueBtn").focus();
}

function continueRawEditing() {
    if (!rawEditor || !rawEditor.confirmClosing || modalPending) return;
    rawEditor.confirmClosing = false;
    document.getElementById("rawClosePrompt").hidden = true;
    var dialog = document.getElementById("rawEditorDialog");
    dialog.inert = false;
    dialog.removeAttribute("aria-hidden");
    rawEditor.view.focus();
}

function activeModalDialog() {
    return rawEditor && rawEditor.confirmClosing
        ? document.querySelector("#rawClosePrompt [role=alertdialog]")
        : document.querySelector("#modalRoot [role=dialog]");
}

/* 自定义路径 */
function openPathModal(target) {
    target = target === "codexAuth" ? "codex" : target;
    var f = mFiles(target);
    var label = target === "codex" ? "Codex 配置目录" : (target === "claude" ? "Claude" : "opencode") + " 配置文件";
    var defaultPath = target === "codex" ? f.defaultDirectory || f.defaultPath.replace(/[\\/][^\\/]*$/, "") : f.defaultPath;
    var root = document.getElementById("modalRoot");
    root.innerHTML = '<div class="modal-overlay" data-action="modal-backdrop">' +
        '<div class="modal-dialog" role="dialog" aria-modal="true">' +
        '<div class="modal-content">' +
        '<div class="modal-header"><h3 class="modal-title">' + label + '</h3>' +
        '<button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>' +
        '<div class="modal-body">' +
        '<p class="modal-subtitle">' + (target === "codex" ? '目录内使用 config.toml 和 auth.json。' : '') + '留空使用默认路径。</p>' +
        '<div class="form-field"><label class="form-label" for="customPathInput">' + (target === "codex" ? '目录' : '文件路径') + '</label>' +
        '<input class="form-control" id="customPathInput" spellcheck="false" autocomplete="off" aria-describedby="customPathError" value="' + esc(f.customPath) + '" placeholder="' + esc(defaultPath) + '">' +
        '<span class="machine-field-error" id="customPathError" role="alert" hidden></span></div></div>' +
        '<div class="modal-footer">' +
        (f.customPath ? '<button type="button" class="btn btn-secondary" data-action="reset-path" data-path-target="' + target + '">恢复默认</button>' : "") +
        '<button type="button" class="btn btn-secondary" data-action="close-modal">取消</button>' +
        '<button type="button" class="btn btn-primary" data-action="apply-path" data-path-target="' + target + '">保存路径</button>' +
        "</div></div></div></div>";
    document.getElementById("customPathInput").focus();
}

/* 机器新增 / 编辑 */
var machineModal = null;

function openMachineModal(id) {
    var machine = id ? machines.filter(function (m) { return m.id === id; })[0] : null;
    machineModal = machine
        ? { mode: "edit", id: machine.id, name: machine.name, host: machine.host, port: machine.port, user: machine.user, password: machine.password,
            proxyMode: machine.proxy.mode || "direct", proxyUrl: machine.proxy.url || "" }
        : { mode: "add", id: "", name: "", host: "", port: "22", user: "", password: "",
            proxyMode: "direct", proxyUrl: "" };
    renderMachineModal();
}

function renderMachineModal() {
    closeHelp();
    var mm = machineModal;
    var root = document.getElementById("modalRoot");
    var proxyCustom = mm.proxyMode === "custom";
    var title = mm.mode === "add" ? "新增机器" : "编辑机器";
    var primaryLabel = mm.mode === "add" ? "保存并连接" : "保存并重连";
    root.innerHTML = '<div class="modal-overlay" data-action="modal-backdrop">' +
        '<div class="modal-dialog" role="dialog" aria-modal="true">' +
        '<div class="modal-content">' +
        '<div class="modal-header"><h3 class="modal-title">' + title + '</h3>' +
        '<button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>' +
        '<div class="modal-body">' +
        '<p class="modal-subtitle">通过 SSH 连接远程机器，读写其上的 Agent 配置文件。</p>' +
        '<div class="modal-form-grid">' +
        '<div class="form-field"><label class="form-label"><span>名称</span></label>' +
        '<input class="form-control" id="mmName" value="' + esc(mm.name) + '" placeholder="例如 开发服务器" maxlength="48"></div>' +
        '<div class="modal-form-row host-port">' +
        '<div class="form-field"><label class="form-label"><span>IP / 主机</span></label>' +
        '<input class="form-control" id="mmHost" value="' + esc(mm.host) + '" placeholder="192.168.1.24" spellcheck="false" required aria-describedby="mmHostError">' +
        '<span class="machine-field-error" id="mmHostError" hidden></span></div>' +
        '<div class="form-field"><label class="form-label"><span>端口</span></label>' +
        '<input class="form-control" id="mmPort" type="number" min="1" max="65535" value="' + esc(mm.port) + '" aria-describedby="mmPortError">' +
        '<span class="machine-field-error" id="mmPortError" hidden></span></div>' +
        "</div>" +
        '<div class="modal-form-row">' +
        '<div class="form-field"><label class="form-label"><span>用户</span></label>' +
        '<input class="form-control" id="mmUser" value="' + esc(mm.user) + '" placeholder="ubuntu" spellcheck="false" required aria-describedby="mmUserError">' +
        '<span class="machine-field-error" id="mmUserError" hidden></span></div>' +
        '<div class="form-field"><label class="form-label"><span>密码</span></label>' +
        '<span class="sensitive-shell"><input class="form-control" id="mmPassword" type="password" value="' + esc(mm.password) + '" autocomplete="off" placeholder="可选">' +
        '<button type="button" class="sensitive-toggle" data-action="toggle-mm-password" aria-label="显示密码">' + ICON_EYE + "</button></span></div>" +
        "</div>" +
        '<div class="proxy-grid' + (proxyCustom ? " is-custom" : "") + '">' +
        '<div class="form-field' + (proxyCustom ? " proxy-mode-field" : " field-narrow") + '"><label class="form-label"><span>代理模式</span></label>' +
        '<select class="form-control" id="mmProxyMode">' +
        '<option value="direct"' + (mm.proxyMode === "direct" ? " selected" : "") + ">直连</option>" +
        '<option value="system"' + (mm.proxyMode === "system" ? " selected" : "") + ">系统代理</option>" +
        '<option value="custom"' + (mm.proxyMode === "custom" ? " selected" : "") + ">自定义代理</option></select></div>" +
        (proxyCustom
            ? '<div class="form-field proxy-url-field"><label class="form-label"><span>代理地址</span>' +
                '<button type="button" class="field-help-button" data-help-topic="proxy_url" aria-label="查看说明">?</button></label>' +
                '<span class="sensitive-shell"><input class="form-control" id="mmProxyUrl" type="password" value="' + esc(mm.proxyUrl) + '" placeholder="http://user:pass@127.0.0.1:7890" autocomplete="off" spellcheck="false" required aria-describedby="mmProxyUrlError">' +
                '<button type="button" class="sensitive-toggle" data-action="toggle-mm-proxy-url" aria-label="显示代理地址">' + ICON_EYE + '</button></span>' +
                '<span class="machine-field-error" id="mmProxyUrlError" hidden></span></div>'
            : "") +
        "</div>" +
        "</div></div>" +
        '<div class="modal-footer">' +
        '<button type="button" class="btn btn-secondary" data-action="close-modal">取消</button>' +
        '<button type="button" class="btn btn-secondary" data-action="save-machine" data-connect="0">保存</button>' +
        '<button type="button" class="btn btn-primary" data-action="save-machine" data-connect="1">' + primaryLabel + "</button>" +
        "</div></div></div></div>";
    if (mm.validationAttempted) updateMachineValidation(false);
}

function collectMachineModal() {
    var mm = machineModal;
    function val(id) {
        var el = document.getElementById(id);
        return el ? el.value : "";
    }
    mm.name = val("mmName").trim();
    mm.host = val("mmHost").trim();
    mm.port = val("mmPort").trim() || "22";
    mm.user = val("mmUser").trim();
    mm.password = val("mmPassword");
    mm.proxyMode = val("mmProxyMode") || mm.proxyMode || "direct";
    mm.proxyUrl = val("mmProxyUrl").trim();
}

function updateMachineValidation(focusFirst) {
    var mm = machineModal, errors = {};
    var host = mm.host.replace(/^\[|\]$/g, "");
    if (!host) errors.mmHost = "请填写 IP 或主机名";
    else if (host.length > 253 || /[\s\x00-\x1f/\\@?#]/.test(host)) errors.mmHost = "请填写有效的 IP 或主机名";
    var portInput = document.getElementById("mmPort");
    if ((portInput && portInput.validity && portInput.validity.badInput) || !/^\d+$/.test(mm.port) || Number(mm.port) < 1 || Number(mm.port) > 65535) {
        errors.mmPort = "请输入 1–65535 的整数";
    }
    if (!mm.user) errors.mmUser = "请填写 SSH 用户名";
    else if (mm.user.length > 128 || /[\x00-\x1f]/.test(mm.user)) errors.mmUser = "请填写有效的 SSH 用户名";
    if (mm.proxyMode === "custom") {
        if (!mm.proxyUrl) errors.mmProxyUrl = "请填写代理地址";
        else if (!parseProxyUrl(mm.proxyUrl)) errors.mmProxyUrl = "请填写有效的 HTTP / SOCKS5 代理地址";
    }
    var firstInvalid = null;
    ["mmHost", "mmPort", "mmUser", "mmProxyUrl"].forEach(function (id) {
        var input = document.getElementById(id), message = document.getElementById(id + "Error");
        if (!input || !message) return;
        var error = errors[id] || "";
        input.setAttribute("aria-invalid", String(!!error));
        var field = input.closest(".form-field");
        if (field) field.classList.toggle("has-error", !!error);
        message.textContent = error;
        message.hidden = !error;
        if (error && !firstInvalid) firstInvalid = input;
    });
    if (focusFirst && firstInvalid) firstInvalid.focus();
    return Object.keys(errors).length === 0;
}

async function applyMachineModal(connect) {
    if (modalPending) return;
    collectMachineModal();
    var mm = machineModal;
    mm.validationAttempted = true;
    if (!updateMachineValidation(true)) return;
    modalPending = true;
    modalBusy(true);
    try {
        var result = await api("/machines" + (mm.id ? "/" + encodeURIComponent(mm.id) : ""), mm.id ? "PUT" : "POST", {
            name: mm.name, host: mm.host, port: mm.port, user: mm.user, password: mm.password,
            proxy: { mode: mm.proxyMode, url: mm.proxyMode === "custom" ? mm.proxyUrl : "" }
        });
        var index = machines.findIndex(function (machine) { return machine.id === result.machine.id; });
        if (index === -1) machines.push(result.machine); else machines[index] = result.machine;
        delete machineState[result.machine.id];
        clearPreviews(result.machine.id);
        selectedMachineId = result.machine.id;
        modalPending = false;
        closeModal();
        render();
        if (connect) await connectMachine(result.machine.id);
        else toast("success", "机器已保存");
    } catch (error) { showError(error); }
    finally { modalPending = false; modalBusy(false); }
}

async function connectMachine(id) {
    var machine = machines.find(function (item) { return item.id === id; });
    if (!machine || machine.connected || machine.connecting) return;
    machine.connecting = true;
    render();
    try {
        var result;
        try { result = await api(machineUrl(id, "/connect"), "POST", {}); }
        catch (error) {
            if (error.code !== "host_key_required") throw error;
            var accepted = await confirmDialog("确认 SSH 主机", '<p class="modal-subtitle">' + esc(connText(machine)) + '</p><code class="host-fingerprint">' + esc(error.fingerprint) + '</code><p class="modal-subtitle">' + esc(error.key_type) + '</p>', "信任并连接");
            if (!accepted) return;
            result = await api(machineUrl(id, "/connect"), "POST", { trustFingerprint: error.fingerprint });
        }
        installSnapshot(id, result, "all");
        toast("success", "已连接 " + machine.name);
    } catch (error) { showError(error); }
    finally { machine.connecting = false; render(); }
}

async function disconnectMachine(id) {
    var machine = machines.find(function (item) { return item.id === id; });
    if (!machine || machine.local) return;
    try {
        await api(machineUrl(id, "/disconnect"), "POST", {});
        machine.connected = false;
        clearPreviews(id);
        render();
        toast("success", "已断开 " + machine.name);
    } catch (error) { showError(error); }
}

async function removeMachine(id) {
    if (modalPending) return;
    modalPending = true;
    modalBusy(true);
    try {
        await api(machineUrl(id), "DELETE", {});
        machines = machines.filter(function (machine) { return machine.id !== id; });
        delete machineState[id];
        clearPreviews(id);
        if (selectedMachineId === id) selectedMachineId = "local";
        modalPending = false;
        closeModal();
        render();
        toast("success", "机器已删除");
    } catch (error) { showError(error); }
    finally { modalPending = false; modalBusy(false); }
}

function confirmRemoveMachine(id) {
    var machine = machines.filter(function (m) { return m.id === id; })[0];
    if (!machine) return;
    var root = document.getElementById("modalRoot");
    root.innerHTML = '<div class="modal-overlay" data-action="modal-backdrop">' +
        '<div class="modal-dialog" role="dialog" aria-modal="true">' +
        '<div class="modal-content">' +
        '<div class="modal-header"><h3 class="modal-title">删除机器</h3>' +
        '<button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>' +
        '<div class="modal-body"><p class="modal-subtitle">确定删除「' + esc(machine.name) + '」（' + esc(connText(machine)) + '）？该机器的 SSH 配置将被移除。</p></div>' +
        '<div class="modal-footer">' +
        '<button type="button" class="btn btn-secondary" data-action="close-modal">取消</button>' +
        '<button type="button" class="btn btn-primary" data-action="confirm-remove-machine" data-machine-id="' + machine.id + '">删除</button>' +
        "</div></div></div></div>";
}

function initDraftAll(machine) {
    var st = ensureMachineState(machine);
    st.drafts = { claude: null, codex: null, opencode: null };
    ["claude", "codex", "opencode"].forEach(function (agent) {
        initDraft(machine, agent);
        st.baselines[agent] = JSON.stringify(st.drafts[agent]);
    });
}

/* ---------- Toast ---------- */
function toast(kind, message) {
    var container = document.getElementById("ui-toast-container");
    var el = document.createElement("div");
    el.className = "toast show toast-" + kind;
    el.innerHTML = '<div class="toast-body">' + esc(message) + '</div>' +
        '<button type="button" class="btn-close" aria-label="关闭">×</button>';
    el.querySelector(".btn-close").addEventListener("click", function () { el.remove(); });
    container.appendChild(el);
    setTimeout(function () { el.remove(); }, 4200);
}

/* ---------- 保存 ---------- */
async function saveActiveAgent() {
    if (saving) return;
    var id = selectedMachineId, agent = activeAgent, st = ensureMachineState(selectedMachine());
    saving = true;
    document.querySelector(".agents-section").inert = true;
    updatePreview();
    try {
        var preview = await api(machineUrl(id, "/" + agent + "/preview"), "POST", { versions: versionsFor(st), draft: st.drafts[agent] });
        if (!preview.hasChanges) { toast("success", "配置已是最新"); return; }
        var result = await api(machineUrl(id, "/apply"), "POST", { token: preview.token });
        installSnapshot(id, result, agent);
        toast("success", "配置已保存，已有文件保留最近 5 份备份");
    } catch (error) { showError(error); clearPreviews(id, agent); }
    finally { saving = false; document.querySelector(".agents-section").inert = false; render(); }
}

/* ---------- 事件 ---------- */
document.addEventListener("click", function (event) {
    closeLimitCandidates(event.target.closest(".limit-candidates"));
    var helpBtn = event.target.closest(".field-help-button");
    if (helpBtn) {
        showHelp(helpBtn);
        return;
    }
    if (helpPopoverEl && !event.target.closest(".help-popover")) closeHelp();
    var keyInput = event.target;
    if (keyInput.id === "apiKeyInput" && keyInput.hasAttribute("data-apikey-candidates") && !apikeyMenuEl) {
        openApiKeyCandidates(keyInput);
    }
    var keyCand = event.target.closest(".apikey-candidate");
    if (keyCand) {
        var keyValue = keyCand.getAttribute("data-key");
        var input = document.getElementById("apiKeyInput");
        if (input) input.value = keyValue;
        if (selectedMachine().connected) {
            mDraft(activeAgent).apiKey = keyValue;
            markClear(mDraft(activeAgent), "apiKey", false);
            updatePreview();
        }
        closeApiKeyCandidates();
        return;
    }
    var customOption = event.target.closest(".custom-select-option[data-value]");
    if (customOption) {
        commitCustomSelect(customOption.getAttribute("data-value"));
        return;
    }
    var customTrigger = event.target.closest(".custom-select-trigger");
    if (customTrigger) {
        openCustomSelect(customTrigger);
        return;
    }
    if (event.target.closest(".custom-select")) return;
    var selectRow = event.target.closest("[data-select-machine]");
    if (selectRow) {
        if (saving) return;
        if (selectedMachineId !== selectRow.getAttribute("data-select-machine")) {
            selectedMachineId = selectRow.getAttribute("data-select-machine");
            closeHelp();
        }
        render();
        return;
    }
    var tab = event.target.closest("[data-agent-tab]");
    if (tab) {
        if (saving) return;
        activeAgent = tab.getAttribute("data-agent-tab");
        document.querySelectorAll("[data-agent-tab]").forEach(function (btn) {
            var on = btn === tab;
            btn.classList.toggle("is-active", on);
            btn.setAttribute("aria-selected", on ? "true" : "false");
        });
        closeHelp();
        render();
        return;
    }
    var modeBtn = event.target.closest("[data-mode-field]");
    if (modeBtn) {
        var modeDraft = mDraft(activeAgent), modeField = modeBtn.getAttribute("data-mode-field"), modeValue = modeBtn.getAttribute("data-mode");
        modeDraft.runtime[modeField] = modeValue;
        markClear(modeDraft, "runtime." + modeField, modeValue === "unset");
        modeBtn.closest(".tri-state").querySelectorAll("[data-mode]").forEach(function (button) {
            var value = button.getAttribute("data-mode"), selected = value === modeValue;
            button.classList.toggle("is-on", selected && value === "enabled");
            button.classList.toggle("is-off", selected && value === "disabled");
            button.setAttribute("aria-pressed", String(selected));
        });
        updatePreview();
        return;
    }
    var action = event.target.closest("[data-action]");
    if (!action) {
        closeCustomSelects();
        /* 聚焦后的点击、候选区域内的点击都保留浮层。 */
        if (!event.target.closest("#apiKeyMount .sensitive-shell, .apikey-candidates")) closeApiKeyCandidates();
        return;
    }
    var name = action.getAttribute("data-action");
    if (saving || modalPending) return;
    if (name === "apply-limit") {
        var limitKey = action.getAttribute("data-limit-key"), limitValue = action.getAttribute("data-limit-value");
        var limitInput = document.querySelector('[data-limit="' + limitKey + '"]');
        if (!limitInput) return;
        limitInput.value = limitValue;
        changeLimit(limitKey, limitValue);
        closeLimitCandidates();
        limitInput.focus();
    }
    else if (name === "confirm-dialog") { var resolve = modalResolve; modalResolve = null; closeModal(); if (resolve) resolve(true); }
    else if (name === "raw-save") saveRaw();
    else if (name === "raw-continue") continueRawEditing();
    else if (name === "raw-discard") closeModal();
    else if (name === "close-help") closeHelp();
    else if (name === "close-modal") requestCloseModal();
    else if (name === "modal-backdrop" && event.target === action) requestCloseModal();
    else if (name === "change-path") openPathModal(action.getAttribute("data-path-target"));
    else if (name === "apply-path") {
        applyPath(action.getAttribute("data-path-target"), document.getElementById("customPathInput").value.trim());
    } else if (name === "reset-path") {
        applyPath(action.getAttribute("data-path-target"), "");
    } else if (name === "add-machine") openMachineModal(null);
    else if (name === "edit-machine") openMachineModal(action.getAttribute("data-machine-id"));
    else if (name === "save-machine") applyMachineModal(action.getAttribute("data-connect") === "1");
    else if (name === "toggle-mm-password" || name === "toggle-mm-proxy-url") {
        var input = document.getElementById(name === "toggle-mm-password" ? "mmPassword" : "mmProxyUrl");
        input.type = input.type === "password" ? "text" : "password";
        action.innerHTML = input.type === "password" ? ICON_EYE : ICON_EYE_OFF;
        action.setAttribute("aria-label", input.type === "password" ? "显示密码" : "隐藏密码");
    }
    else if (name === "toggle-apikey") {
        var keyInput = document.getElementById("apiKeyInput");
        if (keyInput) {
            var masked = keyInput.classList.toggle("is-masked");
            action.innerHTML = masked ? ICON_EYE : ICON_EYE_OFF;
            action.setAttribute("aria-label", masked ? "显示 API Key" : "隐藏 API Key");
            action.setAttribute("aria-pressed", String(!masked));
        }
    }
    else if (name === "connect-machine") connectMachine(action.getAttribute("data-machine-id"));
    else if (name === "disconnect-machine") disconnectMachine(action.getAttribute("data-machine-id"));
    else if (name === "remove-machine") confirmRemoveMachine(action.getAttribute("data-machine-id"));
    else if (name === "confirm-remove-machine") removeMachine(action.getAttribute("data-machine-id"));
    else if (name === "save") saveActiveAgent();
    else if (name === "refresh") {
        refreshSnapshot();
    } else if (name === "open-config") {
        openFileEditor(action.getAttribute("data-file-target"));
    }
});

document.addEventListener("change", function (event) {
    var el = event.target;
    /* 机器模态框内的联动：切换代理模式后整体重渲染 */
    if (el.id === "mmProxyMode") {
        collectMachineModal();
        renderMachineModal();
        return;
    }
    if (el.id === "backupSelect") { loadBackup(Number(el.value)); return; }
    var machine = selectedMachine();
    if (!machine || !machine.connected || !ensureMachineState(machine).files) return;
    var draft = mDraft(activeAgent);
    if (el.getAttribute("data-field") === "baseUrl") {
        updateBaseUrl(el.value);
        return;
    }
    var field = el.getAttribute("data-field");
    var runtimeField = { claudeEffort: "effortLevel", claudePermission: "permissionMode", codexTier: "serviceTier", codexEffort: "modelReasoningEffort", codexSandbox: "sandboxMode", codexApproval: "approvalPolicy", opencodeEffort: "reasoningEffort" }[field];
    if (runtimeField) {
        draft.runtime[runtimeField] = el.value;
        markClear(draft, "runtime." + runtimeField, !el.value);
        updatePreview();
    }
});

document.addEventListener("input", function (event) {
    var el = event.target;
    if (el.id === "customPathInput") {
        el.removeAttribute("aria-invalid");
        document.getElementById("customPathError").hidden = true;
        return;
    }
    if (machineModal && el.id && el.id.indexOf("mm") === 0) {
        collectMachineModal();
        if (machineModal.validationAttempted) updateMachineValidation(false);
        return;
    }
    if (el.id === "machineSearch") {
        renderMachines();
        return;
    }
    if (!selectedMachine() || !selectedMachine().connected || !ensureMachineState(selectedMachine()).files) return;
    if (el.id === "allModelsInput") {
        if (activeAgent === "claude" && !isOwnServiceUrl(mDraft("claude").baseUrl)) applyClaudeModelToAll(el.value);
        return;
    }
    if (el.getAttribute && el.getAttribute("data-field") === "baseUrl") {
        updateBaseUrl(el.value);
        return;
    }
    if (el.getAttribute && el.getAttribute("data-field") === "model") {
        var draft = mDraft(activeAgent);
        if (isOwnServiceUrl(draft.baseUrl)) return;
        if (activeAgent === "claude") {
            var key = el.getAttribute("data-model-key");
            if (CLAUDE_MODEL_KEYS.indexOf(key) === -1) return;
            draft.models[key] = el.value;
            markClear(draft, "models." + key, !el.value.trim());
            var allModelsInput = document.getElementById("allModelsInput");
            if (allModelsInput) allModelsInput.value = uniformClaudeModel();
        } else draft.model = el.value;
        syncLimitSuggestions();
        updatePreview();
        return;
    }
    if (el.getAttribute && el.getAttribute("data-field") === "apiKey") {
        mDraft(activeAgent).apiKey = el.value;
        markClear(mDraft(activeAgent), "apiKey", !el.value);
        updatePreview();
    }
});

/* 参数输入在失焦时提交，保留未改动字段的精确压缩值。 */
document.addEventListener("focusout", function (event) {
    var el = event.target, key = el.getAttribute && el.getAttribute("data-limit");
    if (!key) return;
    var machine = selectedMachine();
    if (!machine || !machine.connected || !ensureMachineState(machine).files) return;
    changeLimit(key, el.value);
});

document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !event.defaultPrevented) {
        var limitMenu = document.querySelector(".limit-candidates[open]");
        if (limitMenu) {
            closeLimitCandidates();
            limitMenu.parentElement.querySelector("[data-limit]").focus();
            event.preventDefault();
            return;
        }
        closeLimitCandidates();
        if (rawEditor && rawEditor.confirmClosing) continueRawEditing();
        else requestCloseModal();
        closeHelp();
        closeCustomSelects();
        closeApiKeyCandidates();
    }
});
document.addEventListener("focusin", function (event) {
    var el = event.target;
    closeLimitCandidates(el.closest ? el.closest(".limit-candidates") : null);
    if (el && el.id === "apiKeyInput" && typeof el.hasAttribute === "function" && el.hasAttribute("data-apikey-candidates")) {
        openApiKeyCandidates(el);
        return;
    }
    if (apikeyMenuEl && !(el && el.closest && el.closest("#apiKeyMount .sensitive-shell, .apikey-candidates"))) {
        closeApiKeyCandidates();
    }
});
["mouseover", "focusin"].forEach(function (name) {
    document.addEventListener(name, function (event) {
        var target = event.target.closest && event.target.closest(".field-help-button, .help-popover");
        if (!target || isHelpRelatedTargetWithin(target, event)) return;
        if (target.classList.contains("field-help-button")) showHelp(target);
        else clearHelpHideTimer();
    });
});
["mouseout", "focusout"].forEach(function (name) {
    document.addEventListener(name, function (event) {
        var target = event.target.closest && event.target.closest(".field-help-button, .help-popover");
        if (!target || isHelpRelatedTargetWithin(helpTriggerEl, event) || isHelpRelatedTargetWithin(helpPopoverEl, event)) return;
        scheduleHelpHide();
    });
});
window.addEventListener("scroll", function (event) {
    if (helpPopoverEl && !(event.target.closest && event.target.closest(".help-popover"))) closeHelp();
    if (!comboState) return;
    /* 菜单内部（搜索框 / 选项列表）自身的滚动不关闭菜单 */
    var target = event.target;
    if (target && target.closest && target.closest(".custom-select")) return;
    closeCustomSelects();
}, true);
window.addEventListener("resize", function () { closeHelp(); if (comboState) closeCustomSelects(); });


/* 本页使用后端生成的预览票据，表单与文件编辑器共享写入流程。 */
var previewCache = {};
var previewTimer = null;
var saving = false;
var modalPending = false;
var modalResolve = null;
var modalReturnFocus = null;
var rawEditor = null;

async function api(path, method, data) {
    var options = { method: method || "GET", credentials: "same-origin", cache: "no-store", headers: { "X-Agent-Config": "1" } };
    if (data !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(data); }
    var response = await fetch("/api/agent-config" + path, options);
    var result;
    try { result = await response.json(); }
    catch (error) { throw new Error("服务响应无效，请刷新页面后重试"); }
    if (!response.ok) {
        var failure = new Error(result.error || "操作失败");
        Object.assign(failure, result);
        throw failure;
    }
    return result;
}

function machineUrl(id, suffix) { return "/machines/" + encodeURIComponent(id) + (suffix || ""); }
function previewId(id, agent) { return id + ":" + agent; }
function versionsFor(state) {
    var result = {};
    Object.keys(state.files).forEach(function (target) { result[target] = state.files[target].version; });
    return result;
}
function formKey(agent) {
    var state = ensureMachineState(selectedMachine());
    return JSON.stringify({ draft: state.drafts[agent], versions: versionsFor(state) });
}
function clearPreviews(id, agent) {
    Object.keys(previewCache).forEach(function (key) { if (key === previewId(id, agent) || (!agent && key.indexOf(id + ":") === 0)) delete previewCache[key]; });
}
function showError(error, machineId) {
    toast("error", error.message || "连接失败，请重试");
    if (error.code === "disconnected") {
        var machine = machines.find(function (item) { return item.id === (machineId || selectedMachineId); });
        if (machine && !machine.local) { machine.connected = false; clearPreviews(machine.id); render(); }
    }
}
function installSnapshot(id, snapshot, reset) {
    var machine = machines.find(function (item) { return item.id === id; });
    if (!machine) return;
    Object.assign(machine, snapshot.machine);
    var state = ensureMachineState(machine);
    if (!state.files || reset === "all") {
        state.files = snapshot.files;
        initDraftAll(machine);
        clearPreviews(id);
    } else if (reset) {
        var targets = reset === "codex" ? ["codex", "codexAuth"] : [reset];
        targets.forEach(function (target) { state.files[target] = snapshot.files[target]; });
        initDraft(machine, reset);
        state.baselines[reset] = JSON.stringify(state.drafts[reset]);
        clearPreviews(id, reset);
    }
    state.error = "";
}
function previewRowView(row, agent) {
    var defaultFile = agent === "claude" ? "settings.json" : agent === "codex" ? "config.toml" : "opencode.json";
    return {
        key: JSON.stringify([row.file, row.path]),
        label: row.file === defaultFile ? row.path : row.file + "." + row.path,
        status: row.kind === "same" ? "保持不变" : row.kind === "remove" ? "删除" : "写入",
        statusClass: "preview-status preview-status-" + (row.kind === "same" ? "same" : row.kind === "remove" ? "delete" : "write"),
        className: "preview-row" + (row.kind === "same" ? "" : " changed"),
        current: displayValue(row.current),
        next: displayValue(row.next)
    };
}
function previewRowContentHtml(view) {
    return '<code class="preview-key">' + esc(view.label) + '</code>' +
        '<span class="preview-values"><span class="preview-value" title="当前值">' + esc(view.current) + '</span><span class="preview-arrow" aria-hidden="true">→</span>' +
        '<span class="preview-value preview-next" title="保存后">' + esc(view.next) + '</span></span><span class="' + view.statusClass + '">' + view.status + '</span>';
}
function previewRowsHtml(rows, agent) {
    return rows.map(function (row) {
        var view = previewRowView(row, agent);
        return '<div class="' + view.className + '" data-preview-key="' + esc(view.key) + '">' + previewRowContentHtml(view) + '</div>';
    }).join("");
}
function syncPreviewRows(list, rows, agent) {
    var existing = new Map();
    list.querySelectorAll(".preview-row").forEach(function (node) { existing.set(node.getAttribute("data-preview-key"), node); });
    var cursor = list.firstElementChild;
    rows.forEach(function (row) {
        var view = previewRowView(row, agent), node = existing.get(view.key);
        if (!node) {
            node = document.createElement("div");
            node.setAttribute("data-preview-key", view.key);
            node.innerHTML = previewRowContentHtml(view);
        }
        if (node.className !== view.className) node.className = view.className;
        setPreviewText(node.querySelector(".preview-key"), view.label);
        setPreviewText(node.querySelector(".preview-value"), view.current);
        setPreviewText(node.querySelector(".preview-next"), view.next);
        var status = node.querySelector(".preview-status");
        setPreviewText(status, view.status);
        if (status.className !== view.statusClass) status.className = view.statusClass;
        if (node !== cursor) list.insertBefore(node, cursor);
        cursor = node.nextElementSibling;
        existing.delete(view.key);
    });
    existing.forEach(function (node) { node.remove(); });
}
async function requestFormPreview(id, agent, key) {
    var cacheId = previewId(id, agent), entry = previewCache[cacheId], state = machineState[id];
    if (!entry || entry.key !== key || !entry.pending || entry.started || saving || rawEditor) return;
    entry.started = true;
    try {
        var result = await api(machineUrl(id, "/" + agent + "/preview"), "POST", { versions: versionsFor(state), draft: state.drafts[agent] });
        if (previewCache[cacheId] === entry) previewCache[cacheId] = Object.assign(result, { key: key });
    } catch (error) {
        if (previewCache[cacheId] === entry) previewCache[cacheId] = { key: key, error: error.message };
        if (error.code === "disconnected") showError(error, id);
    }
    if (selectedMachineId === id && activeAgent === agent && !rawEditor) updatePreview();
}

function modalBusy(busy) {
    document.querySelectorAll("#modalRoot button, #modalRoot input, #modalRoot select, #modalRoot textarea").forEach(function (el) { el.disabled = busy; });
    var dialog = document.querySelector("#modalRoot [role=dialog]");
    if (dialog) dialog.setAttribute("aria-busy", String(busy));
    if (rawEditor && rawEditor.view) rawEditor.view.setReadOnly(busy);
    if (!busy && rawEditor) editorStatus(rawEditor.status || "未修改", rawEditor.error);
}
function confirmDialog(title, body, button) {
    closeModal();
    return new Promise(function (resolve) {
        modalResolve = resolve;
        document.getElementById("modalRoot").innerHTML = '<div class="modal-overlay" data-action="modal-backdrop"><div class="modal-dialog" role="dialog" aria-modal="true"><div class="modal-content">' +
            '<div class="modal-header"><h3 class="modal-title">' + esc(title) + '</h3><button class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>' +
            '<div class="modal-body">' + body + '</div><div class="modal-footer"><button class="btn btn-secondary" data-action="close-modal">取消</button>' +
            '<button class="btn btn-primary" data-action="confirm-dialog">' + esc(button || "继续") + '</button></div></div></div></div>';
    });
}
async function refreshSnapshot() {
    if (saving) return;
    var machine = selectedMachine(), id = machine.id, agent = activeAgent, state = ensureMachineState(machine);
    if (!machine.connected) return;
    if (state.drafts && state.baselines[agent] !== JSON.stringify(state.drafts[agent])) {
        if (!await confirmDialog("重新读取配置", '<p class="modal-subtitle">当前页签有未保存修改，重新读取后将恢复文件中的值。</p>', "重新读取")) return;
    }
    try {
        var result = await api(machineUrl(id, "/snapshot"));
        installSnapshot(id, result, state.files ? agent : "all");
        render();
        toast("success", "已重新读取配置");
    } catch (error) { state.error = error.message; showError(error); render(); }
}
async function applyPath(target, path) {
    if (modalPending) return;
    var id = selectedMachineId;
    modalPending = true;
    modalBusy(true);
    try {
        var result = await api(machineUrl(id, "/path"), "PUT", { target: target, path: path });
        installSnapshot(id, result, target);
        modalPending = false;
        closeModal();
        render();
        toast("success", path ? "路径已保存" : "已恢复默认路径");
    } catch (error) {
        var input = document.getElementById("customPathInput"), message = document.getElementById("customPathError");
        if (input && message) { input.setAttribute("aria-invalid", "true"); message.textContent = error.message; message.hidden = false; }
        if (error.code === "disconnected") showError(error, id);
    }
    finally { modalPending = false; modalBusy(false); }
}

async function openFileEditor(target) {
    if (saving || !selectedMachine().connected) return;
    var id = selectedMachineId, agent = activeAgent;
    target = target || agent;
    closeModal();
    modalReturnFocus = document.activeElement;
    try {
        var result = await api(machineUrl(id, "/" + agent + "/files"));
        clearPreviews(id, agent);
        clearTimeout(previewTimer);
        rawEditor = { id: id, agent: agent, sources: {}, versions: {}, baselines: {}, dirty: false, status: "未修改" };
        result.files.filter(function (source) { return source.target === target; }).forEach(function (source) {
            source.text = source.text.replace(/\r\n?/g, "\n");
            rawEditor.sources[source.target] = source;
            rawEditor.versions[source.target] = source.version;
            rawEditor.baselines[source.target] = source.text;
        });
        renderFileEditor();
    } catch (error) { showError(error); }
}
function renderFileEditor() {
    var editor = rawEditor;
    var target = Object.keys(editor.sources)[0], source = editor.sources[target];
    var filename = source.path.split(/[\\/]/).pop();
    var format = target === "codex" ? "TOML" : /\.jsonc$/i.test(source.path) ? "JSONC" : "JSON";
    var closePrompt = '<div class="config-editor-close-prompt" id="rawClosePrompt" hidden><div class="modal-dialog config-editor-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="rawCloseTitle" aria-describedby="rawCloseMessage"><div class="modal-content">' +
        '<div class="modal-header"><h3 class="modal-title" id="rawCloseTitle">保存修改？</h3><button type="button" class="modal-close" data-action="raw-continue" aria-label="继续编辑">×</button></div>' +
        '<div class="modal-body"><p class="modal-subtitle" id="rawCloseMessage">文件有未保存的修改，是否保存后关闭？</p></div>' +
        '<div class="modal-footer"><button type="button" class="btn btn-secondary" id="rawContinueBtn" data-action="raw-continue">继续编辑</button>' +
        '<button type="button" class="btn btn-danger-quiet" data-action="raw-discard">不保存</button><button type="button" class="btn btn-primary" data-action="raw-save">保存并关闭</button></div></div></div></div>';
    document.getElementById("modalRoot").innerHTML = '<div class="modal-overlay" data-action="modal-backdrop"><div class="modal-dialog config-editor-dialog" id="rawEditorDialog" role="dialog" aria-modal="true"><div class="modal-content">' +
        '<div class="modal-header"><div class="config-editor-heading"><h3 class="modal-title">' + esc(filename) + '</h3>' +
        '<div class="config-editor-path" title="' + esc(source.path) + '">' + esc(source.path) + '</div></div>' +
        '<button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>' +
        '<div class="modal-body"><div class="config-editor-workspace"><div class="config-editor-tools">' +
        '<div class="config-editor-version"><label for="backupSelect">版本</label><select id="backupSelect" class="form-control"><option value="0">当前文件</option>' +
        [1, 2, 3, 4, 5].map(function (n) { return '<option value="' + n + '">备份 .bak.' + n + '</option>'; }).join("") + '</select></div>' +
        '<div class="config-editor-meta"><span>' + format + '</span><span id="rawLineCount"></span></div></div>' +
        '<div class="config-editor-code" id="raw-' + target + '" data-raw-target="' + target + '"></div></div></div>' +
        '<div class="modal-footer"><div id="rawEditorStatus" class="editor-status" role="status"></div><div class="config-editor-actions">' +
        '<button type="button" class="btn btn-secondary" data-action="close-modal">关闭</button>' +
        '<button type="button" class="btn btn-primary" data-action="raw-save" id="rawSaveBtn" disabled>保存</button></div></div></div></div>' + closePrompt + '</div>';
    window.ace.config.set("basePath", "/static/vendor/ace");
    var view = editor.view = window.ace.edit(document.getElementById("raw-" + target));
    view.session.setUseWorker(false);
    view.session.setMode(target === "codex" ? "ace/mode/toml" : "ace/mode/json");
    view.session.setNewLineMode("unix");
    view.setOptions({ fontSize: 13, fontFamily: "var(--font-mono)", tabSize: 2, useSoftTabs: true, wrap: false, showPrintMargin: false, highlightActiveLine: true, displayIndentGuides: true });
    view.renderer.setPadding(16);
    view.renderer.setScrollMargin(14, 14, 0, 0);
    view.setValue(source.text, -1);
    view.textInput.getElement().setAttribute("aria-label", filename + " 内容");
    view.session.on("change", function () {
        if (rawEditor !== editor) return;
        source.text = view.getValue();
        updateRawEditorState();
    });
    view.commands.addCommand({ name: "saveAgentFile", bindKey: { win: "Ctrl-S", mac: "Command-S" }, exec: saveRaw });
    syncRawEditorTheme();
    editor.themeObserver = new MutationObserver(syncRawEditorTheme);
    editor.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    updateRawEditorState();
    view.resize(true);
    editorStatus(editor.status);
}
function syncRawEditorTheme() {
    if (rawEditor && rawEditor.view) rawEditor.view.setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "ace/theme/textmate" : "ace/theme/tomorrow_night");
}
function updateRawEditorState() {
    if (!rawEditor) return;
    rawEditor.dirty = Object.keys(rawEditor.sources).some(function (target) { return rawEditor.sources[target].text !== rawEditor.baselines[target]; });
    document.getElementById("rawLineCount").textContent = rawEditor.view.session.getLength() + " 行";
    editorStatus(rawEditor.dirty ? "有未保存的修改" : "未修改");
}
function editorStatus(message, error) {
    if (!rawEditor) return;
    rawEditor.status = message;
    rawEditor.error = Boolean(error);
    var mount = document.getElementById("rawEditorStatus"), button = document.getElementById("rawSaveBtn");
    if (mount) { mount.textContent = message; mount.classList.toggle("is-error", Boolean(error)); }
    if (button) button.disabled = !rawEditor.dirty || modalPending;
}
async function loadBackup(backup) {
    if (!rawEditor || modalPending) return;
    var editor = rawEditor;
    modalPending = true;
    modalBusy(true);
    try {
        var result = await api(machineUrl(editor.id, "/" + editor.agent + "/files?backup=" + backup));
        var sources = result.files.filter(function (source) { return source.target in editor.sources; });
        if (backup && !sources.some(function (source) { return source.exists; })) throw new Error("该备份不存在");
        sources.forEach(function (source) {
            if (backup && !source.exists) return;
            source.text = source.text.replace(/\r\n?/g, "\n");
            editor.sources[source.target].text = source.text;
            if (!backup) {
                editor.versions[source.target] = source.version;
                editor.baselines[source.target] = source.text;
            }
            editor.view.setValue(source.text, -1);
        });
        updateRawEditorState();
        editorStatus(backup && editor.dirty ? "已载入备份，保存后生效" : "已载入当前文件");
    } catch (error) { editorStatus(error.message, true); }
    finally { modalPending = false; modalBusy(false); }
}
async function saveRaw() {
    if (!rawEditor || !rawEditor.dirty || modalPending) return;
    continueRawEditing();
    var editor = rawEditor, raw = {};
    Object.keys(editor.sources).forEach(function (target) { raw[target] = editor.sources[target].text; });
    modalPending = true;
    modalBusy(true);
    editorStatus("正在检查文件…");
    try {
        var preview = await api(machineUrl(editor.id, "/" + editor.agent + "/preview"), "POST", { versions: editor.versions, raw: raw });
        if (preview.hasChanges) {
            editorStatus("正在保存…");
            var result = await api(machineUrl(editor.id, "/apply"), "POST", { token: preview.token });
            installSnapshot(editor.id, result, editor.agent);
        }
        modalPending = false;
        closeModal();
        render();
        toast("success", preview.hasChanges ? "配置文件已保存" : "文件内容未变化");
    } catch (error) { editorStatus(error.message, true); }
    finally { modalPending = false; modalBusy(false); }
}

function labelControls() {
    document.querySelectorAll(".agents-section .form-field, .agents-section .runtime-field, .limit-edit-row, #modalRoot .form-field").forEach(function (field, index) {
        var control = field.querySelector("input, select, .custom-select-trigger"), label = field.querySelector(".form-label");
        if (!control || !label) return;
        if (!control.id) control.id = "agent-control-" + index;
        label.htmlFor = control.id;
    });
    document.querySelectorAll("[data-agent-tab]").forEach(function (button) { button.setAttribute("tabindex", button.getAttribute("aria-selected") === "true" ? "0" : "-1"); });
}
new MutationObserver(function () {
    var dialog = document.querySelector("#modalRoot [role=dialog]");
    if (!dialog) return;
    if (!modalReturnFocus) modalReturnFocus = document.activeElement;
    document.body.style.overflow = "hidden";
    var title = dialog.querySelector(".modal-title");
    if (title) { title.id = "agentModalTitle"; dialog.setAttribute("aria-labelledby", title.id); }
    labelControls();
    if (!dialog.contains(document.activeElement)) {
        var input = dialog.querySelector("input, textarea, select, button");
        if (input) input.focus();
    }
}).observe(document.getElementById("modalRoot"), { childList: true });
document.addEventListener("keydown", function (event) {
    var dialog = activeModalDialog();
    if (event.key === "Tab" && dialog) {
        var controls = Array.from(dialog.querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)"));
        var first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    var tab = event.target.closest("[data-agent-tab]");
    if (tab && (event.key === "ArrowRight" || event.key === "ArrowLeft")) {
        event.preventDefault();
        var tabs = Array.from(document.querySelectorAll("[data-agent-tab]"));
        var next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        next.click(); next.focus();
    }
});
async function startAgents() {
    window.initThemeToggle();
    render();
    try {
        var data = await api("/bootstrap");
        machines = data.machines;
        providers = data.providers;
        catalogModels = data.models;
        MODEL_LIMIT_PRESETS = data.modelLimitPresets;
        LOCAL_INTERFACES = data.interfaces;
        SERVER_BIND_HOST = data.serverHost;
        SERVER_PORT = String(data.serverPort);
        SERVER_ORIGIN = window.location.origin;
        API_KEY_MANAGEMENT_ENABLED = data.apiKeyEnabled;
        API_KEYS = data.apiKeys;
        render();
        try { installSnapshot("local", await api(machineUrl("local", "/snapshot")), "all"); }
        catch (error) { ensureMachineState(selectedMachine()).error = error.message; showError(error); }
        render();
    } catch (error) {
        panel.innerHTML = '<div class="locked-state"><p>' + esc(error.message) + '</p><a href="/login" class="btn btn-secondary">登录</a></div>';
        toast("error", error.message);
    }
}
startAgents();
