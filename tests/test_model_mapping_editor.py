import subprocess
import unittest
from pathlib import Path


class ModelMappingEditorTests(unittest.TestCase):
    def test_editor_target_interactions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            ["node", "--test", "tests/model_mapping_editor.test.cjs"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
