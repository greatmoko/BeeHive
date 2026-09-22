import unittest
from pathlib import Path


class RunContextPrefixTests(unittest.TestCase):
    def test_run_agent_assembly_does_not_add_wall_clock_to_context(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "wokbee"
            / "engine"
            / "runner_agent_assembly.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("_now", source)
        self.assertNotIn("点击运行", source)


if __name__ == "__main__":
    unittest.main()
