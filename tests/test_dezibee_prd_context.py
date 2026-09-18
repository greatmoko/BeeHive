from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dezibee.core.models import Requirement
from dezibee.core.services import build_design_prompt
from wokbee.engine.file_tools import build_file_tools
from wokbee.engine.prompt import static_system_prompt
from wokbee.engine.runner import RunRequest


class _ReadOnlyFixtureBackend:
    def __init__(self, content: str):
        self.content = content

    def read(self, _path, offset=0, limit=500):
        lines = self.content.splitlines(keepends=True)
        end = min(offset + limit, len(lines))
        return SimpleNamespace(
            error=None,
            file_data={"content": "".join(lines[offset:end])},
            start_line=offset + 1,
            total_lines=len(lines),
            next_offset=end if end < len(lines) else None,
        )


class DeziBeePrdContextTests(unittest.TestCase):
    def test_read_file_range_keeps_absolute_line_numbers(self):
        backend = _ReadOnlyFixtureBackend("one\ntwo\nthree\n")
        read = next(t for t in build_file_tools(backend=backend) if t.name == "read_file_range")
        result = read.invoke({"file_path": "demo/index.html", "offset": 1, "limit": 2})
        self.assertIn("L002 | two", result)
        self.assertIn("L003 | three", result)

    def test_design_prompts_define_fresh_prd_editing(self):
        req = Requirement(id="REQ-TEST", title="测试需求")
        prompt = build_design_prompt(req) + "\n" + static_system_prompt(mode="design")
        for phrase in (
            "任何 PRD 修改前必须先读取",
            "全文读取 PRD",
            "二次编写",
            "不得擅自新增需求",
            "PRD-only",
        ):
            self.assertIn(phrase, prompt)

    def test_run_request_supports_optional_chat_thread(self):
        request = RunRequest(
            project=None,
            project_root=Path("."),
            user_message="修改 PRD",
            resolved=None,
            approval=None,
            chat_thread_id="conv_test",
        )
        self.assertEqual(request.chat_thread_id, "conv_test")


if __name__ == "__main__":
    unittest.main()
