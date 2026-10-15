"""读取随包发布的控件图标，安装目录保持只读。"""

from pathlib import Path

_RES = Path(__file__).resolve().parent.parent / "resources"


def _arrow_url() -> str:
    return (_RES / "combo_down.png").as_uri()


def _check_url() -> str:
    return (_RES / "checkbox_check.png").as_uri()
