import json
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from flask import Flask, Response
from src.application.app_context import AppContext
from src.config.model_mapping_config import ModelMappingSchema
from src.presentation.model_mapping_controller import ModelMappingController
from src.presentation.proxy_controller import ProxyController
from src.repositories import ApiKeyRepository, ModelMappingRepository, UserRepository
from src.services import ApiKeyService, AuthenticationService, ModelCatalogService, UserService
from src.services.model_mapping_service import ModelMappingService
from src.services.proxy_service import ProxyErrorInfo
from src.utils.database import create_connection_factory
from src.utils.local_time import parse_local_datetime


class FakeLogger:
    def info(self, msg: str, *args) -> None:
        del msg, args

    def warning(self, msg: str, *args) -> None:
        del msg, args

    def error(self, msg: str, *args) -> None:
        del msg, args


class FakeConfigManager:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.config = {
            "providers": [
                {
                    "name": "alpha",
                    "enabled": True,
                    "model_list": ["fast", "stable"],
                }
            ]
        }

    def is_model_mapping_enabled(self) -> bool:
        return self.enabled

    def is_auth_enabled(self) -> bool:
        return False

    def get_raw_config(self) -> dict:
        return self.config


class FakeProviderManager:
    def __init__(self, model_ids: tuple[str, ...] = ("alpha/fast", "alpha/stable")) -> None:
        self.model_ids = model_ids
        self.providers: dict[str, Any] = {}
        self.lookup_ids: list[str] = []

    def list_model_names(self) -> tuple[str, ...]:
        return self.model_ids

    def get_provider_for_model(self, model_id: str) -> Any | None:
        self.lookup_ids.append(model_id)
        return self.providers.get(model_id)


class FakeOAuthService:
    def __init__(
        self,
        *,
        catalog_ids: tuple[str, ...] = (),
        image_ids: tuple[str, ...] = (),
        runtime_ids: tuple[str, ...] = (),
        image_runtime_ids: tuple[str, ...] = (),
    ) -> None:
        self.catalog_ids = catalog_ids
        self.image_ids = image_ids
        self.runtime_ids = runtime_ids
        self.image_runtime_ids = image_runtime_ids

    def list_models(self) -> dict:
        return {"models": [{"id": model_id} for model_id in self.catalog_ids]}

    def list_image_models(self) -> dict:
        return {"models": [{"id": model_id} for model_id in self.image_ids]}

    def list_model_names(self) -> tuple[str, ...]:
        return self.runtime_ids

    def list_image_model_names(self) -> tuple[str, ...]:
        return self.image_runtime_ids


