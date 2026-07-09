from __future__ import annotations

import gc
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.provider_config import ProviderConfigSchema
from src.config.provider_runtime_factory import ProviderRuntimeFactory
from src.hooks import HookContext


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, msg: str, *args: object) -> None:
        rendered = msg % args if args else msg
        self.records.append((level, rendered))

    def info(self, msg: str, *args: object) -> None:
        self._log("info", msg, *args)

    def warning(self, msg: str, *args: object) -> None:
        self._log("warning", msg, *args)

    def error(self, msg: str, *args: object) -> None:
        self._log("error", msg, *args)

    def debug(self, msg: str, *args: object) -> None:
        self._log("debug", msg, *args)


class ProviderRuntimeFactoryTests(unittest.TestCase):
    @staticmethod
    def _hook_context(root_path: Path, logger: FakeLogger) -> HookContext:
        return HookContext(
            retry=0,
            root_path=root_path,
            logger=logger,
            provider_name="demo",
            request_model="demo/demo-model",
            upstream_model="demo-model",
            provider_target_format="openai_chat",
            stream=False,
        )

    def _build_factory(self, root_path: Path, logger: FakeLogger) -> ProviderRuntimeFactory:
        ctx = AppContext(
            logger=logger,
            config_manager=None,
            root_path=root_path,
            flask_app=Flask(__name__),
        )
        return ProviderRuntimeFactory(ctx)

    @staticmethod
    def _provider_config(hook: str) -> ProviderConfigSchema:
        return ProviderConfigSchema.from_mapping(
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "model_list": ["demo-model"],
                "hook": hook,
            }
        )

    def test_loads_relative_hook_and_same_directory_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_dir = root_path / "hooks" / "custom"
            hook_dir.mkdir(parents=True)
            (hook_dir / "helper.py").write_text('VALUE = "loaded"\n', encoding="utf-8")
            (hook_dir / "demo_hook.py").write_text(
                """
from helper import VALUE


class Hook:
    def request_guard(self, ctx, body):
        del ctx
        guarded = dict(body)
        guarded["helper_value"] = VALUE
        return guarded
""".lstrip(),
                encoding="utf-8",
            )
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)

            provider = factory.build_provider_from_schema(self._provider_config("custom/demo_hook.py"))
            hook_context = self._hook_context(root_path, logger)

            self.assertIsNotNone(provider.hook)
            self.assertEqual({"helper_value": "loaded"}, provider.apply_request_guard(hook_context, {}))

    def test_missing_hook_file_loads_after_file_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_dir = root_path / "hooks" / "custom"
            hook_dir.mkdir(parents=True)
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)
            provider = factory.build_provider_from_schema(self._provider_config("custom/demo_hook.py"))
            hook_context = self._hook_context(root_path, logger)

            self.assertEqual({}, provider.apply_request_guard(hook_context, {}))

            (hook_dir / "demo_hook.py").write_text(
                """
class Hook:
    def request_guard(self, ctx, body):
        del ctx
        guarded = dict(body)
        guarded["created_after_first_attempt"] = True
        return guarded
""".lstrip(),
                encoding="utf-8",
            )

            self.assertEqual(
                {"created_after_first_attempt": True},
                provider.apply_request_guard(hook_context, {}),
            )

    def test_loaded_hook_reloads_after_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_dir = root_path / "hooks" / "custom"
            hook_dir.mkdir(parents=True)
            hook_file = hook_dir / "demo_hook.py"
            hook_file.write_text(
                """
class Hook:
    def request_guard(self, ctx, body):
        del ctx
        guarded = dict(body)
        guarded["version"] = "one"
        return guarded
""".lstrip(),
                encoding="utf-8",
            )
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)
            provider = factory.build_provider_from_schema(self._provider_config("custom/demo_hook.py"))
            hook_context = self._hook_context(root_path, logger)

            self.assertEqual({"version": "one"}, provider.apply_request_guard(hook_context, {}))

            hook_file.write_text(
                """
class Hook:
    def request_guard(self, ctx, body):
        del ctx
        guarded = dict(body)
        guarded["version"] = "two-hot-reload"
        return guarded
""".lstrip(),
                encoding="utf-8",
            )

            self.assertEqual({"version": "two-hot-reload"}, provider.apply_request_guard(hook_context, {}))

    def test_loaded_hook_stays_available_after_file_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_dir = root_path / "hooks" / "custom"
            hook_dir.mkdir(parents=True)
            hook_file = hook_dir / "demo_hook.py"
            hook_file.write_text(
                """
class Hook:
    def request_guard(self, ctx, body):
        del ctx
        guarded = dict(body)
        guarded["loaded_before_delete"] = True
        return guarded
""".lstrip(),
                encoding="utf-8",
            )
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)
            provider = factory.build_provider_from_schema(self._provider_config("custom/demo_hook.py"))
            hook_context = self._hook_context(root_path, logger)

            self.assertEqual({"loaded_before_delete": True}, provider.apply_request_guard(hook_context, {}))

            hook_file.unlink()

            self.assertEqual({"loaded_before_delete": True}, provider.apply_request_guard(hook_context, {}))

    def test_hook_cache_uses_weak_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_dir = root_path / "hooks" / "custom"
            hook_dir.mkdir(parents=True)
            (hook_dir / "demo_hook.py").write_text(
                """
class Hook:
    def request_guard(self, ctx, body):
        del ctx
        guarded = dict(body)
        guarded["cached"] = True
        return guarded
""".lstrip(),
                encoding="utf-8",
            )
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)
            provider = factory.build_provider_from_schema(self._provider_config("custom/demo_hook.py"))
            hook_context = self._hook_context(root_path, logger)

            self.assertEqual({"cached": True}, provider.apply_request_guard(hook_context, {}))
            cache_entry = next(iter(factory._hook_cache.values()))  # noqa: SLF001
            self.assertIsNotNone(cache_entry.hook_ref)
            hook_ref = cache_entry.hook_ref

            del provider
            gc.collect()

        self.assertIsNone(hook_ref())

    def test_absolute_hook_path_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_file = root_path / "outside_hook.py"
            hook_file.write_text("class Hook:\n    pass\n", encoding="utf-8")
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)

            provider = factory.build_provider_from_schema(self._provider_config(str(hook_file)))

        self.assertIsNone(provider.hook)
        self.assertIn(
            ("warning", f"Absolute hook path is not allowed: {hook_file}"),
            logger.records,
        )

    def test_hook_path_cannot_escape_hooks_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            hook_file = root_path / "outside_hook.py"
            hook_file.write_text("class Hook:\n    pass\n", encoding="utf-8")
            logger = FakeLogger()
            factory = self._build_factory(root_path, logger)

            provider = factory.build_provider_from_schema(self._provider_config("../outside_hook.py"))

        self.assertIsNone(provider.hook)
        self.assertIn(
            ("warning", "Hook path must stay under hooks directory: ../outside_hook.py"),
            logger.records,
        )


if __name__ == "__main__":
    unittest.main()
