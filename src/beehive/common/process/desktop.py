"""使用系统默认程序打开明确指定的路径。"""
import os
import subprocess
import sys


def open_path(path):
    """用系统默认方式打开路径（文件→其所在文件夹由调用方决定）。"""
    path = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass
