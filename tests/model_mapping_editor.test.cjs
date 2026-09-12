const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const template = readFileSync("src/presentation/templates/model_mappings.html", "utf8");
const script = [
    template.slice(template.indexOf("function syncTargetRowActions"), template.indexOf("function openCreateMapping")),
    template.slice(template.indexOf("function formatTargetTestMetric"), template.indexOf("async function exportMappings")),
].join("\n");

function createHarness(modelId = "alpha/fast", available = true) {
    const elements = new Map();
    const classes = new Set();
    const row = {
        dataset: { modelId, available: String(available), enabled: "true", autoDisabled: "false", cooldown: "false" },
        isConnected: true,
        classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } },
        querySelector(selector) {
            if (!elements.has(selector)) {
                elements.set(selector, {
                    value: "", textContent: "", innerHTML: "", hidden: false, disabled: false,
                    dataset: {}, listeners: {},
                    classList: { toggle() {} },
                    setAttribute() {},
                    addEventListener(name, listener) { this.listeners[name] = listener; },
                    querySelectorAll() { return []; },
                });
            }
            return elements.get(selector);
        },
        remove() { this.isConnected = false; },
    };
    row.querySelector(".target-model-id").value = modelId;
    row.querySelector(".target-model-search").value = modelId;
    const requests = [];
    const providers = [{ name: "alpha", model_list: ["fast", "replacement"], api: "https://upstream.test/v1/responses", source_format: "openai_responses", hook: "example.py", verify_ssl: false }];
    const context = {
        AbortController,
        availableTargets: ["alpha/fast", "alpha/replacement"],
        document: { querySelectorAll(selector) { return selector === ".mapping-target-row" ? [row] : [row.querySelector(".target-model-id")]; } },
        escapeHtml: value => String(value),
        escapeAttribute: value => String(value),
        async requestJson(url, options = {}) {
            requests.push({ url, options });
            if (url === "/api/providers") return providers;
            if (url.startsWith("/api/auth-groups/")) return { entries: [{ id: "disabled", enabled: false }, { id: "active" }, { id: "alternate" }] };
            return { results: [{ available: true, first_token_latency_ms: 125, tps: 22.5 }] };
        },
    };
    vm.createContext(context);
    vm.runInContext(script, context);
    return { context, row, elements, classes, requests, providers };
}

test("filled target rows delete immediately and abort their pending test", () => {
    const { context, row } = createHarness();
    const controller = new AbortController();
    row.targetTestController = controller;
    context.removeTargetRow(row);
    assert.equal(row.isConnected, false);
    assert.equal(controller.signal.aborted, true);
});

test("unavailable model IDs can be replaced through the combobox", () => {
    const { context, row, classes } = createHarness("alpha/missing", false);
    context.setupTargetCombobox(row);
    context.syncTargetRowActions(row);
    assert.equal(row.querySelector(".target-model-search").disabled, false);
    assert.equal(row.querySelector(".target-priority").disabled, true);
    assert.equal(row.querySelector(".mapping-toggle-target").hidden, true);
    const input = row.querySelector(".target-model-search");
    input.value = "alpha/replacement";
    input.listeners.input();
    assert.equal(row.querySelector(".target-model-id").value, "alpha/replacement");
    assert.equal(row.dataset.available, "false");
    assert.equal(classes.has("is-unavailable"), true);
    assert.equal(row.querySelector(".mapping-target-unavailable-status").hidden, false);

    input.listeners.blur();

    assert.equal(row.dataset.available, "true");
    assert.equal(classes.has("is-unavailable"), false);
    assert.equal(row.querySelector(".mapping-target-unavailable-status").hidden, true);
    assert.equal(row.querySelector(".target-priority").disabled, false);
    assert.equal(row.querySelector(".mapping-toggle-target").hidden, false);
    assert.equal(row.querySelector(".mapping-test-target").disabled, false);
});

test("renaming a manually disabled row to an unavailable model hides the disabled badge", () => {
    const { context, row } = createHarness("alpha/missing", false);
    row.dataset.enabled = "false";
    context.setupTargetCombobox(row);
    context.syncTargetRowActions(row);
    assert.equal(row.querySelector(".mapping-target-unavailable-status").hidden, false);
    assert.equal(row.querySelector(".mapping-target-auto-disabled-status").hidden, true);
});

test("a duplicate target model ID shows duplicate status instead of unavailable", () => {
    const { context, row, classes } = createHarness("alpha/replacement");
    const ownValueInput = row.querySelector(".target-model-id");
    context.document.querySelectorAll = selector => {
        if (selector === ".mapping-target-row .target-model-id") {
            return [ownValueInput, { value: "alpha/fast" }];
        }
        return [row];
    };

    context.setupTargetCombobox(row);
    context.syncTargetRowActions(row);
    const searchInput = row.querySelector(".target-model-search");
    searchInput.value = "alpha/fast";
    searchInput.listeners.input();

    assert.equal(row.dataset.duplicate, undefined);
    assert.equal(row.querySelector(".mapping-target-duplicate-status").hidden, true);
    assert.equal(row.querySelector(".target-model-id").value, "alpha/fast");

    searchInput.listeners.blur();

    assert.equal(row.dataset.duplicate, "true");
    assert.equal(row.querySelector(".target-model-id").value, "alpha/fast");
    assert.equal(classes.has("is-duplicate"), true);
    assert.equal(row.querySelector(".mapping-target-duplicate-status").hidden, false);
    assert.equal(row.querySelector(".mapping-target-unavailable-status").hidden, true);
    assert.equal(row.querySelector(".target-priority").disabled, true);
    assert.equal(row.querySelector(".mapping-toggle-target").hidden, true);
    assert.equal(row.querySelector(".mapping-test-target").disabled, true);
});

test("an unavailable model status refreshes after the model ID input loses focus", () => {
    const { context, row } = createHarness();
    context.setupTargetCombobox(row);
    context.syncTargetRowActions(row);
    const searchInput = row.querySelector(".target-model-search");
    searchInput.value = "1222";
    searchInput.listeners.input();

    assert.equal(row.dataset.available, "true");
    assert.equal(row.querySelector(".mapping-target-unavailable-status").hidden, true);
    assert.equal(row.querySelector(".target-model-id").value, "1222");

    searchInput.listeners.blur();

    assert.equal(row.dataset.available, "false");
    assert.equal(row.querySelector(".mapping-target-unavailable-status").hidden, false);
    assert.equal(row.querySelector(".target-model-id").value, "1222");
});

test("cooldown targets appear disabled and explain automatic recovery", () => {
    const { context, row, classes } = createHarness();
    row.dataset.cooldown = "true";
    row.dataset.cooldownTooltip = "临时禁用：主动或后台刷新确认 Codex OAuth 账号的全部额度窗口均有剩余额度时，会立即自动解除额度禁用；否则在预计恢复时间到达后自动解除并重试。其他临时故障会在冷却结束后自动解除禁用。";

    context.syncTargetRowActions(row);

    assert.equal(classes.has("is-cooldown"), true);
    assert.equal(row.querySelector(".mapping-target-auto-disabled-status").hidden, false);
    assert.equal(row.querySelector(".mapping-target-auto-disabled-help").dataset.tooltip, row.dataset.cooldownTooltip);
    assert.equal(row.querySelector(".mapping-toggle-target").textContent, "启用");

    context.toggleTargetRow(row);

    assert.equal(row.dataset.cooldown, "false");
    assert.equal(classes.has("is-cooldown"), false);
    assert.equal(row.querySelector(".mapping-target-auto-disabled-status").hidden, true);
    assert.equal(row.querySelector(".mapping-toggle-target").textContent, "禁用");
});

test("testing reuses the Provider configuration and metric format without toggling the target", async () => {
    const { context, row, requests } = createHarness();
    row.dataset.autoDisabled = "true";
    row.dataset.enabled = "false";
    await context.testTargetRow(row);
    const payload = JSON.parse(requests.at(-1).options.body);
    assert.equal(requests.at(-1).url, "/api/providers/test-models");
    assert.deepEqual(payload.models, ["fast"]);
    assert.equal(payload.name, "alpha");
    assert.equal(payload.source_format, "openai_responses");
    assert.equal(payload.verify_ssl, false);
    assert.equal(payload.hook, "example.py");
    assert.equal(row.querySelector(".mapping-target-test-status").textContent, "可用");
    assert.equal(row.querySelector(".target-test-latency").textContent, "125.00 ms");
    assert.equal(row.querySelector(".target-test-tps").textContent, "22.50 tok/s");
    assert.equal(row.dataset.autoDisabled, "true");
    assert.equal(row.dataset.enabled, "false");
});

