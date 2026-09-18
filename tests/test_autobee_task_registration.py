from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.engine.autobee_tools import build_autobee_tools


class AutoBeeTaskRegistrationTests(unittest.TestCase):
    def test_reports_scheduler_registration_failure_after_persisting_task(self):
        store = MagicMock()
        store.create.return_value = SimpleNamespace(id="task_1", name="每日提醒")
        scheduler = MagicMock()
        scheduler.add_or_update.side_effect = RuntimeError("scheduler unavailable")

        with (
            patch("wokbee.engine.autobee_tools.AutoBeeStore", return_value=store),
            patch("wokbee.engine.autobee_tools.ProjectStore"),
            patch("wokbee.engine.autobee_tools.get_global_scheduler", return_value=scheduler),
        ):
            tools = build_autobee_tools()
            create = next(tool for tool in tools if tool.name == "create_scheduled_task")
            result = create.invoke(
                {
                    "name": "每日提醒",
                    "schedule": "0 9 * * *",
                    "content": "早上好",
                }
            )

        store.create.assert_called_once()
        scheduler.add_or_update.assert_called_once_with(store.create.return_value)
        self.assertIn("任务已保存", result)
        self.assertIn("未能注册", result)


if __name__ == "__main__":
    unittest.main()
