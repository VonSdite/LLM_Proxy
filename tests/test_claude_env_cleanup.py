"""Claude 环境变量延迟删除的合并、并发编辑和恢复回归。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services.agent_config_documents import parse_document

from test_agent_config import AgentConfigFixture, draft, make_service


class ClaudeEnvCleanupTests(AgentConfigFixture):
    def raw_save(self, data):
        snapshot = self.snapshot()
        preview = self.service.preview(
            self.owner,
            "local",
            "claude",
            {
                "versions": {target: file["version"] for target, file in snapshot["files"].items()},
                "raw": {"claude": json.dumps(data) + "\n"},
            },
        )
        return self.service.apply(self.owner, "local", preview["token"])

    def file(self):
        return Path(self.snapshot()["files"]["claude"]["path"])

    def pending(self):
        return self.service.state["claudeEnvCleanup"]

    def sweep(self, now):
        with patch("src.services.agent_config_service.time.time", return_value=now):
            self.service._cleanup_claude_env()

    def stage(self):
        self.raw_save({"env": {"ANTHROPIC_MODEL": "old", "ANTHROPIC_AUTH_TOKEN": "old-secret", "KEEP": "keep"}})
        with patch("src.services.agent_config_service.time.time", return_value=100):
            return self.raw_save({"env": {"KEEP": "keep"}})

    def test_empty_values_are_visible_on_disk_then_removed_without_changing_snapshot_version(self):
        saved = self.stage()
        path = self.file()
        env = parse_document("claude", path.read_text(encoding="utf-8"))["env"]
        self.assertEqual("", env["ANTHROPIC_MODEL"])
        self.assertEqual("", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("env.ANTHROPIC_MODEL", saved["files"]["claude"]["managed"])
        editor = self.service.read_files(self.owner, "local", "claude")["files"][0]
        self.assertNotIn("ANTHROPIC_MODEL", editor["text"])
        self.assertEqual(saved["files"]["claude"]["version"], editor["version"])
        self.sweep(109)
        self.assertIn("ANTHROPIC_MODEL", path.read_text(encoding="utf-8"))
        self.sweep(110)
        self.assertEqual({"KEEP": "keep"}, parse_document("claude", path.read_text(encoding="utf-8"))["env"])
        self.assertFalse(self.pending())
        self.assertEqual(saved["files"]["claude"]["version"], self.snapshot()["files"]["claude"]["version"])

    def test_frequent_saves_coalesce_and_only_latest_deadline_runs(self):
        self.stage()
        with patch("src.services.agent_config_service.gevent.spawn_later") as spawn:
            for now in range(101, 121):
                with patch("src.services.agent_config_service.time.time", return_value=now):
                    self.raw_save({"env": {"KEEP": str(now)}})
                self.assertEqual(1, len(self.pending()))
            spawn.assert_not_called()
        pending = next(iter(self.pending().values()))
        self.assertEqual(130, pending["due"])
        self.assertEqual({"ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN"}, set(pending["keys"]))
        self.sweep(129)
        self.assertEqual(1, len(self.pending()))
        self.sweep(130)
        self.assertFalse(self.pending())
        self.assertEqual({"env": {"KEEP": "120"}}, parse_document("claude", self.file().read_text(encoding="utf-8")))

    def test_noop_form_save_does_not_postpone_cleanup(self):
        self.save()
        changed = draft()
        changed["models"] = {}
        changed["clearFields"] = ["models.ANTHROPIC_MODEL"]
        with patch("src.services.agent_config_service.time.time", return_value=100):
            self.save(data=changed)
        with patch("src.services.agent_config_service.time.time", return_value=105):
            preview = self.preview(data=changed)
            self.assertFalse(preview["hasChanges"])
            self.service.apply(self.owner, "local", preview["token"])
        self.assertEqual(110, next(iter(self.pending().values()))["due"])

    def test_reset_value_is_not_removed_by_old_cleanup(self):
        self.stage()
        with patch("src.services.agent_config_service.time.time", return_value=105):
            self.raw_save({"env": {"ANTHROPIC_MODEL": "new", "KEEP": "keep"}})
        self.assertEqual(["ANTHROPIC_AUTH_TOKEN"], next(iter(self.pending().values()))["keys"])
        self.sweep(115)
        self.assertEqual(
            "new", parse_document("claude", self.file().read_text(encoding="utf-8"))["env"]["ANTHROPIC_MODEL"]
        )
        self.assertFalse(self.pending())

    def test_external_edits_and_untracked_empty_values_are_preserved(self):
        self.stage()
        path = self.file()
        path.write_text(
            '// keep comment\n{"env":{"ANTHROPIC_MODEL":"external","ANTHROPIC_AUTH_TOKEN":"",'
            '"ANTHROPIC_API_KEY":"","UNRELATED":""},"custom":true}\n',
            encoding="utf-8",
            newline="\n",
        )
        self.sweep(110)
        text = path.read_text(encoding="utf-8")
        self.assertIn("// keep comment", text)
        self.assertEqual(
            {"env": {"ANTHROPIC_MODEL": "external", "ANTHROPIC_API_KEY": "", "UNRELATED": ""}, "custom": True},
            parse_document("claude", text),
        )

    def test_cleanup_does_not_invalidate_prepared_save(self):
        self.stage()
        preview = self.preview(data=draft())
        self.sweep(110)
        self.service.apply(self.owner, "local", preview["token"])
        self.assertEqual("main/claude-sonnet-4-6", self.snapshot()["files"]["claude"]["managed"]["env.ANTHROPIC_MODEL"])

    def test_failed_state_commit_rolls_back_file_and_keeps_old_cleanup(self):
        self.stage()
        path = self.file()
        before = path.read_bytes()
        pending = json.dumps(self.pending(), sort_keys=True)
        with patch.object(self.service.repository, "save", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.raw_save({"env": {"ANTHROPIC_MODEL": "new"}})
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(pending, json.dumps(self.pending(), sort_keys=True))
        self.sweep(110)
        self.assertFalse(self.pending())

    def test_successful_save_recovers_cleanup_after_restart(self):
        self.stage()
        self.service.close()
        self.service = make_service(self.directory)
        self.addCleanup(self.service.close)
        self.assertEqual(1, len(self.pending()))
        self.sweep(110)
        self.assertFalse(self.pending())
        self.assertNotIn("ANTHROPIC_MODEL", self.file().read_text(encoding="utf-8"))

    def test_missing_file_is_not_recreated_and_invalid_file_can_be_opened(self):
        self.stage()
        path = self.file()
        path.write_text("{invalid", encoding="utf-8", newline="\n")
        self.assertTrue(self.snapshot()["files"]["claude"]["parseError"])
        self.assertEqual("{invalid", self.service.read_files(self.owner, "local", "claude")["files"][0]["text"])
        self.sweep(110)
        self.assertEqual(170, next(iter(self.pending().values()))["due"])
        path.unlink()
        self.sweep(170)
        self.assertFalse(path.exists())
        self.assertFalse(self.pending())

    def test_background_worker_is_singleton(self):
        with patch("src.services.agent_config_service.gevent.spawn") as spawn:
            self.service.start_cleanup()
            self.service.start_cleanup()
            spawn.assert_called_once()

    def test_remote_cleanup_waits_for_reconnection_and_checks_machine_identity(self):
        machine = self.service.save_machine(self.owner, {"host": "example.com", "user": "test"})
        machine_id, path = machine["id"], "/home/test/.claude/settings.json"
        key = self.service._ownership_key(machine_id, path)
        entry = {"machine": machine_id, "path": path, "keys": ["ANTHROPIC_MODEL"], "due": 100}
        self.pending()[key] = entry
        self.sweep(110)
        self.assertIn(key, self.pending())
        contents = {path: b'{"env":{"ANTHROPIC_MODEL":"","KEEP":"keep"}}\n'}
        files = SimpleNamespace(
            alive=lambda: True,
            close=lambda: None,
            read=lambda path: contents.get(path),
            mode=lambda path: 0o600,
            write=lambda path, data, mode: contents.__setitem__(path, data),
        )
        self.service.sessions[(self.owner, machine_id)] = {"files": files, "touched": time.monotonic()}
        self.sweep(110)
        self.assertNotIn(key, self.pending())
        self.assertEqual({"env": {"KEEP": "keep"}}, parse_document("claude", contents[path].decode()))
        self.pending()[key] = entry
        contents[path] = b'{"env":{"ANTHROPIC_MODEL":""}}\n'
        self.service.state["machines"][machine_id]["host"] = "different.example.com"
        self.sweep(120)
        self.assertEqual(b'{"env":{"ANTHROPIC_MODEL":""}}\n', contents[path])
        self.assertNotIn(key, self.pending())

    def test_external_write_during_cleanup_defers_without_overwrite(self):
        self.stage()
        path = self.file()
        before = path.read_bytes()
        external = b'{"env":{"ANTHROPIC_MODEL":"external"}}\n'
        with patch.object(self.service.local, "read", side_effect=[before, external]):
            with patch.object(self.service.local, "write") as write:
                self.sweep(110)
                write.assert_not_called()
        self.assertEqual(1, len(self.pending()))

    def test_cleanup_records_do_not_contain_credentials(self):
        self.stage()
        state = self.service.repository.path.read_text(encoding="utf-8")
        self.assertNotIn("old-secret", state)
        self.assertIn("ANTHROPIC_AUTH_TOKEN", state)
