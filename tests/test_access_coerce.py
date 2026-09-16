from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deepagents.backends.protocol import EditResult, ReadResult

from wokbee.engine.access_coerce import AccessCoerceBackend


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


if __name__ == "__main__":
    unittest.main()
