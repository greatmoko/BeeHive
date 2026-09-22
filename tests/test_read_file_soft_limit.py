import unittest
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage

from wokbee.engine.file_tools import (
    DESIGN_READ_FILE_SOFT_LIMIT,
    READ_FILE_SOFT_LIMIT,
    _soft_limit_read_result,
    wrap_read_file_soft_limit,
)


def _numbered_content(line_count: int = 3000) -> str:
    rows = [f"{line:4d}  {'x' * 20}" for line in range(1, line_count + 1)]
    return "\n".join(rows) + f"\n\n[Read {line_count} lines (lines 1-{line_count} of {line_count} total).]"


class _FakeTool:
    def __init__(self, name, func, coroutine=None):
        self.name = name
        self.func = func
        self.coroutine = coroutine

    def model_copy(self, *, update):
        values = {
            "name": self.name,
            "func": self.func,
            "coroutine": self.coroutine,
        }
        values.update(update)
        return _FakeTool(**values)


class ReadFileSoftLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_full_read_is_clipped_at_complete_line_with_next_page_hint(self):
        result = ToolMessage(content=_numbered_content(), tool_call_id="call")

        clipped = _soft_limit_read_result(
            result,
            max_chars=READ_FILE_SOFT_LIMIT,
            kwargs={"offset": 0, "limit": 100_000},
        )

        self.assertIsNot(clipped, result)
        self.assertIn("文件共 3000 行", clipped.content)
        self.assertRegex(clipped.content, r"已显示前 \d+ 行；继续读取请用 read_file_range\(offset=\d+\)。")
        body = clipped.content.split("\n\n文件共", 1)[0]
        self.assertRegex(body.splitlines()[-1], r"^\s*\d+\s{2}x{20}$")

    def test_range_read_and_small_limit_are_unchanged(self):
        result = ToolMessage(content=_numbered_content(), tool_call_id="call")

        for kwargs in ({"offset": 10, "limit": 100_000}, {"offset": 0, "limit": 500}):
            self.assertIs(
                _soft_limit_read_result(result, max_chars=READ_FILE_SOFT_LIMIT, kwargs=kwargs),
                result,
            )

    def test_design_mode_allows_larger_prd_read_window(self):
        result = ToolMessage(content="x" * 40_000, tool_call_id="call")

        self.assertIs(
            _soft_limit_read_result(
                result,
                max_chars=DESIGN_READ_FILE_SOFT_LIMIT,
                kwargs={"offset": 0, "limit": 100_000},
            ),
            result,
        )

    async def test_wrapper_changes_only_read_file_and_supports_async_tool(self):
        content = _numbered_content()

        def read_file(*_args, **_kwargs):
            return ToolMessage(content=content, tool_call_id="call")

        async def async_read_file(*_args, **_kwargs):
            return ToolMessage(content=content, tool_call_id="call")

        def other(*_args, **_kwargs):
            return ToolMessage(content=content, tool_call_id="call")

        middleware = SimpleNamespace(
            tools=[
                _FakeTool("read_file", read_file, async_read_file),
                _FakeTool("grep", other),
            ]
        )
        wrap_read_file_soft_limit(middleware, max_chars=DESIGN_READ_FILE_SOFT_LIMIT)

        wrapped = middleware.tools[0]
        sync_result = wrapped.func("file", None, 0, 100_000)
        async_result = await wrapped.coroutine("file", None, 0, 100_000)
        self.assertIn("read_file_range(offset=", sync_result.content)
        self.assertIn("read_file_range(offset=", async_result.content)
        self.assertEqual(middleware.tools[1].func("file", None, 0, 100_000).content, content)


if __name__ == "__main__":
    unittest.main()