class ModelMappingSchemaTests(unittest.TestCase):
    def test_mapping_id_allows_letter_or_underscore_prefix(self) -> None:
        letter = ModelMappingSchema.from_mapping({"id": "public_v1", "targets": [{"model_id": "alpha/fast"}]})
        underscore = ModelMappingSchema.from_mapping({"id": "_public2", "targets": [{"model_id": "alpha/fast"}]})
        hyphen = ModelMappingSchema.from_mapping({"id": "gpt-image-2", "targets": [{"model_id": "alpha/fast"}]})
        dot = ModelMappingSchema.from_mapping({"id": "gpt.image.v2", "targets": [{"model_id": "alpha/fast"}]})

        self.assertEqual("public_v1", letter.id)
        self.assertEqual("_public2", underscore.id)
        self.assertEqual("gpt-image-2", hyphen.id)
        self.assertEqual("gpt.image.v2", dot.id)

    def test_mapping_id_rejects_digit_prefix_and_duplicate_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能以字母或下划线开头"):
            ModelMappingSchema.from_mapping({"id": "2public", "targets": [{"model_id": "alpha/fast"}]})
        with self.assertRaisesRegex(ValueError, "不能重复选择"):
            ModelMappingSchema.from_mapping(
                {
                    "id": "public",
                    "targets": [
                        {"model_id": "alpha/fast"},
                        {"model_id": "alpha/fast"},
                    ],
                }
            )

    def test_priority_and_cooldown_allow_zero(self) -> None:
        mapping = ModelMappingSchema.from_mapping(
            {
                "id": "public",
                "cooldown_seconds_on_429": 0,
                "targets": [{"model_id": "alpha/fast", "priority": 0}],
            }
        )

        self.assertEqual(0, mapping.cooldown_seconds_on_429)
        self.assertEqual(0, mapping.targets[0].priority)

    def test_mapping_enabled_defaults_true_and_requires_boolean(self) -> None:
        enabled = ModelMappingSchema.from_mapping({"id": "public", "targets": [{"model_id": "alpha/fast"}]})
        disabled = ModelMappingSchema.from_mapping(
            {"id": "private", "enabled": False, "targets": [{"model_id": "alpha/fast"}]}
        )

        self.assertTrue(enabled.enabled)
        self.assertFalse(disabled.enabled)
        with self.assertRaisesRegex(ValueError, "启用状态必须是布尔值"):
            ModelMappingSchema.from_mapping(
                {"id": "invalid", "enabled": "false", "targets": [{"model_id": "alpha/fast"}]}
            )

    def test_strategy_defaults_to_highest_priority_and_validates_explicit_value(self) -> None:
        default_mapping = ModelMappingSchema.from_mapping({"id": "default", "targets": [{"model_id": "alpha/fast"}]})
        empty_mapping = ModelMappingSchema.from_mapping(
            {"id": "empty", "strategy": "", "targets": [{"model_id": "alpha/fast"}]}
        )
        sticky_mapping = ModelMappingSchema.from_mapping(
            {"id": "sticky", "strategy": "sticky_failover", "targets": [{"model_id": "alpha/fast"}]}
        )

        self.assertEqual("highest_priority", default_mapping.strategy)
        self.assertEqual("highest_priority", empty_mapping.strategy)
        self.assertEqual("sticky_failover", sticky_mapping.strategy)
        with self.assertRaisesRegex(ValueError, "模型映射策略必须是"):
            ModelMappingSchema.from_mapping(
                {"id": "invalid", "strategy": "random", "targets": [{"model_id": "alpha/fast"}]}
            )

    def test_priority_and_cooldown_reject_negative_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "目标优先级必须是非负整数"):
            ModelMappingSchema.from_mapping({"id": "public", "targets": [{"model_id": "alpha/fast", "priority": -1}]})
        with self.assertRaisesRegex(ValueError, "429 冷却时间必须是非负整数"):
            ModelMappingSchema.from_mapping(
                {
                    "id": "public",
                    "cooldown_seconds_on_429": -1,
                    "targets": [{"model_id": "alpha/fast"}],
                }
            )

    def test_mapping_editor_uses_target_actions_and_unclipped_dropdown(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        template = (project_root / "src/presentation/templates/model_mappings.html").read_text(encoding="utf-8")
        stylesheet = (project_root / "src/presentation/static/css/model_mappings.css").read_text(encoding="utf-8")
        settings_template = (project_root / "src/presentation/templates/settings.html").read_text(encoding="utf-8")

        self.assertIn("model_mappings.css?v=20260807-7", template)
        self.assertIn('id="mappingStrategySelect"', template)
        self.assertIn('<option value="highest_priority">最高优先级</option>', template)
        self.assertIn('<option value="sticky_failover">粘滞故障切换</option>', template)
        self.assertNotIn("最高优先级（恢复后自动切回）", template)
        self.assertNotIn("粘滞故障切换（保持当前目标）", template)
        self.assertIn('aria-describedby="mappingStrategyHelp"', template)
        self.assertIn("高优先级目标恢复后自动切回", template)
        self.assertIn('strategy: document.getElementById("mappingStrategySelect").value', template)
        self.assertIn('mapping.strategy || "highest_priority"', template)
        self.assertIn('class="mapping-strategy-badge">${strategyLabel}</span>', template)
        self.assertNotIn("mapping-strategy-label", template)
        self.assertNotIn("<th>策略</th>", template)
        self.assertNotIn("<span>策略</span>", template)
        self.assertNotIn("target-enabled", template)
        self.assertIn('id="mappingCooldownInput" min="0"', template)
        self.assertIn('class="form-control target-priority" aria-label="优先级" min="0"', template)
        self.assertIn("mapping-help-button", template)
        self.assertIn("mapping-target-unavailable", template)
        self.assertIn('class="mapping-target-priority-control"', template)
        self.assertIn("mapping-target-unavailable-help", template)
        self.assertIn("该目标模型已不在当前可用模型目录中", template)
        self.assertIn("mapping-target-auto-disabled", template)
        self.assertIn("mapping-target-auto-disabled-help", template)
        self.assertIn('manuallyDisabled ? "手动禁用" : failureTooltip', template)
        self.assertIn("row.dataset.failureTooltip = failureTooltip", template)
        self.assertIn("最后一次调用失败", template)
        self.assertIn("width: 72px", stylesheet)
        self.assertIn("border-radius: 999px", stylesheet)
        self.assertIn(".mapping-strategy-badge", stylesheet)
        self.assertIn("min-height: 24px", stylesheet)
        self.assertIn("background: rgba(58, 127, 237, 0.08)", stylesheet)
        self.assertIn(':root[data-theme="dark"] .mapping-strategy-badge', stylesheet)
        self.assertIn('<table class="mapping-target-table"', template)
        self.assertIn('class="mapping-target-add-button" id="addTargetBtn"', template)
        self.assertIn("<span>新增目标模型</span>", template)
        self.assertIn('class="mapping-target-action mapping-toggle-target"', template)
        self.assertIn(
            'class="mapping-target-action is-delete mapping-target-delete-trigger mapping-remove-target">删除', template
        )
        self.assertIn('class="mapping-targets-cell"', template)
        self.assertIn("const targets = Array.isArray(mapping.targets)", template)
        self.assertIn("function getTargetPreview(targets, limit = 5)", template)
        self.assertIn("preview.hiddenCount", template)
        self.assertIn("<th>目标模型</th>", template)
        self.assertNotIn('mapping.current_target_model_id || "-"', template)
        self.assertIn("data-mapping-delete-trigger=", template)
        self.assertIn("mappingDeletePopover", template)
        self.assertIn("mappingTargetDeletePopover", template)
        self.assertIn("toggleDeleteTargetConfirm", template)
        self.assertNotIn("window.confirm", template)
        self.assertIn("function selectedTargetIds()", template)
        self.assertIn("!selectedIds.has(modelId)", template)
        self.assertIn(".filter(target => target.model_id)", template)
        self.assertNotIn("payload.targets.some(target => !target.model_id)", template)
        self.assertIn(".mapping-cooldown-cell", stylesheet)
        self.assertIn(".mapping-list-actions-column {\n    width: 248px;\n}", stylesheet)
        self.assertIn("function copyMapping(encodedId)", template)
        self.assertIn("/api/model-mappings/${encodeURIComponent(mappingId)}/copy", template)
        self.assertIn("onclick=\"copyMapping('${encodedId}')\"", template)
        self.assertIn(".mapping-target-label .form-label", stylesheet)
        self.assertIn('class="mapping-id" title="${escapeHtml(mapping.id)}"', template)
        self.assertNotIn("mapping-conflict", template)
        self.assertNotIn("mapping-conflict", stylesheet)
        self.assertNotIn("覆盖 ${escapeHtml(mapping.conflict_source)} 同名模型", template)
        self.assertIn('class="mapping-table" data-resizable-columns="model-mappings"', template)
        self.assertNotIn("mappingIdColumnWidthStorageKey", template)
        self.assertNotIn('data-resizable-columns="model-mapping-targets"', template)
        self.assertIn('row.classList.toggle("is-disabled"', template)
        self.assertIn(".mapping-target-row.is-disabled td", stylesheet)
        self.assertIn('class="mapping-drag-handle mapping-target-drag-handle"', template)
        self.assertIn("function handleTargetRowDrop", template)
        self.assertIn("function buildDroppedMappingOrderIds", template)
        self.assertIn('buildMappingTable("disabled"', template)
        self.assertIn('data-mapping-group-select="${groupKey}"', template)
        self.assertIn('data-mapping-export-button="${groupKey}"', template)
        self.assertIn("body: JSON.stringify({ mapping_ids: mappingIds })", template)
        self.assertIn('class="btn btn-primary" id="importMappingsBtn"', template)
        self.assertIn('class="mapping-group-actions"', template)
        self.assertIn('class="btn btn-toolbar-secondary mapping-group-data-btn"', template)
        self.assertNotIn('class="toolbar-pill">${mappings.length} 个</span>', template)
        self.assertNotIn('id="exportMappingsBtn"', template)
        self.assertIn(".mapping-target-row.is-disabled .form-control", stylesheet)
        self.assertIn(".btn-toolbar-secondary", stylesheet)
        self.assertIn("min-height: 26px", stylesheet)
        self.assertIn(".mapping-model-tag", stylesheet)
        self.assertIn("font-family: var(--font-sans);", stylesheet)
        self.assertNotIn(".mapping-model-tag.is-current", stylesheet)
        self.assertNotIn(".mapping-model-tag.is-disabled", stylesheet)
        self.assertNotIn(".mapping-model-tag.is-unavailable", stylesheet)
        self.assertIn('class="mapping-model-tag" title="${escapeHtml(targetId)}"', template)
        self.assertIn(".mapping-group-table-wrap .mapping-table th", stylesheet)
        self.assertIn("background: transparent", stylesheet)
        create_editor = template[
            template.index("function openCreateMapping()") : template.index("function openEditMapping")
        ]
        self.assertNotIn("createTargetRow", create_editor)
        self.assertIn(".model-mappings-page .modal-content", stylesheet)
        self.assertNotIn("min-height: min(680px", stylesheet)
        self.assertIn("overflow: visible;", stylesheet)
        self.assertIn("font-family: var(--font-sans);", stylesheet)
        self.assertIn("font-family: var(--font-mono);", stylesheet)
        self.assertIn(".mapping-target-add-button", stylesheet)
        self.assertIn(".model-mappings-page .modal .form-select", stylesheet)
        self.assertIn("linear-gradient(45deg, transparent 50%, var(--text-muted) 50%)", stylesheet)
        self.assertIn("calc(100% - 18px) calc(50% - 2px)", stylesheet)
        self.assertIn("width: max-content", stylesheet)
        self.assertIn("max-width: min(300px, calc(100vw - 48px))", stylesheet)
        self.assertIn(':root[data-theme="dark"] .model-mappings-page .modal-content', stylesheet)
        self.assertIn(':root[data-theme="dark"] .model-mappings-page .modal .form-control', stylesheet)
        self.assertIn(':root[data-theme="dark"] .model-mappings-page .mapping-toolbar .btn-secondary', stylesheet)
        self.assertIn("模型映射提供固定的下游模型 ID", settings_template)
        self.assertIn("典型场景：", settings_template)
        self.assertIn("下游客户端始终请求同一个模型 ID", settings_template)
        self.assertIn('data-settings-help-topic="model_mapping"', settings_template)
        self.assertIn("toggleSettingsHelp('model_mapping', event)", settings_template)
        self.assertNotIn('<p class="section-subtitle"><strong>功能：</strong>', settings_template)
        self.assertIn("settings.css?v=20260807-1", settings_template)

    def test_mapping_drag_order_stays_within_enabled_group(self) -> None:
        template_path = (
            Path(__file__).resolve().parents[1] / "src" / "presentation" / "templates" / "model_mappings.html"
        )
        html = template_path.read_text(encoding="utf-8")
        script_start = html.index("function isMappingEnabled")
        script_end = html.index("function getRowDragPlacement", script_start)
        script = html[script_start:script_end]
        node_script = f"""
const vm = require("vm");
const sandbox = {{
  modelMappings: [
    {{ id: "enabled-a", enabled: true }},
    {{ id: "enabled-b", enabled: true }},
    {{ id: "disabled-a", enabled: false }},
    {{ id: "disabled-b", enabled: false }},
  ],
  selectedMappingIds: new Set(),
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(script)}, sandbox);
process.stdout.write(JSON.stringify({{
  enabled: sandbox.buildDroppedMappingOrderIds("enabled-b", "enabled-a", false),
  disabled: sandbox.buildDroppedMappingOrderIds("disabled-a", "disabled-b", true),
  crossGroup: sandbox.buildDroppedMappingOrderIds("enabled-a", "disabled-a", false),
}}));
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))

        self.assertEqual(["enabled-b", "enabled-a", "disabled-a", "disabled-b"], payload["enabled"])
        self.assertEqual(["enabled-a", "enabled-b", "disabled-b", "disabled-a"], payload["disabled"])
        self.assertIsNone(payload["crossGroup"])


class ModelMappingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root_path = Path(self.temp_dir.name)
        self.config_manager = FakeConfigManager()
        self.provider_manager = FakeProviderManager()
        self.codex_service = FakeOAuthService(
            catalog_ids=("gpt_text",),
            image_ids=("gpt_image",),
            runtime_ids=("gpt_text",),
            image_runtime_ids=("gpt_image",),
        )
        self.claude_service = FakeOAuthService(catalog_ids=("claude_text",), runtime_ids=("claude_text",))
        self.repository = ModelMappingRepository(create_connection_factory(root_path / "mappings.db"))
        self.ctx = AppContext(
            logger=FakeLogger(),
            config_manager=self.config_manager,
            root_path=root_path,
            flask_app=Flask(__name__),
        )
        self.service = self._build_service()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_service(self) -> ModelMappingService:
        return ModelMappingService(
            self.ctx,
            self.repository,
            provider_manager=self.provider_manager,
            codex_oauth_service=self.codex_service,
            claude_oauth_service=self.claude_service,
        )

    @staticmethod
    def _mapping_payload(mapping_id: str = "public_model") -> dict:
        return {
            "id": mapping_id,
            "cooldown_seconds_on_429": 60,
            "targets": [
                {"model_id": "alpha/fast", "priority": 10, "enabled": True},
                {"model_id": "alpha/stable", "priority": 5, "enabled": True},
            ],
        }

    def test_create_allows_oauth_catalog_conflicts_and_marks_shadowed(self) -> None:
        for mapping_id, source in (
            ("gpt_text", "Codex OAuth"),
            ("gpt_image", "Codex OAuth 图片"),
            ("claude_text", "Claude OAuth"),
        ):
            with self.subTest(mapping_id=mapping_id):
                mapping = self.service.create_mapping(self._mapping_payload(mapping_id))
                self.assertTrue(mapping["effective"])
                self.assertEqual(source, mapping["conflict_source"])
                self.assertIn(mapping_id, self.service.list_mapping_ids())

    def test_mapping_ids_are_exposed_when_enabled_and_shadow_oauth_conflicts(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        self.assertEqual(("public_model",), self.service.list_mapping_ids())

        self.config_manager.enabled = False
        self.assertEqual((), self.service.list_mapping_ids())

        self.config_manager.enabled = True
        self.codex_service.catalog_ids = ("gpt_text", "public_model")
        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        self.assertTrue(mapping["effective"])
        self.assertEqual("Codex OAuth", mapping["conflict_source"])
        self.assertEqual(("public_model",), self.service.list_mapping_ids())

        self.codex_service.catalog_ids = ("gpt_text",)
        self.assertEqual(("public_model",), self.service.list_mapping_ids())

    def test_mapping_level_disable_and_reorder_persist(self) -> None:
        self.service.create_mapping(self._mapping_payload("enabled_a"))
        disabled_payload = self._mapping_payload("disabled_a")
        disabled_payload["enabled"] = False
        self.service.create_mapping(disabled_payload)
        self.service.create_mapping(self._mapping_payload("enabled_b"))

        initial = self.service.list_mappings()
        self.assertEqual(["enabled_a", "enabled_b", "disabled_a"], [item["id"] for item in initial])
        self.assertNotIn("disabled_a", self.service.list_mapping_ids())
        self.assertFalse(self.service.get_mapping("disabled_a")["effective"])

        result = self.service.reorder_mappings(["enabled_b", "enabled_a", "disabled_a"])
        self.assertEqual(3, result["count"])
        recreated = self._build_service()
        self.assertEqual(
            ["enabled_b", "enabled_a", "disabled_a"],
            [item["id"] for item in recreated.list_mappings()],
        )
        with self.assertRaisesRegex(ValueError, "已启用模型映射必须排在"):
            self.service.reorder_mappings(["disabled_a", "enabled_b", "enabled_a"])

        enabled = self.service.set_mapping_enabled("disabled_a", enabled=True)
        self.assertTrue(enabled["effective"])
        self.assertIn("disabled_a", self.service.list_mapping_ids())
        disabled = self.service.set_mapping_enabled("enabled_b", enabled=False)
        self.assertFalse(disabled["effective"])
        self.assertFalse(self.service.has_mapping("enabled_b"))

    def test_copy_mapping_inserts_definition_below_source_without_runtime_state(self) -> None:
        source_payload = self._mapping_payload("enabled_a")
        source_payload["strategy"] = "sticky_failover"
        self.service.create_mapping(source_payload)
        self.service.create_mapping(self._mapping_payload("enabled_b"))
        selected = self.service.acquire_target("enabled_a")
        self.service.record_failure(
            selected,
            status_code=500,
            error_type="upstream_error",
            error_message="failed",
        )

        copied = self.service.copy_mapping("enabled_a")

        self.assertEqual("enabled_a_1", copied["id"])
        self.assertEqual(
            ["enabled_a", "enabled_a_1", "enabled_b"],
            [mapping["id"] for mapping in self.service.list_mappings()],
        )
        self.assertEqual(60, copied["cooldown_seconds_on_429"])
        self.assertEqual("sticky_failover", copied["strategy"])
        self.assertEqual(["alpha/fast", "alpha/stable"], [target["model_id"] for target in copied["targets"]])
        self.assertTrue(all(target["status"] == "available" for target in copied["targets"]))
        self.assertIsNone(copied["current_target_model_id"])

    def test_existing_mapping_table_migrates_enabled_and_sort_order(self) -> None:
        database_path = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(database_path) as conn:
            conn.executescript(
                """
                CREATE TABLE model_mappings (
                    id TEXT PRIMARY KEY,
                    cooldown_seconds_on_429 INTEGER NOT NULL DEFAULT 60,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO model_mappings VALUES ('later', 60, '2026-01-02 00:00:00', '2026-01-02 00:00:00');
                INSERT INTO model_mappings VALUES ('earlier', 60, '2026-01-01 00:00:00', '2026-01-01 00:00:00');
                """
            )

        repository = ModelMappingRepository(create_connection_factory(database_path))
        mappings = repository.list_mappings()

        self.assertEqual(["earlier", "later"], [mapping["id"] for mapping in mappings])
        self.assertEqual([True, True], [mapping["enabled"] for mapping in mappings])
        self.assertEqual([0, 1], [mapping["sort_order"] for mapping in mappings])
        self.assertEqual(["highest_priority", "highest_priority"], [mapping["strategy"] for mapping in mappings])

    def test_mapping_order_and_status_routes(self) -> None:
        self.service.create_mapping(self._mapping_payload("first"))
        self.service.create_mapping(self._mapping_payload("second"))
        catalog_syncs = []
        ModelMappingController(
            self.ctx,
            self.service,
            AuthenticationService(self.ctx),
            model_catalog_changed_callback=lambda: catalog_syncs.append(True),
        )
        client = self.ctx.flask_app.test_client()

        reorder_response = client.put("/api/model-mappings/order", json={"ids": ["second", "first"]})
        disable_response = client.post("/api/model-mappings/second/disable")
        enable_response = client.post("/api/model-mappings/second/enable")
        copy_response = client.post("/api/model-mappings/second/copy")

        self.assertEqual(200, reorder_response.status_code)
        self.assertEqual(["second", "first"], reorder_response.get_json()["ids"])
        self.assertFalse(disable_response.get_json()["enabled"])
        self.assertTrue(enable_response.get_json()["enabled"])
        self.assertEqual(201, copy_response.status_code)
        self.assertEqual("second_1", copy_response.get_json()["id"])
        self.assertEqual(3, len(catalog_syncs))

    def test_image_model_is_available_as_mapping_target(self) -> None:
        self.assertIn("gpt_image", self.service.list_available_target_model_ids())
        mapping = self.service.create_mapping(
            {
                "id": "gpt-image-2",
                "targets": [{"model_id": "gpt_image"}],
            }
        )
        self.assertTrue(mapping["effective"])
        self.assertEqual("available", mapping["targets"][0]["status"])

    def test_target_catalog_excludes_disabled_provider_and_includes_oauth_models(self) -> None:
        self.config_manager.config["providers"].append(
            {
                "name": "disabled",
                "enabled": False,
                "model_list": ["hidden"],
            }
        )

        targets = self.service.list_available_target_model_ids()

        self.assertIn("alpha/fast", targets)
        self.assertNotIn("disabled/hidden", targets)
        self.assertIn("gpt_text", targets)
        self.assertIn("gpt_image", targets)
        self.assertIn("claude_text", targets)

    def test_highest_priority_strategy_reselects_recovered_target(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        self.provider_manager.model_ids = ("alpha/stable",)
        first = self.service.acquire_target("public_model")
        self.provider_manager.model_ids = ("alpha/fast", "alpha/stable")
        second = self.service.acquire_target("public_model")

        self.assertEqual("alpha/stable", first.target_model_id)
        self.assertEqual("alpha/fast", second.target_model_id)

    def test_sticky_selection_keeps_current_target_after_higher_priority_recovers(self) -> None:
        payload = self._mapping_payload()
        payload["strategy"] = "sticky_failover"
        self.service.create_mapping(payload)
        self.provider_manager.model_ids = ("alpha/stable",)
        first = self.service.acquire_target("public_model")
        self.provider_manager.model_ids = ("alpha/fast", "alpha/stable")
        recreated = self._build_service()
        second = recreated.acquire_target("public_model")

        self.assertEqual("alpha/stable", first.target_model_id)
        self.assertEqual("alpha/stable", second.target_model_id)

    def test_same_priority_uses_target_order(self) -> None:
        payload = self._mapping_payload()
        payload["targets"][1]["priority"] = 10
        self.service.create_mapping(payload)

        selected = self.service.acquire_target("public_model")

        self.assertEqual("alpha/fast", selected.target_model_id)

    def test_429_cools_target_then_fails_over_without_auto_disabling(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        selected = self.service.acquire_target("public_model")

        with patch(
            "src.services.model_mapping_service.now_local_datetime", return_value=datetime(2099, 8, 5, 10, 0, 0)
        ):
            self.service.record_failure(
                selected,
                status_code=429,
                error_type="rate_limit_error",
                error_message="rate limited",
                response_headers={"Retry-After": "120"},
            )

        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        failed_target = mapping["targets"][0]
        next_target = self.service.acquire_target("public_model")
        self.assertFalse(failed_target["auto_disabled"])
        self.assertEqual("cooldown", failed_target["status"])
        self.assertEqual(datetime(2099, 8, 5, 10, 2, 0), parse_local_datetime(failed_target["cooldown_until"]))
        self.assertEqual("alpha/stable", next_target.target_model_id)

        with patch(
            "src.services.model_mapping_service.now_local_datetime", return_value=datetime(2099, 8, 5, 10, 2, 1)
        ):
            recovered_target = self.service.acquire_target("public_model")
        self.assertEqual("alpha/fast", recovered_target.target_model_id)

    def test_non_429_failure_auto_disables_until_manual_enable(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        selected = self.service.acquire_target("public_model")
        self.service.record_failure(
            selected,
            status_code=500,
            error_type="upstream_error",
            error_message="failed",
        )

        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        self.assertEqual("auto_disabled", mapping["targets"][0]["status"])
        self.assertEqual("alpha/stable", self.service.acquire_target("public_model").target_model_id)

        restored = self.service.set_target_enabled("public_model", "alpha/fast", enabled=True)
        target = next(item for item in restored["targets"] if item["model_id"] == "alpha/fast")
        self.assertEqual("available", target["status"])
        self.assertEqual("alpha/fast", self.service.acquire_target("public_model").target_model_id)

    def test_automatically_disabled_targets_retry_when_no_normal_candidate_remains(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        excluded_targets: set[str] = set()
        for _ in range(2):
            selected = self.service.acquire_target("public_model", excluded_targets)
            excluded_targets.add(selected.target_model_id)
            self.service.record_failure(
                selected,
                status_code=500,
                error_type="upstream_error",
                error_message="failed",
            )

        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        self.assertTrue(all(target["status"] == "auto_disabled" for target in mapping["targets"]))
        fallback = self.service.acquire_target("public_model", excluded_targets)
        self.assertEqual("alpha/fast", fallback.target_model_id)
        self.assertTrue(fallback.is_fallback)
        self.assertEqual("alpha/fast", self.service.acquire_target("public_model").target_model_id)

    def test_fallback_ignores_request_exclusion_and_keeps_highest_priority(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        excluded_targets = {"alpha/fast", "alpha/stable"}

        fallback = self.service.acquire_target("public_model", excluded_targets)

        self.assertEqual("alpha/fast", fallback.target_model_id)
        self.assertTrue(fallback.is_fallback)

    def test_single_automatically_disabled_target_is_retried_on_next_request(self) -> None:
        payload = self._mapping_payload()
        payload["targets"] = [payload["targets"][0]]
        self.service.create_mapping(payload)
        selected = self.service.acquire_target("public_model")
        self.service.record_failure(
            selected,
            status_code=500,
            error_type="upstream_error",
            error_message="failed",
        )

        self.assertEqual("alpha/fast", self.service.acquire_target("public_model").target_model_id)

    def test_all_manually_disabled_targets_fall_back_to_highest_priority(self) -> None:
        payload = self._mapping_payload()
        for target in payload["targets"]:
            target["enabled"] = False
        self.service.create_mapping(payload)

        selected = self.service.acquire_target("public_model")

        self.assertEqual("alpha/fast", selected.target_model_id)

    def test_unavailable_target_cannot_toggle_but_mapping_can_be_deleted(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        self.provider_manager.model_ids = ("alpha/stable",)

        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        self.assertEqual("unavailable", mapping["targets"][0]["status"])
        with self.assertRaisesRegex(ValueError, "当前不可用"):
            self.service.set_target_enabled("public_model", "alpha/fast", enabled=False)
        changed = self._mapping_payload()
        changed["targets"][0]["priority"] = 99
        with self.assertRaisesRegex(ValueError, "只能删除"):
            self.service.update_mapping("public_model", changed)

        self.service.delete_mapping("public_model")
        self.assertIsNone(self.service.get_mapping("public_model"))

    def test_export_import_excludes_runtime_state_and_rejects_duplicates(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        selected = self.service.acquire_target("public_model")
        self.service.record_failure(
            selected,
            status_code=500,
            error_type="upstream_error",
            error_message="failed",
        )
        exported = self.service.export_mappings()

        self.assertEqual("llm_proxy.model_mappings", exported["kind"])
        self.assertNotIn("auto_disabled", exported["model_mappings"][0]["targets"][0])
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.service.import_mappings(exported)

        second_repository = ModelMappingRepository(create_connection_factory(Path(self.temp_dir.name) / "imported.db"))
        imported_service = ModelMappingService(
            self.ctx,
            second_repository,
            provider_manager=self.provider_manager,
            codex_oauth_service=self.codex_service,
            claude_oauth_service=self.claude_service,
        )
        result = imported_service.import_mappings(exported)
        imported = imported_service.get_mapping("public_model")
        assert imported is not None
        self.assertEqual(1, result["count"])
        self.assertEqual("available", imported["targets"][0]["status"])
        self.assertIsNone(imported["current_target_model_id"])

    def test_runtime_failure_state_survives_service_recreation(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        selected = self.service.acquire_target("public_model")
        self.service.record_failure(
            selected,
            status_code=500,
            error_type="upstream_error",
            error_message="failed",
        )

        recreated = self._build_service()
        mapping = recreated.get_mapping("public_model")
        assert mapping is not None

        self.assertEqual("auto_disabled", mapping["targets"][0]["status"])
        self.assertEqual("alpha/stable", recreated.acquire_target("public_model").target_model_id)

    def test_late_callback_after_target_deletion_does_not_recreate_runtime_rows(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        selected = self.service.acquire_target("public_model")
        self.service.delete_mapping("public_model")

        self.service.record_success(selected)
        self.service.record_failure(
            selected,
            status_code=500,
            error_type="upstream_error",
            error_message="late failure",
        )

        with self.repository._get_connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM model_mappings").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM model_mapping_targets").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM model_mapping_target_runtime").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM model_mapping_runtime").fetchone()[0])

    def test_disabled_switch_preserves_user_and_api_key_mapping_permissions(self) -> None:
        self.service.create_mapping(self._mapping_payload())
        catalog = ModelCatalogService(self.ctx, model_mapping_service=self.service)
        user_repository = UserRepository(create_connection_factory(Path(self.temp_dir.name) / "users.db"))
        api_key_repository = ApiKeyRepository(create_connection_factory(Path(self.temp_dir.name) / "keys.db"))
        user_service = UserService(self.ctx, user_repository, catalog)
        api_key_service = ApiKeyService(self.ctx, api_key_repository, catalog)
        user_id = user_service.create_user("alice", "127.0.0.1")
        assert user_id is not None
        user_service.update_user(
            user_id,
            model_permissions_provided=True,
            model_permissions=["public_model"],
        )
        api_key = api_key_service.create_api_key("mapping-key", ["public_model"])

        self.config_manager.enabled = False
        self.assertNotIn("public_model", user_service.get_available_models())
        self.assertNotIn("public_model", api_key_service.get_available_models())
        self.assertEqual(0, user_service.sync_model_permissions())
        self.assertEqual(0, api_key_service.sync_model_permissions())
        self.assertIn("public_model", str(user_repository.get_by_id(user_id)["model_permissions"]))
        self.assertIn("public_model", str(api_key_repository.get_by_id(api_key["id"])["model_permissions"]))

        self.config_manager.enabled = True
        self.assertEqual(["public_model"], user_service.get_user_by_id(user_id)["model_permissions"])
        self.assertEqual(["public_model"], api_key_service.get_api_key_by_id(api_key["id"])["model_permissions"])


class FakeMappedProxyService:
    def __init__(self, outcomes: dict[str, Any], calls: list[str]) -> None:
        self._outcomes = outcomes
        self._calls = calls

    def proxy_request(self, *args: Any, **kwargs: Any) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        request_data = next(item for item in args if isinstance(item, dict) and "model" in item)
        model_id = request_data["model"]
        self._calls.append(model_id)
        outcome = self._outcomes[model_id]
        if callable(outcome):
            return outcome(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        response, status_code, failure = outcome
        if response is not None and 200 <= status_code < 300 and kwargs.get("on_complete"):
            kwargs["on_complete"]({"response_model": model_id})
        return response, status_code, failure


class FakeMappedOAuthProxyService(FakeMappedProxyService):
    def __init__(self, model_ids: tuple[str, ...], outcomes: dict[str, Any], calls: list[str]) -> None:
        super().__init__(outcomes, calls)
        self._model_ids = set(model_ids)

    def has_model(self, model_id: str) -> bool:
        return model_id in self._model_ids


class FakeMappedImageProxyService(FakeMappedOAuthProxyService):
    def has_image_model(self, model_id: str) -> bool:
        return model_id in self._model_ids

    def proxy_image_request(self, *args: Any, **kwargs: Any) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        return self.proxy_request(*args, **kwargs)


class ModelMappingProxyControllerTests(ModelMappingServiceTests):
    def test_model_mapping_route_precedes_provider_lookup(self) -> None:
        self.service.create_mapping(self._mapping_payload("gpt_text"))
        self.provider_manager.providers["gpt_text"] = SimpleNamespace(name="shadowed-provider")
        controller = object.__new__(ProxyController)
        controller._model_mapping_service = self.service
        controller._provider_manager = self.provider_manager
        controller._codex_proxy_service = None
        controller._claude_proxy_service = None

        provider, is_codex, is_claude, is_mapping = controller._resolve_model_route("gpt_text")

        self.assertIsNone(provider)
        self.assertFalse(is_codex)
        self.assertFalse(is_claude)
        self.assertTrue(is_mapping)
        self.assertEqual([], self.provider_manager.lookup_ids)

    def _build_controller(
        self,
        outcomes: dict[str, Any],
        calls: list[str],
    ) -> ProxyController:
        self.provider_manager.model_ids = ("alpha/fast", "gpt_text", "claude_text")
        self.provider_manager.providers = {"alpha/fast": SimpleNamespace(name="alpha")}
        self.codex_service.runtime_ids = ("gpt_text",)
        self.claude_service.runtime_ids = ("claude_text",)
        controller = object.__new__(ProxyController)
        controller._logger = FakeLogger()
        controller._provider_manager = self.provider_manager
        controller._model_mapping_service = self.service
        controller._proxy_service = FakeMappedProxyService(outcomes, calls)
        controller._codex_proxy_service = FakeMappedOAuthProxyService(("gpt_text",), outcomes, calls)
        controller._claude_proxy_service = FakeMappedOAuthProxyService(("claude_text",), outcomes, calls)
        return controller

    def _create_cross_type_mapping(self) -> None:
        self.service.create_mapping(
            {
                "id": "public_model",
                "targets": [
                    {"model_id": "alpha/fast", "priority": 30},
                    {"model_id": "gpt_text", "priority": 20},
                    {"model_id": "claude_text", "priority": 10},
                ],
            }
        )

    def _proxy(self, controller: ProxyController, completed: list[dict[str, Any]]) -> Response:
        response, status_code, failure = controller._proxy_model_mapping_request(
            mapping_id="public_model",
            request_data={"model": "public_model", "messages": []},
            request_headers={},
            on_complete=lambda meta: completed.append(meta),
            forward_stream_usage=False,
            resolved_target_format="openai_chat",
            trace_id="trace",
            route_name="chat_completions",
            client_ip="127.0.0.1",
        )
        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        assert response is not None
        return response

    def test_cross_type_failover_records_429_and_redirect_then_uses_claude(self) -> None:
        calls: list[str] = []
        completed: list[dict[str, Any]] = []
        outcomes = {
            "alpha/fast": (Response("limited", status=429, headers={"Retry-After": "120"}), 429, None),
            "gpt_text": (
                None,
                502,
                ProxyErrorInfo(
                    message="redirect",
                    status_code=502,
                    details={"upstream_status": 302},
                ),
            ),
            "claude_text": (Response("ok", status=200), 200, None),
        }
        controller = self._build_controller(outcomes, calls)
        self._create_cross_type_mapping()

        response = self._proxy(controller, completed)
        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        targets = {target["model_id"]: target for target in mapping["targets"]}

        self.assertEqual(["alpha/fast", "gpt_text", "claude_text"], calls)
        self.assertEqual(b"ok", response.get_data())
        self.assertEqual("cooldown", targets["alpha/fast"]["status"])
        self.assertEqual(302, targets["gpt_text"]["last_status_code"])
        self.assertEqual("auto_disabled", targets["gpt_text"]["status"])
        self.assertEqual("claude_text", mapping["current_target_model_id"])
        self.assertEqual("claude_text", completed[0]["response_model"])

    def test_provider_403_disables_target_records_response_message_and_switches(self) -> None:
        calls: list[str] = []
        completed: list[dict[str, Any]] = []
        outcomes = {
            "alpha/fast": (
                Response(
                    json.dumps({"error": {"message": "API key has no model access"}}),
                    status=403,
                    content_type="application/json",
                ),
                403,
                None,
            ),
            "gpt_text": (Response("ok", status=200), 200, None),
            "claude_text": (Response("unexpected", status=200), 200, None),
        }
        controller = self._build_controller(outcomes, calls)
        self._create_cross_type_mapping()

        response = self._proxy(controller, completed)
        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        targets = {target["model_id"]: target for target in mapping["targets"]}

        self.assertEqual(["alpha/fast", "gpt_text"], calls)
        self.assertEqual(b"ok", response.get_data())
        self.assertEqual("auto_disabled", targets["alpha/fast"]["status"])
        self.assertEqual(403, targets["alpha/fast"]["last_status_code"])
        self.assertEqual("API key has no model access", targets["alpha/fast"]["last_error_message"])
        self.assertEqual("gpt_text", mapping["current_target_model_id"])

    def test_image_mapping_uses_codex_image_target(self) -> None:
        calls: list[str] = []
        completed: list[dict[str, Any]] = []
        self.service.create_mapping(
            {
                "id": "gpt-image-2",
                "targets": [{"model_id": "gpt_image"}],
            }
        )
        controller = object.__new__(ProxyController)
        controller._logger = FakeLogger()
        controller._model_mapping_service = self.service
        controller._codex_proxy_service = FakeMappedImageProxyService(
            ("gpt_image",),
            {"gpt_image": (Response("ok", status=200), 200, None)},
            calls,
        )

        response, status_code, failure = controller._proxy_model_mapping_image_request(
            mapping_id="gpt-image-2",
            request_data={"model": "gpt-image-2", "prompt": "draw"},
            request_headers={},
            action="generate",
            on_complete=completed.append,
            trace_id="trace",
            route_name="images_generations",
            client_ip="127.0.0.1",
        )

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        assert response is not None
        self.assertEqual(b"ok", response.get_data())
        self.assertEqual(["gpt_image"], calls)
        self.assertEqual("gpt_image", completed[0]["response_model"])

    def test_successful_target_does_not_switch(self) -> None:
        calls: list[str] = []
        completed: list[dict[str, Any]] = []
        outcomes = {
            "alpha/fast": (Response("ok", status=200), 200, None),
            "gpt_text": (Response("unexpected", status=200), 200, None),
            "claude_text": (Response("unexpected", status=200), 200, None),
        }
        controller = self._build_controller(outcomes, calls)
        self._create_cross_type_mapping()

        self._proxy(controller, completed)

        self.assertEqual(["alpha/fast"], calls)
        self.assertEqual("alpha/fast", completed[0]["response_model"])

    def test_target_exception_disables_and_switches(self) -> None:
        calls: list[str] = []
        completed: list[dict[str, Any]] = []
        outcomes = {
            "alpha/fast": RuntimeError("provider failed"),
            "gpt_text": (Response("ok", status=200), 200, None),
            "claude_text": (Response("unexpected", status=200), 200, None),
        }
        controller = self._build_controller(outcomes, calls)
        self._create_cross_type_mapping()

        self._proxy(controller, completed)
        mapping = self.service.get_mapping("public_model")
        assert mapping is not None

        self.assertEqual(["alpha/fast", "gpt_text"], calls)
        self.assertEqual("auto_disabled", mapping["targets"][0]["status"])

    def test_all_manually_disabled_targets_use_highest_priority_once_per_request(self) -> None:
        calls: list[str] = []
        self._create_cross_type_mapping()
        mapping = self.service.get_mapping("public_model")
        assert mapping is not None
        self.service.update_mapping(
            "public_model",
            {
                "id": "public_model",
                "targets": [
                    {"model_id": target["model_id"], "priority": target["priority"], "enabled": False}
                    for target in mapping["targets"]
                ],
            },
        )
        outcomes = {
            "alpha/fast": (Response("failed", status=500), 500, None),
            "gpt_text": (Response("unexpected", status=200), 200, None),
            "claude_text": (Response("unexpected", status=200), 200, None),
        }
        controller = self._build_controller(outcomes, calls)

        response, status_code, failure = controller._proxy_model_mapping_request(
            mapping_id="public_model",
            request_data={"model": "public_model", "messages": []},
            request_headers={},
            on_complete=lambda _meta: None,
            forward_stream_usage=False,
            resolved_target_format="openai_chat",
            trace_id="trace",
            route_name="chat_completions",
            client_ip="127.0.0.1",
        )

        self.assertIsNone(response)
        self.assertEqual(500, status_code)
        self.assertIsNotNone(failure)
        self.assertEqual(["alpha/fast"], calls)

    def test_stream_failure_after_commit_affects_only_next_request(self) -> None:
        calls: list[str] = []
        completed: list[dict[str, Any]] = []

        def committed_stream(kwargs: dict[str, Any]) -> tuple[Response, int, None]:
            def generate():
                yield b"data: partial\n\n"
                kwargs["on_stream_failure"](
                    {
                        "status_code": 502,
                        "error_type": "upstream_stream_error",
                        "error_message": "stream interrupted",
                    }
                )

            return Response(generate(), status=200), 200, None

        outcomes = {
            "alpha/fast": committed_stream,
            "gpt_text": (Response("ok", status=200), 200, None),
            "claude_text": (Response("unexpected", status=200), 200, None),
        }
        controller = self._build_controller(outcomes, calls)
        self._create_cross_type_mapping()

        first_response = self._proxy(controller, completed)
        self.assertEqual(["alpha/fast"], calls)
        self.assertIn(b"partial", first_response.get_data())
        second_response = self._proxy(controller, completed)

        self.assertEqual(["alpha/fast", "gpt_text"], calls)
        self.assertEqual(b"ok", second_response.get_data())