test("Provider names and upstream model IDs retain their internal slashes", async () => {
    const { context, row, requests, providers } = createHarness("team/provider/org/model");
    providers[0] = { name: "team/provider", model_list: ["org/model"], api: "https://upstream.test" };
    await context.testTargetRow(row);
    assert.deepEqual(JSON.parse(requests.at(-1).options.body).models, ["org/model"]);
});

test("Auth Group probes select the first enabled entry and preserve a manual choice", async () => {
    const { context, row, requests, providers } = createHarness();
    providers[0].auth_group = "test group";
    providers[0].api_key = "unused-legacy-key";
    await context.testTargetRow(row);
    let payload = JSON.parse(requests.at(-1).options.body);
    assert.equal(payload.auth_entry_id, "active");
    assert.equal(payload.api_key, "");
    assert.equal(payload.auth_group, "test group");
    assert.equal(row.querySelector(".mapping-target-test-auth").hidden, false);
    row.querySelector(".target-test-auth-entry").value = "alternate";
    await context.testTargetRow(row);
    payload = JSON.parse(requests.at(-1).options.body);
    assert.equal(payload.auth_entry_id, "alternate");
});

test("missing metrics remain unknown rather than appearing as zero", async () => {
    const { context, row, providers } = createHarness();
    context.requestJson = async url => url === "/api/providers" ? providers : { results: [{ available: true, tps: null }] };
    await context.testTargetRow(row);
    assert.equal(row.querySelector(".mapping-target-test-status").textContent, "可用");
    assert.equal(row.querySelector(".target-test-latency").textContent, "--");
    assert.equal(row.querySelector(".target-test-tps").textContent, "--");
    assert.equal(context.formatTargetTestMetric("invalid", "ms"), "--");
    assert.equal(context.formatTargetTestMetric(0, "ms"), "0.00 ms");
});

test("test failures show details without changing mapping runtime flags", async () => {
    const { context, row, providers } = createHarness();
    context.requestJson = async url => {
        if (url === "/api/providers") return providers;
        throw new Error("HTTP 503: upstream unavailable");
    };
    await context.testTargetRow(row);
    assert.equal(row.querySelector(".mapping-target-test-status").textContent, "不可用");
    assert.equal(row.querySelector(".mapping-target-test-error").textContent, "HTTP 503: upstream unavailable");
    assert.equal(row.querySelector(".mapping-target-test-error").hidden, false);
    assert.equal(row.dataset.available, "true");
    assert.equal(row.dataset.enabled, "true");
    assert.equal(row.dataset.autoDisabled, "false");
});

test("unsupported targets are not falsely reported as unavailable", async () => {
    const { context, row, requests } = createHarness("oauth-model");
    await context.testTargetRow(row);
    assert.equal(requests.length, 1);
    assert.equal(row.querySelector(".mapping-target-test-status").textContent, "未测试");
    assert.match(row.querySelector(".mapping-target-test-error").textContent, /Provider/);
});

test("duplicate clicks and late results cannot overwrite an edited or removed row", async () => {
    for (const action of ["edit", "delete", "close"]) {
        const { context, row, providers } = createHarness();
        let finish;
        let submitted = 0;
        context.requestJson = async url => {
            if (url === "/api/providers") return providers;
            submitted += 1;
            return new Promise(resolve => { finish = resolve; });
        };
        const pending = context.testTargetRow(row);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(row.querySelector(".mapping-target-test-status").textContent, "测试中");
        await context.testTargetRow(row);
        assert.equal(submitted, 1);
        const controller = row.targetTestController;
        if (action === "edit") {
            row.querySelector(".target-model-id").value = "alpha/replacement";
            context.syncTargetModelSelection(row);
        } else if (action === "delete") {
            context.removeTargetRow(row);
        } else {
            context.resetTargetTest(row);
        }
        assert.equal(controller.signal.aborted, true);
        finish({ results: [{ available: true, first_token_latency_ms: 100, tps: 99 }] });
        await pending;
        assert.equal(row.targetTestResult, null);
        assert.equal(row.querySelector(".mapping-target-test-status").textContent, "未测试");
    }
});
