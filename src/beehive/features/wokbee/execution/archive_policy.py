"""WokBee 项目归档路径与命令禁用策略。"""
from __future__ import annotations
import re
from pathlib import Path

_DENY_MSG = (
    "禁止访问 archives/：归档文档与归档数据不能作为当前运行的数据来源。"
    "可用：uploads/、output/、memory/（可读写；events.jsonl 由系统维护，不改写）"
    "与项目根目录（临时脚本与中间材料）；.project/ 由系统维护。"
)

_ARCHIVES_RE = re.compile(r"(^|[/\\])archives([/\\]|$)", re.IGNORECASE)
# 守卫用：盘符绝对 / UNC / 根化的 /… / drive-relative（C:），决定是否按绝对路径解析
_ABS_RE = re.compile(r"^[a-zA-Z]:[/\\]|^\\\\|^/|^[a-zA-Z]:$")



def _looks_abs(p: str) -> bool:
    return bool(_ABS_RE.match(p))

def path_touches_archives(path: str | None, project_root=None) -> bool:
    """项目根下是否触及 archives/。

    归档守卫的边界是**本项目根目录下的 archives 目录**（含其整棵子树），不是任何名子里
    含 archives 的路径：uploads/archives、/ext/archives/… 这些「别处恰好叫 archives」
    的目录放行——若按裸组件名拦截，恰好访问一个叫 archives 的已授权外部目录，或项目内
    无关子目录里的同名文件夹，都会被误杀（对外部目录尤其致命）。
    """
    if not path:
        return False
    if project_root is not None:
        try:
            root = Path(project_root).resolve()
        except (OSError, ValueError):
            root = None
        if root is not None:
            try:
                p = str(path)
                if _looks_abs(p):
                    target = Path(p).resolve()
                else:
                    target = (root / p).resolve()
                archive_root = root / "archives"
                if target == archive_root or archive_root in target.parents:
                    return True
                return False
            except (OSError, ValueError):
                pass  # 解析失败 → 退回保守检查
    # 无 project_root 或解析失败：退化为「多级路径含 archives 组件」的保守判断
    norm = str(path).replace("\\", "/").strip()
    return _ARCHIVES_RE.search(norm) is not None

def _iter_command_tokens(command: str) -> list[str]:
    """粗切 shell 命令为 token：引号内容作整体（含空格路径），其余按空白切。"""
    tokens: list[str] = []
    for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'|`([^`]*)`', command):
        tokens.append(m.group(1) or m.group(2) or m.group(3) or "")
    rest = re.sub(r'"[^"]*"|\'[^\']*\'|`[^`]*`', " ", command)
    rest = re.sub(r"[<>|&;]", " ", rest)
    for tok in rest.split():
        tokens.append(tok)
    return tokens

def _token_touches_archives(token: str) -> bool:
    """单个 token 是否指向项目 archives（路径解析/dir 视角），而非仅提到该字样。"""
    t = token
    # 去命令替换/变量引用（把 $(…) 内容并过来，尽量挡住 archiv$( 'e')s 类拼接）
    t = re.sub(r"\$\{[^}]*\}|\$\([^)]*\)", "", t)
    t = t.replace("`", "").replace("'", "").replace('"', "")
    t = t.replace("\\", "/")
    has_glob = any(c in t for c in "*?[")
    # 展开单层 ./ 与 ../，归一化后再看组件
    parts = t.split("/")
    collapsed: list[str] = []
    for x in parts:
        if x == "..":
            if collapsed:
                collapsed.pop()
        elif x == ".":
            continue
        else:
            collapsed.append(x)
    norm = "/".join(collapsed).lstrip("/")
    if has_glob:
        low = t.lower()
        # glob 展开后可能就是 archives（archiv*、*chives）
        if "chives" in low or "archiv" in low:
            return True
    # 仅当「archives」是独立路径组件才算命中；my-archives-backup、archive_summary.py 放行
    return _ARCHIVES_RE.search(norm) is not None

def command_touches_archives(command: str | None) -> bool:
    """shell 命令是否触及项目 archives。

    用「路径解析」替代硬子串匹配：处理引号（含空格路径）、`..`/glob，只拦指向 archives
    目录的 token，从而放行 `dir my-archives-backup`、`python archive_summary.py` 这类
    仅提到 archives 字样的正常命令。（命令替换/变量拼接属 shell 语义，此处尽力解析，非
    完美沙箱。）
    """
    if not command or not isinstance(command, str):
        return False
    for tok in _iter_command_tokens(command):
        if _token_touches_archives(tok):
            return True
    return False
