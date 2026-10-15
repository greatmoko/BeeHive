"""可用终端的系统探测与默认选择。"""
import shutil

TERMINAL_APPS: list[tuple[str, str]] = [
    ("cmd", "cmd"),
    ("powershell", "powershell"),
    ("pwsh", "pwsh"),
    ("wt", "wt"),
]


_DEFAULT_TERMINAL = "cmd"


def detect_terminal_apps() -> list[tuple[str, str]]:
    """探测系统 PATH 中可用的命令行工具，返回 [(key, 显示名)]。

    cmd 是 Windows 系统组件，始终可用；其余按 shutil.which 探测。
    """
    found: list[tuple[str, str]] = []
    for key, exe in TERMINAL_APPS:
        if key == "cmd" or shutil.which(exe):
            found.append((key, exe))
    return found
