from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deepagents.backends.protocol import EditResult, ExecuteResponse, ReadResult

from wokbee.engine.access_coerce import AccessCoerceBackend
from wokbee.engine.file_tools import build_file_tools


class _Backend:
    def __init__(self, content: str):
        self.content = content

    def read(self, _path: str, *, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(file_data={"content": self.content})

    def edit(
        self, path: str, old_string: str, new_string: str, *, replace_all: bool = False
    ) -> EditResult:
        if old_string not in self.content:
            return EditResult(error=f"String not found in file: {old_string!r}")
        self.content = self.content.replace(old_string, new_string, -1 if replace_all else 1)
        return EditResult(path=path, occurrences=1)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=command, exit_code=0, truncated=False)


class AccessCoerceEditTests(unittest.TestCase):
    def test_retries_a_unique_double_escaped_unicode_anchor(self) -> None:
        backend = _Backend('html: "</li></ul>"')
        result = AccessCoerceBackend(backend).edit(
            "demo/index.html", r"\u003c/li>\u003c/ul>", "END"
        )

        self.assertIsNone(result.error)
        self.assertEqual(backend.content, 'html: "END"')

    def test_does_not_fallback_when_decoded_anchor_is_ambiguous(self) -> None:
        backend = _Backend("</li></ul> and </li></ul>")
        result = AccessCoerceBackend(backend).edit(
            "demo/index.html", r"\u003c/li>\u003c/ul>", "END"
        )

        self.assertIn("String not found", result.error or "")
        self.assertIn("未自动替换", result.error or "")
        self.assertEqual(backend.content, "</li></ul> and </li></ul>")


class FileLocatorTests(unittest.TestCase):
    def test_find_in_file_returns_current_context_and_line_number(self) -> None:
        backend = _Backend("first line\nunique anchor\nlast line")
        tools = {tool.name: tool for tool in build_file_tools(backend=backend)}

        result = tools["find_in_file"].invoke(
            {"file_path": "demo/index.html", "query": "unique anchor"}
        )

        self.assertIn("第 2 行附近", result)
        self.assertIn("unique anchor", result)


class ExecuteFallbackTests(unittest.TestCase):
    def test_blocks_node_line_by_line_file_probe_without_file_tool_failure(self) -> None:
        backend = AccessCoerceBackend(_Backend("content"))

        result = backend.execute(
            'node.exe -e "const fs=require(\'fs\'); fs.readFileSync(\'demo/a.js\').split(\'\\n\')"'
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("read_file_range", result.output)

    def test_allows_one_probe_only_after_a_file_tool_error(self) -> None:
        backend = AccessCoerceBackend(_Backend("content"))
        backend.read(r"C:\\not-authorized\\a.js")
        command = 'node.exe -e "const fs=require(\'fs\'); fs.readFileSync(\'demo/a.js\').split(\'\\n\')"'

        self.assertEqual(backend.execute(command).exit_code, 0)
        repeated = backend.execute(command)

        self.assertEqual(repeated.exit_code, 1)
        self.assertIn("已停止重复", repeated.output)


if __name__ == "__main__":
    unittest.main()
