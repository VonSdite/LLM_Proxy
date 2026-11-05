const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync("src/presentation/static/js/agents.js", "utf8").replace(/startAgents\(\);\s*$/, "");
const clone = value => JSON.parse(JSON.stringify(value));
const modelPresets = JSON.parse(readFileSync("src/config/agent_model_limits.json", "utf8"));

function element() {
    const classes = new Set();
    return {
        innerHTML: "", textContent: "", value: "", disabled: false, isConnected: true, style: {}, attributes: {},
        classList: {
            toggle(name, force = !classes.has(name)) { if (force) classes.add(name); else classes.delete(name); return force; },
            add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); },
        },
        querySelector() { return element(); }, querySelectorAll() { return []; }, closest() { return null; },
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        getAttribute(name) { return this.attributes[name] || null; },
        hasAttribute(name) { return name in this.attributes; },
        addEventListener() {}, appendChild() {}, insertBefore() {}, focus() {}, remove() {},
    };
}

function editorView() {
    let value = "", change;
    const view = {
        session: {
            setUseWorker(enabled) { this.useWorker = enabled; }, setMode(mode) { this.mode = mode; }, setNewLineMode() {},
            on(name, listener) { if (name === "change") change = listener; },
            getLength() { return value.split("\n").length; },
        },
        renderer: { setPadding() {}, setScrollMargin() {} },
        textInput: { getElement: element }, commands: { addCommand() {} },
        setValue(next) { value = next; if (change) change(); }, getValue() { return value; },
        setOptions(options) { this.options = options; }, setTheme(theme) { this.theme = theme; },
        setReadOnly(readOnly) { this.readOnly = readOnly; },
        resize() {}, focus() {}, destroy() { this.destroyed = true; },
    };
    return view;
}

function snapshot(overrides = {}) {
    const files = {};
    for (const target of ["claude", "codex", "codexAuth", "opencode"]) files[target] = {
        path: "/srv/home/" + target, defaultPath: "/srv/home/" + target, customPath: "", exists: true,
        managed: {}, parseError: "", version: "version-" + target,
    };
    Object.assign(files.claude.managed, { "env.ANTHROPIC_MODEL": "main/claude-sonnet-4-6", "env.IS_SANDBOX": "1", "sandbox.enabled": false });
    Object.assign(files.codex.managed, {
        model: "main/org/model.v1", model_provider: "main", model_context_window: 372000, model_auto_compact_token_limit: 350000,
        "model_providers.main.base_url": "https://proxy.example/v1",
    });
    return { machine: { id: "local", name: "本机", local: true, connected: true, platform: "Linux", proxy: { mode: "direct", url: "" } }, files, ...overrides };
}

