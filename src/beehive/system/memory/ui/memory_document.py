"""全局记忆文档标题解析和对话框表面样式。"""
from beehive.common.ui.theme import COLORS
from beehive.system.memory.defaults import MODULES


def _apply_memory_dialog_surface(dialog, colors):
    background = colors.get("card_bg", COLORS["card_bg"])
    text = colors.get("text", COLORS["text"])
    dialog.setStyleSheet(
        f"QDialog {{ background-color: {background}; color: {text}; }}"
        f" QLabel {{ color: {text}; background: transparent; }}"
    )


def _parse_global_memory_text(text):
    """只按模块标题拆分文档，保留模块内部的空行。"""
    parts = {name: [] for name in MODULES}
    current = None
    headings = {f"【{name}】": name for name in MODULES}
    for line in str(text or "").splitlines():
        current = headings.get(line.strip(), current)
        if line.strip() in headings:
            continue
        if current is not None:
            parts[current].append(line)
    return {name: "\n".join(parts[name]).strip() for name in MODULES}
