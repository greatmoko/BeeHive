import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from wokbee.core.models import ProjectEvent
from wokbee.core.project_store import EVENT_LOG_MAX_CHARS, ProjectStore


class EventLoggingLimitTests(unittest.TestCase):
    def make_store(self, root: Path) -> ProjectStore:
        store = ProjectStore.__new__(ProjectStore)
        store.path_for = lambda _project_id: root  # type: ignore[method-assign]
        return store

    def append_and_read_logged_event(self, event: ProjectEvent) -> dict:
        path = MagicMock()
        path.exists.return_value = False
        with patch("wokbee.core.project_store.ensure_project_layout"), \
             patch("wokbee.core.project_store.events_path", return_value=path), \
             patch("wokbee.core.project_store.safe_write_text") as write:
            self.make_store(Path("unused")).append_event("project", event)
        return json.loads(write.call_args.args[1])

    def test_tool_result_is_full_in_memory_but_capped_in_log(self):
        content = "x" * (EVENT_LOG_MAX_CHARS + 1000)
        event = ProjectEvent(kind="tool", content=content)

        logged = self.append_and_read_logged_event(event)

        self.assertEqual(event.content, content)
        self.assertLessEqual(len(logged["content"]), EVENT_LOG_MAX_CHARS)
        self.assertTrue(logged["meta"]["log_truncated"])
        self.assertEqual(logged["meta"]["original_content_length"], len(content))

    def test_short_event_is_logged_unchanged(self):
        event = ProjectEvent(kind="info", content="保留完整")

        logged = self.append_and_read_logged_event(event)

        self.assertEqual(logged["content"], event.content)
        self.assertNotIn("log_truncated", logged["meta"])


if __name__ == "__main__":
    unittest.main()
