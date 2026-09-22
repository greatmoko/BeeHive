import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.engine.lesson_storage import LessonStore
from wokbee.engine.runner_sessions import ensure_experience_files


class MemoryMetadataTests(unittest.TestCase):
    def test_memory_exposes_only_latest_experience(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ensure_experience_files(root)
            agents = root / "memory" / "AGENTS.md"
            self.assertFalse(agents.exists())
            store = LessonStore(root)
            self.assertEqual(store.virtual_memory_paths(), [])

            agents.write_text("Existing user notes", encoding="utf-8")
            for stamp in ("20260920_120000", "20260921_120000"):
                (store.experiences_dir / f"exp_{stamp}.md").write_text(
                    "Experience", encoding="utf-8"
                )
            ensure_experience_files(root)
            self.assertEqual(agents.read_text(encoding="utf-8"), "Existing user notes")
            self.assertEqual(
                store.virtual_memory_paths(),
                ["/memory/experiences/exp_20260921_120000.md"],
            )


if __name__ == "__main__":
    unittest.main()
