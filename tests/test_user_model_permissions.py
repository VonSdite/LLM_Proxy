import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.repositories import UserRepository
from src.services import UserService
from src.utils.database import create_connection_factory


class DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class FakeConfigManager:
    def __init__(self, payload):
        self.payload = payload

    def get_raw_config(self):
        return deepcopy(self.payload)


class UserModelPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tempdir.name) / "requests.db"
        self._get_connection = create_connection_factory(self._db_path)
        self._config = {
            "providers": [
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "api_key": "sk-demo",
                    "model_list": ["gpt-4.1", "gpt-4.1-mini"],
                }
            ]
        }
        self._ctx = SimpleNamespace(
            logger=DummyLogger(),
            config_manager=FakeConfigManager(self._config),
        )
        self._repository = UserRepository(self._get_connection)
        self._service = UserService(self._ctx, self._repository)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_user_repository_defaults_model_permissions_to_wildcard(self) -> None:
        user_id = self._repository.create("alice", "127.0.0.1")

        user = self._repository.get_by_id(user_id)

        self.assertEqual("*", user["model_permissions"])

    def test_update_user_model_permissions_supports_explicit_models(self) -> None:
        user_id = self._repository.create("alice", "127.0.0.1")

        updated = self._service.update_user(
            user_id,
            model_permissions_provided=True,
            model_permissions=["demo/gpt-4.1-mini"],
        )
        user = self._service.get_user_by_id(user_id)

        self.assertTrue(updated)
        self.assertEqual(["demo/gpt-4.1-mini"], user["model_permissions"])
        self.assertFalse(self._service.can_user_access_model(user, "demo/gpt-4.1"))
        self.assertTrue(self._service.can_user_access_model(user, "demo/gpt-4.1-mini"))

    def test_sync_model_permissions_prunes_deleted_models_from_explicit_users(self) -> None:
        user_id = self._repository.create("alice", "127.0.0.1")
        self._service.update_user(
            user_id,
            model_permissions_provided=True,
            model_permissions=["demo/gpt-4.1", "demo/gpt-4.1-mini"],
        )

        self._config["providers"][0]["model_list"] = ["gpt-4.1"]
        updated_count = self._service.sync_model_permissions()
        user = self._service.get_user_by_id(user_id)

        self.assertEqual(1, updated_count)
        self.assertEqual(["demo/gpt-4.1"], user["model_permissions"])

    def test_batch_update_model_permissions_updates_multiple_users(self) -> None:
        alice_id = self._repository.create("alice", "127.0.0.1")
        bob_id = self._repository.create("bob", "127.0.0.2")

        result = self._service.batch_update_model_permissions(
            [alice_id, bob_id],
            ["demo/gpt-4.1-mini"],
        )

        alice = self._service.get_user_by_id(alice_id)
        bob = self._service.get_user_by_id(bob_id)
        self.assertEqual(2, result["count"])
        self.assertEqual(["demo/gpt-4.1-mini"], result["model_permissions"])
        self.assertEqual(["demo/gpt-4.1-mini"], alice["model_permissions"])
        self.assertEqual(["demo/gpt-4.1-mini"], bob["model_permissions"])


if __name__ == "__main__":
    unittest.main()
