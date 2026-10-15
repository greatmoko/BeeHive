"""原型文档完整性与 JavaScript 对象定位。"""
from __future__ import annotations
import re
_JS_PAIRS = {"{": "}", "[": "]"}

def validate_workbench_document(content: str) -> None:
    """拒绝截断的原型文档；此处只检查完整性，不做完整 JS 语法校验。"""
    from html.parser import HTMLParser

    class Document(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.ends = set()
            self.in_script = False
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.in_script = True

        def handle_endtag(self, tag):
            self.ends.add(tag)
            if tag == "script":
                self.in_script = False

        def handle_data(self, data):
            if self.in_script:
                self.scripts.append(data)

    document = Document()
    document.feed(content)
    document.close()
    if document.in_script or not {"body", "html"} <= document.ends:
        raise ValueError("原型 HTML 未完整闭合，拒绝覆盖。请补齐 script/body/html 后重新提交。")
    for script in document.scripts:
        match = re.search(r"\bWORKBENCH_DATA\s*=\s*(?:window\.WORKBENCH_DATA\s*=\s*)?\{", script)
        if match and _match_bracket(script, match.end() - 1) is not None:
            return
    raise ValueError("WORKBENCH_DATA 缺失或括号/字符串未闭合，拒绝覆盖原型。")

def _skip_js_string(text: str, i: int) -> int:
    """i 位于引号（' " `）处；返回字符串结束后的下标。模板串会跳过 ${...}。"""
    quote = text[i]
    n = len(text)
    i += 1
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        if quote == "`" and c == "$" and i + 1 < n and text[i + 1] == "{":
            j = _match_bracket(text, i + 1)
            i = n if j is None else j + 1
            continue
        i += 1
    return n

def _match_bracket(text: str, open_idx: int) -> int | None:
    """open_idx 指向 '{' 或 '['；返回配对闭合下标（跳过字符串与注释）。"""
    stack = [_JS_PAIRS[text[open_idx]]]
    i = open_idx + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            i = _skip_js_string(text, i)
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i + 2)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in _JS_PAIRS:
            stack.append(_JS_PAIRS[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    return None

def _find_toplevel_key(text: str, obj_open: int, obj_close: int, key: str):
    """在对象字面量内找顶层 key，返回 (key起始, 冒号起始, 值起始)；找不到返回 None。"""
    i = obj_open + 1
    depth = 0
    klen = len(key)
    colon_re = re.compile(r"\s*:")
    while i < obj_close:
        c = text[i]
        if c in "\"'`":
            i = _skip_js_string(text, i)
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i + 2)
            i = len(text) if j < 0 else j + 1
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        if c in _JS_PAIRS:
            depth += 1
            i += 1
            continue
        if c in "}]":
            depth -= 1
            i += 1
            continue
        if depth == 0 and text.startswith(key, i):
            prev = text[i - 1] if i > 0 else ""
            m = colon_re.match(text, i + klen)
            if not (prev.isalnum() or prev in "_$") and m:
                v = m.end()
                while v < len(text) and text[v] in " \t\r\n":
                    v += 1
                return i, m.start(), v
        i += 1
    return None
