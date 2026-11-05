"""Agent 页面异步交互回归。"""

import subprocess
import unittest
from pathlib import Path


class AgentFrontendTests(unittest.TestCase):
    def test_real_frontend_interactions(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "tests/agents_editor.test.cjs"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
