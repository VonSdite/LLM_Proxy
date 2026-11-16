"""通过真实用户仓储验证白名单准入与模型权限的组合。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from flask import Flask, Response
from src.application.app_context import AppContext
from src.external import LLMProvider
from src.presentation.proxy_controller import ProxyController
from src.repositories import ApiKeyRepository, UserRepository
from src.services import ApiKeyService, UserService
from src.utils.database import create_connection_factory
from tests.test_proxy_error_handling import (
    FakeClaudeProxyService,
    FakeCodexProxyService,
    FakeLogger,
    FakeLogService,
    FakeProviderManager,
    RecordingProxyService,
)


class RegisteredUserPermissionTests(unittest.TestCase):
    MODEL_IDS = ("demo/m1", "demo/m2", "gpt-5", "claude-sonnet-4-5", "gpt-image-2")
    REQUESTS = (
        ("/v1/chat/completions", "demo/m1"),
        ("/v1/responses", "gpt-5"),
        ("/v1/responses/compact", "gpt-5"),
        ("/v1/messages", "claude-sonnet-4-5"),
        ("/v1/images/generations", "gpt-image-2"),
        ("/v1/images/edits", "gpt-image-2"),
    )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.whitelist_enabled = False
        self.api_key_enabled = False
        app = Flask(__name__)
        config = SimpleNamespace(
            is_chat_whitelist_enabled=lambda: self.whitelist_enabled,
            is_api_key_management_enabled=lambda: self.api_key_enabled,
            is_real_client_ip_enabled=lambda: False,
            get_real_client_ip_header=lambda: "X-Forwarded-For",
        )
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=cast(Any, config),
            root_path=Path(self.tempdir.name),
            flask_app=app,
        )
        connection = create_connection_factory(Path(self.tempdir.name) / "requests.db")
        catalog = cast(Any, SimpleNamespace(list_permission_model_names=lambda: self.MODEL_IDS))
        self.users = UserService(ctx, UserRepository(connection), catalog)
        self.keys = ApiKeyService(ctx, ApiKeyRepository(connection), catalog)
        result = (Response('{"ok":true}', mimetype="application/json"), 200, None)
        self.proxy = RecordingProxyService(result)
        self.codex = FakeCodexProxyService(proxy_result=result, image_proxy_result=result)
        ProxyController(
            ctx,
            self.proxy,
            self.users,
            FakeLogService(),
            FakeProviderManager(LLMProvider(name="demo", api="https://example.test/v1", model_list=("m1", "m2"))),
            codex_proxy_service=self.codex,
            claude_proxy_service=FakeClaudeProxyService(proxy_result=result),
            api_key_service=self.keys,
        )
        self.client = app.test_client()

    def register_user(self, permissions, *, whitelist_access=False):
        user_id = self.users.create_user("registered", "127.0.0.1")
        self.assertIsNotNone(user_id)
        self.set_permissions(user_id, permissions, whitelist_access=whitelist_access)
        return user_id

    def set_permissions(self, user_id, permissions, *, whitelist_access=False):
        self.assertTrue(
            self.users.update_user(
                user_id,
                whitelist_access_enabled=whitelist_access,
                model_permissions_provided=True,
                model_permissions=permissions,
            )
        )

    def post(self, route, model, **kwargs):
        return self.client.post(
            route,
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "input": "hi", "prompt": "draw"},
            **kwargs,
        )

    def models(self, **kwargs):
        response = self.client.get("/v1/models", **kwargs)
        self.assertEqual(200, response.status_code, response.get_json())
        return {item["id"] for item in response.get_json()["data"]}

    def test_registered_user_permissions_apply_to_every_route_with_whitelist_off(self):
        user_id = self.register_user([])
        self.assertEqual(set(), self.models())
        for route, model in self.REQUESTS:
            with self.subTest(route=route):
                self.set_permissions(user_id, [])
                response = self.post(route, model)
                self.assertEqual(403, response.status_code, response.get_json())
                self.assertIn("not allowed to access model", response.get_json()["error"]["message"])
                self.assertIsNone(self.proxy.last_args)
                self.assertIsNone(self.codex.last_image_args)

        # 白名单访问关闭不影响白名单关闭时的准入，权限变更同步失效 IP 缓存。
        self.set_permissions(user_id, list(self.MODEL_IDS))
        self.assertEqual(set(self.MODEL_IDS), self.models())
        for route, model in self.REQUESTS:
            with self.subTest(allowed_route=route):
                response = self.post(route, model)
                self.assertEqual(200, response.status_code, response.get_json())

    def test_unregistered_user_is_unrestricted_only_when_whitelist_is_off(self):
        self.assertEqual(set(self.MODEL_IDS), self.models())
        for route, model in self.REQUESTS:
            with self.subTest(route=route):
                response = self.post(route, model)
                self.assertEqual(200, response.status_code, response.get_json())
        self.whitelist_enabled = True
        self.assertEqual(403, self.client.get("/v1/models").status_code)
        for route, model in self.REQUESTS:
            with self.subTest(whitelist_route=route):
                response = self.post(route, model)
                self.assertEqual(403, response.status_code)
                self.assertIn("not in whitelist", response.get_json()["error"]["message"])

    def test_whitelist_requires_enabled_user_and_still_checks_model_permissions(self):
        user_id = self.register_user("*")
        self.whitelist_enabled = True
        for route, model in self.REQUESTS:
            with self.subTest(route=route):
                self.assertEqual(403, self.post(route, model).status_code)
        self.assertEqual(403, self.client.get("/v1/models").status_code)
        self.set_permissions(user_id, ["demo/m1"], whitelist_access=True)
        self.assertEqual({"demo/m1"}, self.models())
        self.assertEqual(200, self.post("/v1/chat/completions", "demo/m1").status_code)
        response = self.post("/v1/chat/completions", "demo/m2")
        self.assertEqual("model_not_allowed", response.get_json()["error"]["code"])
        self.assertEqual(403, response.status_code)

    def test_api_key_and_registered_user_permissions_intersect_without_whitelist(self):
        user_id = self.register_user(["demo/m1"])
        key = self.keys.create_api_key(name="test", model_permissions=["demo/m2"])
        headers = {"Authorization": f"Bearer {key['api_key']}"}
        self.api_key_enabled = True
        self.assertEqual(set(), self.models(headers=headers))
        response = self.post("/v1/chat/completions", "demo/m1", headers=headers)
        self.assertEqual(403, response.status_code)
        self.assertEqual("api_key_model_not_allowed", response.get_json()["error"]["code"])
        response = self.post("/v1/chat/completions", "demo/m2", headers=headers)
        self.assertEqual(403, response.status_code)
        self.assertEqual("model_not_allowed", response.get_json()["error"]["code"])
        self.set_permissions(user_id, ["demo/m1", "demo/m2"])
        self.assertEqual({"demo/m2"}, self.models(headers=headers))
        self.assertEqual(200, self.post("/v1/chat/completions", "demo/m2", headers=headers).status_code)

    def test_unregistered_ip_still_requires_api_key_and_its_model_permissions(self):
        self.api_key_enabled = True
        self.assertEqual(401, self.client.get("/v1/models").status_code)
        self.assertEqual(401, self.post("/v1/chat/completions", "demo/m1").status_code)
        key = self.keys.create_api_key(name="test", model_permissions=["demo/m1"])
        headers = {"Authorization": f"Bearer {key['api_key']}"}
        self.assertEqual({"demo/m1"}, self.models(headers=headers))
        response = self.post("/v1/chat/completions", "demo/m2", headers=headers)
        self.assertEqual(403, response.status_code)
        self.assertEqual("api_key_model_not_allowed", response.get_json()["error"]["code"])

    def test_register_update_and_delete_refresh_cached_permissions(self):
        self.assertEqual(set(self.MODEL_IDS), self.models())
        user_id = self.register_user(["demo/m1"])
        self.assertEqual({"demo/m1"}, self.models())
        self.set_permissions(user_id, "*")
        self.assertEqual(set(self.MODEL_IDS), self.models())
        self.set_permissions(user_id, [])
        self.assertEqual(set(), self.models())
        self.assertTrue(self.users.delete_user(user_id))
        self.assertEqual(set(self.MODEL_IDS), self.models())