function harness() {
    const elements = new Map();
    const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
    const events = {};
    const context = {
        URL, console, JSON, Promise, setTimeout() { return 1; }, clearTimeout() {},
        MutationObserver: class { observe() {} disconnect() {} },
        document: {
            documentElement: element(),
            getElementById: get,
            querySelector(selector) { return selector === ".agents-section" ? get("section") : null; },
            querySelectorAll() { return []; },
            createElement: element,
            addEventListener(name, listener) { (events[name] ||= []).push(listener); },
            body: { style: {} }, activeElement: element(),
        },
        window: { addEventListener() {}, ace: { config: { set() {} }, edit: editorView } },
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    context.SERVER_ORIGIN = "https://proxy.example";
    context.SERVER_PORT = "9090";
    context.SERVER_BIND_HOST = "0.0.0.0";
    context.catalogModels = ["main/org/model.v1", "main/claude-sonnet-4-6", "smart-model"];
    context.machines = [snapshot().machine];
    context.installSnapshot("local", snapshot(), "all");
    const notifications = [];
    context.toast = (kind, message) => notifications.push({ kind, message });
    return { context, get, events, notifications };
}

function apiKeyHarness(agent) {
    const state = harness();
    const { context, get, events } = state;
    context.activeAgent = agent;
    context.API_KEY_MANAGEMENT_ENABLED = true;
    context.API_KEYS = [{ name: "Development", key: "sk-fixture-development" }];
    context.mDraft(agent).baseUrl = "https://proxy.example/v1";
    const within = (target, ancestors = {}) => {
        target.closest = selectors => selectors.split(",").map(selector => ancestors[selector.trim()]).find(Boolean) || null;
        return target;
    };
    const shell = element();
    const input = within(get("apiKeyInput"), { "#apiKeyMount .sensitive-shell": shell });
    input.id = "apiKeyInput";
    input.type = "text";
    input.classList.add("is-masked");
    input.setAttribute("data-field", "apiKey");
    input.setAttribute("data-apikey-candidates", "1");
    input.getBoundingClientRect = () => ({ left: 100, bottom: 200, width: 300 });
    const dispatch = (name, target = input, extra = {}) => events[name].forEach(listener => listener({ target, ...extra }));
    return { ...state, input, shell, within, dispatch };
}

test("API Key candidates stay open through focus then click and reopen while the input remains focused", () => {
    for (const agent of ["claude", "codex", "opencode"]) {
        const { context, input, shell, within, dispatch } = apiKeyHarness(agent);
        dispatch("focusin");
        const menu = context.apikeyMenuEl;
        assert.ok(menu);
        dispatch("click");
        dispatch("click");
        assert.equal(context.apikeyMenuEl, menu);
        within(menu, { ".apikey-candidates": menu });
        dispatch("click", menu);
        assert.equal(context.apikeyMenuEl, menu);
        const toggle = element();
        toggle.setAttribute("data-action", "toggle-apikey");
        within(toggle, { "#apiKeyMount .sensitive-shell": shell, "[data-action]": toggle });
        dispatch("focusin", toggle);
        dispatch("click", toggle);
        assert.equal(input.type, "text");
        assert.equal(input.classList.contains("is-masked"), false);
        assert.equal(toggle.getAttribute("aria-pressed"), "true");
        assert.equal(context.apikeyMenuEl, menu);
        dispatch("click", toggle);
        assert.equal(input.type, "text");
        assert.equal(input.classList.contains("is-masked"), true);
        assert.equal(toggle.getAttribute("aria-pressed"), "false");
        assert.equal(context.apikeyMenuEl, menu);
        dispatch("keydown", input, { key: "Escape" });
        assert.equal(context.apikeyMenuEl, null);
        dispatch("click");
        assert.ok(context.apikeyMenuEl);
        dispatch("click", within(element()));
        assert.equal(context.apikeyMenuEl, null);
        dispatch("click");
        assert.ok(context.apikeyMenuEl);
        dispatch("focusin", within(element()));
        assert.equal(context.apikeyMenuEl, null);
    }
});

test("API Key manual entry and candidate selection update every Agent draft without replacing the input", () => {
    for (const agent of ["claude", "codex", "opencode"]) {
        const { context, input, get, within, dispatch } = apiKeyHarness(agent);
        dispatch("focusin");
        dispatch("click");
        const menu = context.apikeyMenuEl;
        input.value = "sk-manual-fixture";
        dispatch("input");
        assert.equal(context.mDraft(agent).apiKey, input.value);
        assert.equal(context.apikeyMenuEl, menu);
        input.value = "";
        dispatch("input");
        assert.ok(context.mDraft(agent).clearFields.includes("apiKey"));
        const candidate = element();
        candidate.setAttribute("data-key", "sk-fixture-development");
        const child = within(element(), { ".apikey-candidate": candidate, ".apikey-candidates": menu });
        dispatch("focusin", child);
        assert.equal(context.apikeyMenuEl, menu);
        dispatch("click", child);
        assert.equal(context.mDraft(agent).apiKey, "sk-fixture-development");
        assert.ok(!(context.mDraft(agent).clearFields || []).includes("apiKey"));
        assert.equal(get("apiKeyInput"), input);
        assert.equal(input.value, "sk-fixture-development");
        assert.equal(context.apikeyMenuEl, null);
    }
});

test("loads actual platform, tri-state environment values and current compact tokens", () => {
    const { context } = harness();
    assert.equal(context.connText(context.selectedMachine()), "Linux · 服务运行用户");
    assert.equal(context.mDraft("claude").runtime.sandboxEnabled, "disabled");
    assert.equal(context.mDraft("claude").runtime.sandbox, "enabled");
    assert.equal(context.mDraft("codex").limits.compactTokens, 350000);
    assert.equal(context.defaultBaseUrl(context.selectedMachine(), "codex"), "http://127.0.0.1:9090/v1");
    assert.equal(context.mDraft("codex").baseUrl, "https://proxy.example/v1");
});

test("Codex memories reflects file booleans and keeps missing configurations unset", () => {
    for (const [exists, value, expected] of [
        [false, undefined, "unset"], [true, undefined, "unset"],
        [true, true, "enabled"], [true, false, "disabled"], [true, "true", "unset"],
    ]) {
        const { context } = harness();
        const data = snapshot();
        data.files.codex.exists = exists;
        if (!exists) data.files.codex.managed = {};
        if (value !== undefined) data.files.codex.managed["features.memories"] = value;
        context.activeAgent = "codex";
        context.installSnapshot("local", data, "all");
        const draft = context.mDraft("codex");
        assert.equal(draft.runtime.memories, expected);
        const html = context.renderCodex(draft);
        assert.match(html, new RegExp('data-mode-field="memories" data-mode="' + expected + '" aria-pressed="true"'));
        assert.match(html, /data-help-topic="codex_memories"/);
        assert.equal((html.split('<div class="runtime-grid">')[1].match(/class="runtime-field"/g) || []).length, 4);
        assert.doesNotMatch(html, /data-mode-field="(?:goals|multiAgent)"/);
    }
});

test("missing Claude configurations use the requested runtime defaults on local and SSH machines", () => {
    for (const machine of [
        { id: "local", local: true, platform: "Windows" },
        { id: "local", local: true, platform: "Linux" },
        { id: "ssh-fixture", local: false, platform: "Linux" },
    ]) {
        const { context } = harness();
        const data = snapshot();
        Object.assign(data.machine, machine);
        data.files.claude.exists = false;
        data.files.claude.managed = {};
        context.machines = [data.machine];
        context.selectedMachineId = machine.id;
        context.installSnapshot(machine.id, data, "all");
        const runtime = context.mDraft("claude").runtime;
        assert.deepEqual(clone(runtime), {
            effortLevel: "", permissionMode: "", sandboxEnabled: "unset",
            skipWebFetchPreflight: "enabled", attributionHeader: "disabled",
            disableNonessentialTraffic: "enabled", experimentalAgentTeams: "enabled",
            enableToolSearch: "enabled", sandbox: "enabled",
        });
        const html = context.renderClaude(context.mDraft("claude"));
        assert.match(html, /data-mode-field="skipWebFetchPreflight" data-mode="enabled" aria-pressed="true"/);
        assert.match(html, /data-mode-field="sandboxEnabled" data-mode="unset" aria-pressed="true"/);
        assert.match(html, /data-mode-field="disableNonessentialTraffic" data-mode="enabled" aria-pressed="true">禁用/);
        assert.match(html, /data-help-topic="claude_webfetch_preflight"/);
        runtime.attributionHeader = "enabled";
        context.render();
        assert.equal(context.mDraft("claude").runtime.attributionHeader, "enabled");
    }
});

test("existing Claude files retain their runtime values without receiving creation defaults", () => {
    const { context } = harness();
    const data = snapshot();
    data.files.claude.managed = {};
    context.installSnapshot("local", data, "all");
    const runtime = context.mDraft("claude").runtime;
    for (const key of ["sandboxEnabled", "skipWebFetchPreflight", ...context.CLAUDE_MODE_SETTINGS.map(item => item.key)]) {
        assert.equal(runtime[key], "unset", key);
    }
    for (const [value, expected] of [[true, "enabled"], [false, "disabled"], ["true", "unset"]]) {
        data.files.claude.managed = {
            skipWebFetchPreflight: value,
            "env.CLAUDE_CODE_ATTRIBUTION_HEADER": "1",
            "env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0",
            "env.ENABLE_TOOL_SEARCH": "false",
            "env.IS_SANDBOX": "0",
            "sandbox.enabled": true,
        };
        context.installSnapshot("local", data, "all");
        const runtime = context.mDraft("claude").runtime;
        assert.equal(runtime.skipWebFetchPreflight, expected);
        assert.equal(runtime.attributionHeader, "enabled");
        assert.equal(runtime.experimentalAgentTeams, "disabled");
        assert.equal(runtime.enableToolSearch, "disabled");
        assert.equal(runtime.sandbox, "disabled");
        assert.equal(runtime.sandboxEnabled, "enabled");
    }
});

test("selectable catalog keeps routed IDs, mappings, 1M variants and existing custom IDs", () => {
    const { context } = harness();
    const options = context.comboOptionsFor("claudeModel", "ANTHROPIC_MODEL").options;
    assert.ok(options.some(item => item.value === "smart-model"));
    assert.ok(options.some(item => item.value === "main/org/model.v1[1m]"));
    context.mDraft("codex").model = "retired/custom.model";
    context.activeAgent = "codex";
    assert.ok(context.comboOptionsFor("codexModel").options.some(item => item.value === "retired/custom.model"));
});

test("unified Claude selection applies the full routed ID to all supported fields and keeps other drafts intact", () => {
    const { context } = harness();
    const draft = context.mDraft("claude");
    const codex = clone(context.mDraft("codex"));
    const opencode = clone(context.mDraft("opencode"));
    const runtime = clone(draft.runtime);
    draft.apiKey = "fixture-key";
    draft.clearFields = ["models.ANTHROPIC_DEFAULT_OPUS_MODEL", "runtime.effortLevel"];
    const options = context.comboOptionsFor("claudeAllModels");
    assert.equal(options.value, "");
    assert.ok(options.options.some(item => item.value === "main/org/model.v1[1m]"));
    assert.ok(options.options.every(item => item.value !== ""));
    context.comboState = { kind: "claudeAllModels" };
    context.commitCustomSelect("main/org/model.v1[1m]");
    for (const key of context.CLAUDE_MODEL_KEYS) assert.equal(draft.models[key], "main/org/model.v1[1m]");
    assert.equal(context.uniformClaudeModel(), "main/org/model.v1[1m]");
    assert.deepEqual(clone(draft.clearFields), ["runtime.effortLevel"]);
    assert.deepEqual(clone(draft.runtime), runtime);
    assert.equal(draft.apiKey, "fixture-key");
    assert.deepEqual(clone(context.mDraft("codex")), codex);
    assert.deepEqual(clone(context.mDraft("opencode")), opencode);
    context.comboState = { kind: "claudeModel", key: "ANTHROPIC_DEFAULT_OPUS_MODEL" };
    context.commitCustomSelect("smart-model");
    assert.equal(context.uniformClaudeModel(), "");
    assert.equal(draft.models.ANTHROPIC_MODEL, "main/org/model.v1[1m]");
});

test("Claude permissions round-trip through selection and preview without changing other drafts", async () => {
    const { context, events } = harness();
    const data = snapshot();
    data.files.claude.managed["permissions.defaultMode"] = "manual";
    data.files.claude.managed["env.ANTHROPIC_REASONING_MODEL"] = "legacy-model";
    context.installSnapshot("local", data, "all");
    assert.equal(context.mDraft("claude").runtime.permissionMode, "default");
    assert.equal(context.mDraft("claude").models.ANTHROPIC_REASONING_MODEL, undefined);
    const models = clone(context.mDraft("claude").models);
    const codex = clone(context.mDraft("codex")), opencode = clone(context.mDraft("opencode"));
    const input = element();
    input.setAttribute("data-field", "claudePermission");
    let payload;
    context.api = async (url, method, data) => { payload = clone(data); return {token:"fixture", rows:[], hasChanges:true}; };
    for (const mode of ["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions", ""]) {
        input.value = mode;
        events.change[0]({target:input});
        await context.requestFormPreview("local", "claude", context.formKey("claude"));
        assert.equal(payload.draft.runtime.permissionMode, mode);
        assert.equal((payload.draft.clearFields || []).includes("runtime.permissionMode"), !mode);
        assert.deepEqual(clone(context.mDraft("claude").models), models);
        assert.deepEqual(clone(context.mDraft("codex")), codex);
        assert.deepEqual(clone(context.mDraft("opencode")), opencode);
    }
});

test("Claude detects truthy traffic flags, real sandbox state and effort aliases", () => {
    const { context } = harness();
    for (const value of ["1", "0", "false", "custom"]) {
        const data = snapshot();
        delete data.files.claude.managed["sandbox.enabled"];
        data.files.claude.managed["env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = value;
        data.files.claude.managed["env.CLAUDE_CODE_EFFORT_LEVEL"] = "unset";
        context.installSnapshot("local", data, "all");
        assert.equal(context.mDraft("claude").runtime.disableNonessentialTraffic, "enabled");
        assert.equal(context.mDraft("claude").runtime.sandboxEnabled, "unset");
        assert.equal(context.mDraft("claude").runtime.sandbox, "enabled");
        assert.equal(context.mDraft("claude").runtime.effortLevel, "auto");
    }
});

test("external batch entry syncs immediately and mixed individual models clear only the batch input", () => {
    const { context, events, get } = harness();
    context.updateBaseUrl("https://external.example/v1");
    const draft = context.mDraft("claude");
    draft.limits = { context: 128000, compact: 75, output: 12345 };
    const before = clone(draft);
    const input = get("allModelsInput");
    input.id = "allModelsInput";
    context.render = () => assert.fail("Typing must preserve the mounted inputs");
    input.value = " external/custom.model[1m] ";
    events.input[0]({ target: input });
    for (const key of context.CLAUDE_MODEL_KEYS) assert.equal(draft.models[key], "external/custom.model[1m]");
    assert.deepEqual(clone(draft.limits), before.limits);
    assert.deepEqual(clone(draft.runtime), before.runtime);

    const individual = element();
    individual.setAttribute("data-field", "model");
    individual.setAttribute("data-model-key", "ANTHROPIC_DEFAULT_OPUS_MODEL");
    individual.value = "external/opus-only";
    events.input[0]({ target: individual });
    assert.equal(input.value, "");
    assert.equal(draft.models.ANTHROPIC_DEFAULT_OPUS_MODEL, "external/opus-only");
    for (const key of context.CLAUDE_MODEL_KEYS.filter(key => key !== "ANTHROPIC_DEFAULT_OPUS_MODEL")) {
        assert.equal(draft.models[key], "external/custom.model[1m]");
    }
    individual.value = "external/custom.model[1m]";
    events.input[0]({ target: individual });
    assert.equal(input.value, "external/custom.model[1m]");

    input.value = "";
    events.input[0]({ target: input });
    for (const key of context.CLAUDE_MODEL_KEYS) {
        assert.equal(draft.models[key], "");
        assert.ok(draft.clearFields.includes("models." + key));
    }
    input.value = "external/new-model";
    events.input[0]({ target: input });
    for (const key of context.CLAUDE_MODEL_KEYS) assert.equal(draft.models[key], input.value);
    assert.equal(draft.clearFields, undefined);
    assert.deepEqual(clone(draft.limits), before.limits);
    assert.deepEqual(clone(draft.runtime), before.runtime);
    const applied = clone(draft);
    context.comboState = { kind: "claudeAllModels" };
    context.commitCustomSelect("main/org/model.v1");
    assert.deepEqual(clone(draft), applied);
});

test("service URL detection checks complete endpoints and supports LAN, reverse proxy and IPv6 addresses", () => {
    const { context } = harness();
    context.LOCAL_INTERFACES = [{ ip: "192.0.2.10" }];
    context.machines.push({ id: "remote", serviceHost: "2001:db8::10" });
    for (const url of ["https://PROXY.EXAMPLE:443/v1/", "http://127.0.0.1:9090", "http://192.0.2.10:9090/v1", "http://[2001:db8::10]:9090/v1"]) {
        assert.equal(context.isOwnServiceUrl(url), true, url);
    }
    for (const url of ["https://external.example/v1", "https://proxy.example.evil/v1", "https://proxy.example:444/v1", "https://proxy.example/other", "https://user:pass@proxy.example/v1", "https://proxy.example/v1?model=x", "not-a-url", ""]) {
        assert.equal(context.isOwnServiceUrl(url), false, url);
    }
});

test("external API addresses use manual models and switching back preserves all three Agent drafts", () => {
    for (const agent of ["claude", "codex", "opencode"]) {
        const { context, events, get } = harness();
        context.activeAgent = agent;
        context.API_KEY_MANAGEMENT_ENABLED = true;
        const original = clone(context.mDraft(agent));
        const address = element();
        address.setAttribute("data-field", "baseUrl");
        address.value = "https://external.example/v1";
        events.input[0]({ target: address });
        assert.equal(context.mDraft(agent).baseUrl, address.value);
        assert.match(context.apiAddressField(address.value), /<input[^>]+data-field="baseUrl"/);
        assert.match(get("modelFieldsMount").innerHTML, /model-id-input/);
        assert.doesNotMatch(get("modelFieldsMount").innerHTML, /data-combo=/);
        assert.doesNotMatch(get("apiKeyMount").innerHTML, /data-apikey-candidates/);
        assert.equal(context.comboOptionsFor(agent + "Model", "ANTHROPIC_MODEL").options.length, 0);
        const manual = element();
        manual.setAttribute("data-field", "model");
        if (agent === "claude") manual.setAttribute("data-model-key", "ANTHROPIC_MODEL");
        manual.value = "external/custom.model";
        events.input[0]({ target: manual });
        assert.equal(agent === "claude" ? context.mDraft(agent).models.ANTHROPIC_MODEL : context.mDraft(agent).model, manual.value);
        assert.deepEqual(clone(context.mDraft(agent).limits), original.limits);
        assert.deepEqual(clone(context.mDraft(agent).runtime), original.runtime);
        address.value = "https://proxy.example" + (agent === "claude" ? "" : "/v1");
        events.change[0]({ target: address });
        assert.match(get("modelFieldsMount").innerHTML, /data-combo=/);
        assert.doesNotMatch(get("modelFieldsMount").innerHTML, /model-id-input/);
        assert.match(get("apiKeyMount").innerHTML, /data-apikey-candidates/);
        assert.equal(agent === "claude" ? context.mDraft(agent).models.ANTHROPIC_MODEL : context.mDraft(agent).model, manual.value);
    }
});

test("manual Claude model clearing is explicit and stale catalog choices cannot edit an external model", () => {
    const { context, events } = harness();
    context.updateBaseUrl("https://external.example");
    const manual = element();
    manual.setAttribute("data-field", "model");
    manual.setAttribute("data-model-key", "ANTHROPIC_MODEL");
    manual.value = "";
    events.input[0]({ target: manual });
    assert.ok(context.mDraft("claude").clearFields.includes("models.ANTHROPIC_MODEL"));
    context.comboState = { kind: "claudeModel", key: "ANTHROPIC_MODEL" };
    context.commitCustomSelect("main/claude-sonnet-4-6");
    assert.equal(context.mDraft("claude").models.ANTHROPIC_MODEL, "");
    assert.equal(context.comboState, null);
});

test("external configurations with no selected model do not default to a local catalog model", () => {
    const { context } = harness();
    const data = snapshot();
    delete data.files.codex.managed.model;
    data.files.codex.managed["model_providers.main.base_url"] = "https://external.example/v1";
    data.files.opencode.managed["provider.main.options.baseURL"] = "https://external.example/v1";
    context.installSnapshot("local", data, "all");
    assert.equal(context.mDraft("codex").model, "");
    assert.equal(context.mDraft("opencode").model, "");
});

test("opencode round-trips provider plus full routed model and actual file limits", () => {
    const { context } = harness();
    const data = snapshot();
    data.files.opencode.managed = {
        model: "main/main/org/model.v1",
        "provider.main.options.baseURL": "https://custom.example/v1",
        "provider.main.models.main/org/model.v1": { name: "main/org/model.v1", limit: { context: 100003, output: 1000 }, options: { reasoningEffort: "high" } },
        "compaction.reserved": 12345,
    };
    context.installSnapshot("local", data, "opencode");
    const draft = context.mDraft("opencode");
    assert.equal(draft.model, "main/org/model.v1");
    assert.equal(draft.baseUrl, "https://custom.example/v1");
    assert.equal(draft.limits.context, 100003);
    assert.equal(draft.limits.reserved, 12345);
    assert.equal(draft.runtime.reasoningEffort, "high");
});

test("unexpected field types keep the configuration editor accessible", () => {
    const { context } = harness();
    const data = snapshot();
    data.files.claude.managed["env.ANTHROPIC_MODEL"] = 12;
    data.files.opencode.managed.model = { unexpected: true };
    context.installSnapshot("local", data, "all");
    context.render();
    assert.equal(typeof context.mDraft("claude").models.ANTHROPIC_MODEL, "string");
    assert.equal(typeof context.mDraft("opencode").model, "string");
    assert.equal(context.mDraft("claude").models.ANTHROPIC_MODEL, "");
});

test("server preview owns rows and save state, with escaped file values", async () => {
    const { context } = harness();
    const calls = [];
    context.api = async (url, method, payload) => {
        calls.push({ url, method, payload: clone(payload) });
        return { token: "server-plan", hasChanges: true, rows: [{ path: "env.ANTHROPIC_MODEL", file: "settings.json", kind: "change", current: "old", next: "<script>" }] };
    };
    context.updatePreview();
    assert.match(context.previewHtml("claude"), /disabled/);
    const key = context.formKey("claude");
    await context.requestFormPreview("local", "claude", key);
    const html = context.previewHtml("claude");
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /id="saveBtn" disabled/);
    assert.equal(calls[0].payload.versions.claude, "version-claude");
    assert.equal(calls[0].payload.draft.models.ANTHROPIC_MODEL, "main/claude-sonnet-4-6");
});

test("preview keeps mounted content while updating or failing and enables save only for the latest result", async () => {
    const { context, get } = harness();
    const mount = get("previewMount"), controls = new Map();
    mount.querySelector = selector => {
        if (!controls.has(selector)) controls.set(selector, element());
        return controls.get(selector);
    };
    Object.defineProperty(mount, "innerHTML", { set() { assert.fail("Preview must stay mounted"); } });
    const list = mount.querySelector(".preview-list"), button = mount.querySelector("#saveBtn");
    let shownRows = [], updates = 0, calls = 0;
    context.syncPreviewRows = (target, rows) => { assert.equal(target, list); shownRows = clone(rows); updates++; };
    const rows = [{ file: "settings.json", path: "env.ANTHROPIC_MODEL", kind: "change", current: "old", next: "new" }];
    context.api = async () => { calls++; return { token: "first", hasChanges: true, rows }; };
    context.updatePreview();
    assert.equal(button.disabled, true);
    assert.equal(updates, 0);
    await context.requestFormPreview("local", "claude", context.formKey("claude"));
    assert.equal(button.disabled, false);
    assert.deepEqual(shownRows, rows);
    const readyUpdates = updates;
    await context.requestFormPreview("local", "claude", context.formKey("claude"));
    assert.equal(calls, 1);

    context.mDraft("claude").apiKey = "edited";
    context.updatePreview();
    assert.equal(button.disabled, true);
    assert.equal(list.getAttribute("aria-busy"), "true");
    assert.equal(updates, readyUpdates);
    assert.deepEqual(shownRows, rows);
    context.api = async () => { throw new Error("preview unavailable"); };
    await context.requestFormPreview("local", "claude", context.formKey("claude"));
    assert.equal(button.disabled, true);
    assert.equal(list.getAttribute("aria-busy"), "false");
    assert.equal(updates, readyUpdates);
    assert.match(mount.querySelector(".preview-feedback").textContent, /preview unavailable/);
    assert.equal(mount.querySelector(".preview-feedback").classList.contains("is-error"), true);

    context.mDraft("claude").apiKey = "retry";
    context.updatePreview();
    context.api = async () => ({ token: "latest", hasChanges: false, rows: [] });
    await context.requestFormPreview("local", "claude", context.formKey("claude"));
    assert.equal(button.disabled, true);
    assert.deepEqual(shownRows, []);
    assert.equal(mount.querySelector(".preview-feedback").textContent, "");
});

test("model selections and runtime changes update controls without rebuilding the form", () => {
    const { context, events } = harness();
    context.render = () => assert.fail("Editing a field must preserve the mounted form");
    for (const agent of ["claude", "codex", "opencode"]) {
        context.activeAgent = agent;
        context.comboState = { kind: agent + "Model", key: "ANTHROPIC_MODEL" };
        context.commitCustomSelect("main/org/model.v1");
        const draft = context.mDraft(agent);
        assert.equal(agent === "claude" ? draft.models.ANTHROPIC_MODEL : draft.model, "main/org/model.v1");
        const control = element();
        control.setAttribute("data-field", { claude: "claudeEffort", codex: "codexEffort", opencode: "opencodeEffort" }[agent]);
        control.value = "high";
        events.change[0]({ target: control });
        assert.equal(draft.runtime[{ claude: "effortLevel", codex: "modelReasoningEffort", opencode: "reasoningEffort" }[agent]], "high");
    }
    context.activeAgent = "claude";
    context.comboState = { kind: "claudeAllModels" };
    context.commitCustomSelect("main/org/model.v1[1m]");
    assert.equal(context.uniformClaudeModel(), "main/org/model.v1[1m]");
    for (const [agent, field] of [["claude", "sandboxEnabled"], ["claude", "skipWebFetchPreflight"], ["codex", "memories"]]) {
        context.activeAgent = agent;
        const buttons = ["enabled", "disabled", "unset"].map(mode => {
            const button = element();
            button.setAttribute("data-mode-field", field);
            button.setAttribute("data-mode", mode);
            return button;
        });
        const group = { querySelectorAll: () => buttons };
        for (const selected of [...buttons, buttons[0]]) {
            selected.closest = selector => selector === "[data-mode-field]" ? selected : selector === ".tri-state" ? group : null;
            events.click[0]({ target: selected });
            const mode = selected.getAttribute("data-mode"), draft = context.mDraft(agent);
            assert.equal(draft.runtime[field], mode);
            assert.equal((draft.clearFields || []).includes("runtime." + field), mode === "unset");
            for (const button of buttons) {
                assert.equal(button.getAttribute("aria-pressed"), String(button === selected));
                assert.equal(button.classList.contains("is-on"), button === selected && button.getAttribute("data-mode") === "enabled");
                assert.equal(button.classList.contains("is-off"), button === selected && button.getAttribute("data-mode") === "disabled");
            }
        }
    }
});

test("late preview response cannot replace the current draft preview", async () => {
    const { context } = harness();
    let finishOld;
    context.api = () => new Promise(resolve => { finishOld = resolve; });
    context.updatePreview();
    const old = context.requestFormPreview("local", "claude", context.formKey("claude"));
    context.mDraft("claude").apiKey = "edited";
    context.updatePreview();
    const newKey = context.formKey("claude");
    context.api = async () => ({ token: "new-plan", rows: [], hasChanges: true });
    await context.requestFormPreview("local", "claude", newKey);
    finishOld({ token: "old-plan", rows: [], hasChanges: true });
    await old;
    assert.equal(context.previewCache["local:claude"].token, "new-plan");
});

test("save applies only the server ticket and retains the other Agent drafts", async () => {
    const { context, get } = harness();
    const calls = [];
    context.mDraft("codex").model = "pending/codex-edit";
    context.api = async (url, method, payload) => {
        calls.push({ url, method, payload: clone(payload) });
        return url.endsWith("/preview") ? { token: "approved-plan", hasChanges: true } : snapshot();
    };
    await context.saveActiveAgent();
    assert.deepEqual(calls[1].payload, { token: "approved-plan" });
    assert.equal(context.mDraft("codex").model, "pending/codex-edit");
    assert.equal(get("section").inert, false);
    assert.equal(context.saving, false);
});

test("save conflict keeps the unsaved draft", async () => {
    const { context, notifications } = harness();
    context.mDraft("claude").apiKey = "pending-key";
    context.api = async () => { throw Object.assign(new Error("file changed"), { code: "file_conflict" }); };
    await context.saveActiveAgent();
    assert.equal(context.mDraft("claude").apiKey, "pending-key");
    assert.equal(notifications.at(-1).kind, "error");
});

test("choosing unset marks a visible field for explicit removal", () => {
    const { context, events } = harness();
    context.comboState = { kind: "claudeModel", key: "ANTHROPIC_DEFAULT_OPUS_MODEL" };
    context.commitCustomSelect("");
    assert.ok(context.mDraft("claude").clearFields.includes("models.ANTHROPIC_DEFAULT_OPUS_MODEL"));
    const input = element();
    input.setAttribute("data-field", "claudeEffort");
    input.value = "";
    events.change[0]({ target: input });
    assert.ok(context.mDraft("claude").clearFields.includes("runtime.effortLevel"));
    input.value = "high";
    events.change[0]({ target: input });
    assert.ok(!context.mDraft("claude").clearFields.includes("runtime.effortLevel"));
});

test("file editor stages backups until a single save validates and applies the current file", async () => {
    const { context } = harness();
    const calls = [];
    context.api = async (url, method, payload) => {
        calls.push({ url, method, payload: payload && clone(payload) });
        if (url.includes("/files")) return { files: [{ target: "claude", path: "/srv/home/settings.json", text: url.includes("backup") ? '{"backup":true}\n' : '{"current":true}\n', version: "disk-v1", exists: true }] };
        if (url.endsWith("/preview")) return { token: "raw-plan", hasChanges: true, rows: [{ file: "settings.json", kind: "change" }] };
        return snapshot();
    };
    await context.openFileEditor();
    assert.equal(context.rawEditor.sources.claude.text, '{"current":true}\n');
    await context.loadBackup(1);
    assert.equal(context.rawEditor.sources.claude.text, '{"backup":true}\n');
    assert.ok(calls.every(call => !call.url.endsWith("/apply")));
    await context.saveRaw();
    assert.deepEqual(calls.at(-2).payload, { versions: { claude: "disk-v1" }, raw: { claude: '{"backup":true}\n' } });
    assert.deepEqual(calls.at(-1).payload, { token: "raw-plan" });
    assert.equal(context.rawEditor, null);
});

test("Codex file links preview only the selected file and ignore the sibling backup", async () => {
    for (const target of ["codex", "codexAuth"]) {
        const { context, get } = harness();
        context.activeAgent = "codex";
        const paths = { codex: "/srv/home/config.toml", codexAuth: "/srv/home/auth.json" };
        const current = { codex: 'model = "current"\n', codexAuth: '{"OPENAI_API_KEY":"current"}\n' };
        const backup = { codex: 'model = "backup"\n', codexAuth: '{"OPENAI_API_KEY":"backup"}\n' };
        let request;
        const requests = [];
        context.api = async (url, method, payload) => {
            request = { url, method, payload: payload && clone(payload) };
            requests.push(request);
            if (url.includes("/files")) return { files: Object.keys(paths).map(key => ({
                target: key, path: paths[key], version: "disk-" + key,
                text: url.includes("backup=") ? backup[key] : current[key],
                exists: !url.includes("backup=2") || key !== target,
            })) };
            return url.endsWith("/apply") ? snapshot() : { token: "single-file", hasChanges: true, rows: [{ file: paths[target], kind: "change" }] };
        };
        await context.openFileEditor(target);
        assert.deepEqual(Object.keys(context.rawEditor.sources), [target]);
        assert.deepEqual(clone(context.rawEditor.versions), { [target]: "disk-" + target });
        const other = target === "codex" ? "codexAuth" : "codex";
        assert.match(get("modalRoot").innerHTML, new RegExp('data-raw-target="' + target + '"'));
        assert.doesNotMatch(get("modalRoot").innerHTML, new RegExp('data-raw-target="' + other + '"'));
        await context.loadBackup(2);
        assert.equal(context.rawEditor.error, true);
        assert.equal(context.rawEditor.sources[target].text, current[target]);
        await context.loadBackup(1);
        assert.equal(context.rawEditor.error, false);
        assert.equal(context.rawEditor.sources[target].text, backup[target]);
        await context.saveRaw();
        assert.deepEqual(requests.at(-2).payload, { versions: { [target]: "disk-" + target }, raw: { [target]: backup[target] } });
        assert.deepEqual(request.payload, { token: "single-file" });
    }
});

test("file editor tracks real changes and asks before closing, with continue and save choices", async () => {
    const { context, get } = harness();
    const original = '{"original":true}\n', edited = '{"edited":true}\n', calls = [];
    context.api = async (url, method, payload) => {
        calls.push({ url, payload: payload && clone(payload) });
        if (url.includes("/files")) return { files: [{ target: "claude", path: "/fixture/settings.json", text: original, version: "v1", exists: true }] };
        if (url.endsWith("/preview")) return { token: "validated", hasChanges: true };
        return snapshot();
    };
    await context.openFileEditor();
    const editor = context.rawEditor, view = editor.view;
    assert.equal(view.session.mode, "ace/mode/json");
    assert.equal(view.session.useWorker, false);
    assert.equal(editor.dirty, false);
    assert.equal(get("rawSaveBtn").disabled, true);
    assert.doesNotMatch(get("modalRoot").innerHTML, /raw-preview|预览修改|请预览/);
    view.setValue(edited);
    assert.equal(editor.dirty, true);
    assert.equal(get("rawSaveBtn").disabled, false);
    view.setValue(original);
    assert.equal(editor.dirty, false);
    view.setValue(edited);
    context.requestCloseModal();
    assert.equal(context.rawEditor, editor);
    assert.equal(editor.confirmClosing, true);
    assert.equal(get("rawEditorDialog").inert, true);
    assert.equal(get("rawClosePrompt").hidden, false);
    assert.equal(calls.length, 1);
    context.continueRawEditing();
    assert.equal(get("rawClosePrompt").hidden, true);
    assert.equal(get("rawEditorDialog").inert, false);
    assert.equal(view.getValue(), edited);
    context.requestCloseModal();
    await context.saveRaw();
    assert.deepEqual(calls.at(-2).payload, { versions: { claude: "v1" }, raw: { claude: edited } });
    assert.deepEqual(calls.at(-1).payload, { token: "validated" });
    assert.equal(context.rawEditor, null);
    assert.equal(view.destroyed, true);
});

test("discarding file edits or closing an unchanged file performs no write", async () => {
    for (const dirty of [false, true]) {
        const { context, events } = harness();
        let calls = 0;
        context.api = async () => {
            calls++;
            return { files: [{ target: "claude", path: "/fixture/settings.json", text: "{}\n", version: "v1", exists: true }] };
        };
        await context.openFileEditor();
        const view = context.rawEditor.view;
        if (dirty) view.setValue('{"edited":true}');
        context.requestCloseModal();
        if (dirty) {
            const button = element();
            button.setAttribute("data-action", "raw-discard");
            button.closest = selector => selector === "[data-action]" ? button : null;
            events.click[0]({ target: button });
        }
        assert.equal(context.rawEditor, null);
        assert.equal(view.destroyed, true);
        assert.equal(calls, 1);
    }
});

test("file save errors retain edits and retry obtains a fresh ticket", async () => {
    for (const failingStage of ["preview", "apply"]) {
        const { context, get } = harness();
        const calls = [];
        let failing = true;
        context.api = async (url, method, payload) => {
            calls.push({ url, payload: payload && clone(payload) });
            if (url.includes("/files")) return { files: [{ target: "claude", path: "/fixture/settings.json", text: "{}\n", version: "v1", exists: true }] };
            if (failing && url.endsWith("/" + failingStage)) throw new Error("fixture validation or version conflict");
            if (url.endsWith("/preview")) return { token: failing ? "first-ticket" : "fresh-ticket", hasChanges: true };
            return snapshot();
        };
        await context.openFileEditor();
        const editor = context.rawEditor;
        editor.view.setValue('{"first":true}');
        context.requestCloseModal();
        await context.saveRaw();
        assert.equal(context.rawEditor, editor);
        assert.equal(editor.dirty, true);
        assert.equal(editor.view.getValue(), '{"first":true}');
        assert.equal(editor.view.readOnly, false);
        assert.equal(context.modalPending, false);
        assert.equal(get("rawSaveBtn").disabled, false);
        assert.match(get("rawEditorStatus").textContent, /fixture validation/);
        if (failingStage === "preview") assert.ok(calls.every(call => !call.url.endsWith("/apply")));
        failing = false;
        editor.view.setValue('{"retry":true}');
        await context.saveRaw();
        assert.deepEqual(calls.at(-2).payload.raw, { claude: '{"retry":true}' });
        assert.deepEqual(calls.at(-1).payload, { token: "fresh-ticket" });
        assert.equal(context.rawEditor, null);
    }
});

test("file save locks the editor through validation and apply and ignores duplicate saves and closing", async () => {
    const { context } = harness();
    let finishPreview, finishApply, previews = 0, applies = 0;
    context.api = async url => {
        if (url.includes("/files")) return { files: [{ target: "claude", path: "/fixture/settings.json", text: "{}\n", version: "v1", exists: true }] };
        if (url.endsWith("/preview")) { previews++; return new Promise(resolve => { finishPreview = resolve; }); }
        applies++; return new Promise(resolve => { finishApply = resolve; });
    };
    await context.openFileEditor();
    const editor = context.rawEditor;
    editor.view.setValue('{"edited":true}');
    const pending = context.saveRaw();
    await context.saveRaw();
    context.requestCloseModal();
    assert.equal(context.rawEditor, editor);
    assert.equal(editor.view.readOnly, true);
    assert.equal(previews, 1);
    finishPreview({ token: "ticket", hasChanges: true });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(applies, 1);
    assert.equal(editor.view.readOnly, true);
    context.requestCloseModal();
    assert.equal(context.rawEditor, editor);
    finishApply(snapshot());
    await pending;
    assert.equal(context.rawEditor, null);
});

test("API uses same-origin credentials and file operation request header", async () => {
    const { context } = harness();
    let request;
    context.fetch = async (url, options) => { request = { url, options }; return { ok: true, json: async () => ({ ok: true }) }; };
    await context.api("/machines/local/apply", "POST", { token: "ticket" });
    assert.equal(request.options.credentials, "same-origin");
    assert.equal(request.options.cache, "no-store");
    assert.equal(request.options.headers["X-Agent-Config"], "1");
    assert.equal(request.options.headers["Content-Type"], "application/json");
});

test("SSH machine save sends the selected proxy and clears password from UI state", async () => {
    const { context, get } = harness();
    context.openMachineModal(null);
    const values = { mmName: "Remote", mmHost: "server.test", mmPort: "22", mmUser: "agent", mmPassword: "ssh-secret", mmProxyMode: "custom", mmProxyUrl: "https://u:p%40ss@proxy.test:443" };
    for (const [key, value] of Object.entries(values)) get(key).value = value;
    let submitted;
    context.api = async (url, method, payload) => {
        submitted = clone(payload);
        return { machine: { ...payload, id: "remote", local: false, connected: false, password: "" } };
    };
    await context.applyMachineModal(false);
    assert.equal(submitted.password, "ssh-secret");
    assert.equal(submitted.proxy.url, "https://u:p%40ss@proxy.test:443");
    assert.equal(context.machineModal, null);
    assert.equal(context.selectedMachine().password, "");
});

test("missing SSH fields are marked inside the dialog without requests or repeated toasts", async () => {
    const { context, get, events, notifications } = harness();
    context.openMachineModal(null);
    for (const [id, value] of Object.entries({ mmName: "Remote", mmHost: "", mmPort: "22", mmUser: "", mmProxyMode: "direct" })) {
        get(id).id = id;
        get(id).value = value;
    }
    let focused = "";
    get("mmHost").focus = () => { focused = "mmHost"; };
    get("mmUser").focus = () => { focused = "mmUser"; };
    context.api = () => assert.fail("Invalid fields must not be submitted");
    for (const connect of [false, true, true]) await context.applyMachineModal(connect);
    assert.equal(focused, "mmHost");
    assert.equal(get("mmHost").getAttribute("aria-invalid"), "true");
    assert.equal(get("mmUser").getAttribute("aria-invalid"), "true");
    assert.equal(get("mmPort").getAttribute("aria-invalid"), "false");
    assert.equal(get("mmHostError").hidden, false);
    assert.equal(get("mmUserError").hidden, false);
    assert.equal(get("mmPassword").getAttribute("aria-invalid"), null);
    assert.deepEqual(notifications, []);
    get("mmHost").value = "server.test";
    events.input[0]({ target: get("mmHost") });
    assert.equal(get("mmHost").getAttribute("aria-invalid"), "false");
    assert.equal(get("mmHostError").hidden, true);
    assert.equal(get("mmUser").getAttribute("aria-invalid"), "true");
    await context.applyMachineModal(false);
    assert.equal(focused, "mmUser");
    get("mmUser").value = "agent";
    events.input[0]({ target: get("mmUser") });
    assert.equal(get("mmUser").getAttribute("aria-invalid"), "false");
    assert.equal(get("mmUserError").hidden, true);
    assert.equal(context.modalPending, false);
    assert.deepEqual(notifications, []);
});

test("SSH port and proxy errors stay with their fields and follow proxy mode changes", async () => {
    const { context, get, events, notifications } = harness();
    context.openMachineModal(null);
    for (const [id, value] of Object.entries({ mmHost: "[::1]", mmPort: "65536", mmUser: "agent", mmProxyMode: "custom", mmProxyUrl: "" })) {
        get(id).id = id;
        get(id).value = value;
    }
    context.api = () => assert.fail("Invalid fields must not be submitted");
    await context.applyMachineModal(true);
    assert.equal(get("mmHost").getAttribute("aria-invalid"), "false");
    assert.equal(get("mmPort").getAttribute("aria-invalid"), "true");
    assert.equal(get("mmProxyUrl").getAttribute("aria-invalid"), "true");
    for (const port of ["0", "-22", "22.5", "1e2"]) {
        get("mmPort").value = port;
        events.input[0]({ target: get("mmPort") });
        assert.equal(get("mmPort").getAttribute("aria-invalid"), "true", port);
    }
    get("mmPort").value = "2222";
    events.input[0]({ target: get("mmPort") });
    assert.equal(get("mmPortError").hidden, true);
    get("mmProxyUrl").value = "socks4://proxy.test:1080";
    events.input[0]({ target: get("mmProxyUrl") });
    assert.equal(get("mmProxyUrl").getAttribute("aria-invalid"), "true");
    get("mmProxyMode").value = "direct";
    events.change[0]({ target: get("mmProxyMode") });
    assert.equal(get("mmProxyUrlError").hidden, true);
    get("mmProxyMode").value = "custom";
    events.change[0]({ target: get("mmProxyMode") });
    assert.equal(get("mmProxyUrlError").hidden, false);
    get("mmProxyUrl").value = "socks5h://proxy.test:1080";
    events.input[0]({ target: get("mmProxyUrl") });
    assert.equal(get("mmProxyUrlError").hidden, true);
    assert.equal(context.updateMachineValidation(false), true);
    assert.deepEqual(notifications, []);
});


test("model-specific suggestions retain every candidate and never fill missing configuration values", () => {
    const { context } = harness();
    context.MODEL_LIMIT_PRESETS = modelPresets;
    assert.deepEqual(clone(context.modelParameterCandidates("main/gpt-5.4").context.map(item => item.value)), [372000, 400000, 1050000]);
    assert.deepEqual(clone(context.modelParameterCandidates("gpt-6-astra").context.map(item => item.value)), [272000, 872000]);
    assert.equal(context.modelParameterCandidates("main/claude-sonnet-4-6[1m]").output[0].value, 64000);
    assert.equal(context.modelParameterCandidates("unknown-model").matched, false);
    const data = snapshot();
    context.installSnapshot("local", data, "all");
    assert.deepEqual(clone(context.mDraft("claude").limits), { context: null, compact: null, output: null });
    assert.match(context.modelParameterHelp("main/gpt-5.4"), /372,000.*400,000.*1,050,000/);
    assert.match(context.modelParameterHelp("<unknown>"), /&lt;unknown&gt;/);
    data.files.codex.managed = { model: "main/gpt-5.4", model_auto_compact_token_limit: 800000 };
    context.installSnapshot("local", data, "all");
    assert.equal(context.mDraft("codex").limits.context, null);
    assert.equal(context.mDraft("codex").limits.compactTokens, 800000);
});

test("Claude suggestions label reference models directly in the menus, including mixed aliases without a main model", () => {
    const { context } = harness();
    context.MODEL_LIMIT_PRESETS = modelPresets;
    const draft = context.mDraft("claude");
    draft.models = { ANTHROPIC_MODEL: "main/gpt-5.4", ANTHROPIC_DEFAULT_HAIKU_MODEL: "main/claude-sonnet-4-6", ANTHROPIC_DEFAULT_OPUS_MODEL: "main/gpt-6-astra" };
    assert.deepEqual(clone(context.limitReferenceModels(draft)), ["main/gpt-5.4"]);
    assert.doesNotMatch(context.limitFieldsHtml(draft), /limitReferenceMount|limitReferenceModel|Codex/);
    for (const key of ["context", "compact", "output"]) {
        assert.ok(context.limitCandidates(key, draft).every(item => item.referenceModel === "main/gpt-5.4"));
    }
    draft.models.ANTHROPIC_MODEL = "";
    const before = JSON.stringify(draft);
    assert.deepEqual(clone(context.limitReferenceModels(draft)), ["main/claude-sonnet-4-6", "main/gpt-6-astra"]);
    for (const key of ["context", "compact", "output"]) {
        const html = context.limitCandidateOptions(key, context.limitCandidates(key, draft));
        assert.match(html, /main\/claude-sonnet-4-6 · /);
        assert.match(html, /main\/gpt-6-astra · /);
    }
    assert.equal(JSON.stringify(draft), before);
    draft.models.ANTHROPIC_DEFAULT_OPUS_MODEL = "main/claude-sonnet-4-6";
    assert.deepEqual(clone(context.limitReferenceModels(draft)), ["main/claude-sonnet-4-6"]);
    assert.equal(context.limitCandidates("context", draft).length, context.modelParameterCandidates("main/claude-sonnet-4-6").context.length);
    draft.models = { ANTHROPIC_MODEL: "<unknown>" };
    assert.match(context.limitCandidateOptions("context", context.limitCandidates("context", draft)), /&lt;unknown&gt; · 通用参考值/);
    draft.models = {};
    assert.deepEqual(clone(context.limitCandidates("context", draft)), []);
    assert.match(context.limitFieldsHtml(draft), /先选择模型/);
});

test("compression totals stay in the threshold label and refresh only after changed inputs lose focus", () => {
    for (const fixture of [
        { agent: "codex", limits: { context: 272000, compact: 85, compactTokens: 231199 }, exact: "compactTokens", label: "上限", initial: "231,199", changed: "217,600", resized: "160,000" },
        { agent: "opencode", limits: { context: 1048576, compact: 60, reserved: 419430 }, exact: "reserved", label: "预留", initial: "419,430", changed: "209,716", resized: "40,000" },
    ]) {
        const { context, events, get } = harness();
        context.activeAgent = fixture.agent;
        const draft = context.mDraft(fixture.agent);
        draft.limits = { ...fixture.limits };
        const labels = [...context.limitFieldsHtml(draft).matchAll(/<label\b[^>]*>([\s\S]*?)<\/label>/g)];
        const thresholdLabel = labels.find(match => match[1].includes('data-help-topic="limit_compact"'))[1];
        assert.match(thresholdLabel, /id="limitDerivedMount"/);
        assert.ok(thresholdLabel.includes(fixture.label) && thresholdLabel.includes(fixture.initial));
        let derived = context.limitDerivedHtml(draft), writes = 0, previews = 0;
        Object.defineProperty(get("limitDerivedMount"), "innerHTML", {
            get: () => derived,
            set(value) { derived = value; writes += 1; },
        });
        context.updatePreview = () => { previews += 1; };
        context.render = () => assert.fail("Committing a parameter must preserve the form");
        const input = element();
        input.setAttribute("data-limit", "compact");
        input.value = String(fixture.limits.compact);
        const dispatch = name => events[name].forEach(listener => listener({ target: input }));
        dispatch("focusout");
        input.value = "8";
        dispatch("input");
        input.value = String(fixture.limits.compact);
        dispatch("focusout");
        assert.equal(draft.limits[fixture.exact], fixture.limits[fixture.exact]);
        assert.equal(writes, 0);
        assert.equal(previews, 0);
        input.value = "80";
        dispatch("input");
        assert.ok(derived.includes(fixture.initial));
        assert.equal(draft.limits.compact, fixture.limits.compact);
        dispatch("focusout");
        assert.equal(draft.limits.compact, "80");
        assert.equal(draft.limits[fixture.exact], undefined);
        assert.ok(derived.includes(fixture.changed));
        assert.equal(writes, 1);
        assert.equal(previews, 1);
        input.value = "080";
        dispatch("focusout");
        assert.equal(writes, 1);
        assert.equal(previews, 1);
        input.setAttribute("data-limit", "context");
        input.value = "200000";
        dispatch("input");
        assert.ok(derived.includes(fixture.changed));
        dispatch("focusout");
        assert.ok(derived.includes(fixture.resized));
        assert.equal(writes, 2);
        assert.equal(previews, 2);
        input.setAttribute("data-limit", "output");
        input.value = "64000";
        dispatch("focusout");
        assert.equal(writes, 2);
        assert.equal(previews, 3);
        input.setAttribute("data-limit", "context");
        input.value = "";
        dispatch("focusout");
        assert.equal(derived, "");
        assert.ok(draft.clearFields.includes("limits.context"));
    }
});

test("changing dropdown or typed models updates suggestions while preserving edited and explicitly cleared limits", () => {
    for (const agent of ["claude", "codex", "opencode"]) {
        const { context, events, get } = harness();
        context.MODEL_LIMIT_PRESETS = modelPresets;
        context.activeAgent = agent;
        const draft = context.mDraft(agent);
        const menus = ["context", "compact", "output"].map(key => {
            const menu = element();
            menu.setAttribute("data-limit-options", key);
            return menu;
        });
        const candidate = element();
        candidate.setAttribute("data-action", "apply-limit");
        candidate.setAttribute("data-limit-key", "context");
        candidate.setAttribute("data-limit-value", "872000");
        candidate.closest = selector => ["[data-action]", ".custom-select-option"].includes(selector) ? candidate : null;
        context.document.querySelectorAll = selector => selector === "[data-limit-options]" ? menus
            : selector === '.limit-candidate[data-limit-key="context"]' ? [candidate] : [];
        draft.baseUrl = "https://proxy.example/v1";
        draft.limits = { context: 123456, compact: "", output: 45000, compactTokens: 87654, reserved: 12000 };
        context.markClear(draft, "limits.compact", true);
        const before = JSON.stringify(draft.limits);
        context.comboState = { kind: agent + "Model", key: "ANTHROPIC_MODEL" };
        context.commitCustomSelect("main/gpt-5.4");
        assert.equal(JSON.stringify(draft.limits), before);
        assert.equal(context.limitCandidates("context", draft).length, 3);
        menus.forEach(menu => assert.match(menu.innerHTML, /main\/gpt-5\.4 · /));
        draft.baseUrl = "https://external.example/v1";
        const input = get("typedModel");
        input.setAttribute("data-field", "model");
        input.setAttribute("data-model-key", "ANTHROPIC_MODEL");
        input.value = "gpt-6-astra";
        events.input.forEach(listener => listener({ target: input }));
        assert.equal(JSON.stringify(draft.limits), before);
        assert.equal(context.limitCandidates("context", draft)[0].value, 272000);
        menus.forEach(menu => {
            assert.match(menu.innerHTML, /gpt-6-astra · /);
            assert.doesNotMatch(menu.innerHTML, /gpt-5\.4|Codex/);
        });
        assert.ok(draft.clearFields.includes("limits.compact"));
        const limitInput = element();
        const querySelector = context.document.querySelector;
        context.document.querySelector = selector => selector === '[data-limit="context"]' ? limitInput : querySelector(selector);
        context.render = () => assert.fail("Choosing a limit must preserve the form");
        events.click.forEach(listener => listener({ target: candidate }));
        assert.equal(limitInput.value, "872000");
        assert.ok(candidate.classList.contains("selected"));
        assert.equal(candidate.getAttribute("aria-pressed"), "true");
        assert.equal(draft.limits.context, "872000");
        assert.equal(draft.limits.output, 45000);
        assert.equal(draft.limits.compactTokens, undefined);
        assert.equal(draft.limits.reserved, undefined);
        assert.ok(draft.clearFields.includes("limits.compact"));
    }
});

test("path dialogs save the selected agent and preserve other drafts; failed saves stay in the dialog", async () => {
    for (const agent of ["claude", "codex", "opencode"]) {
        const { context, get, notifications } = harness();
        const other = agent === "claude" ? "codex" : "claude";
        context.activeAgent = agent;
        context.mDraft(other).apiKey = "unsaved-fixture-key";
        const before = JSON.stringify(context.mDraft(other));
        const result = snapshot();
        result.files[agent].customPath = "~/custom/" + agent;
        const requests = [];
        context.api = async (url, method, payload) => { requests.push({ url, method, payload: clone(payload) }); return result; };
        context.openPathModal(agent);
        assert.match(get("modalRoot").innerHTML, agent === "codex" ? /Codex 配置目录/ : /配置文件/);
        await context.applyPath(agent, result.files[agent].customPath);
        assert.deepEqual(requests[0].payload, { target: agent, path: result.files[agent].customPath });
        assert.equal(context.mFiles(agent).customPath, result.files[agent].customPath);
        assert.equal(JSON.stringify(context.mDraft(other)), before);
        context.openPathModal(agent);
        const stateBefore = JSON.stringify(context.ensureMachineState(context.selectedMachine()).files);
        context.api = async () => { throw new Error("Fixture unreadable path"); };
        await context.applyPath(agent, "~/bad/path");
        assert.equal(get("customPathInput").getAttribute("aria-invalid"), "true");
        assert.equal(get("customPathError").textContent, "Fixture unreadable path");
        assert.equal(JSON.stringify(context.ensureMachineState(context.selectedMachine()).files), stateBefore);
        assert.equal(notifications.filter(item => item.kind === "error").length, 0);
    }
});
