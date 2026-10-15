"""通道面板卡片、样式与二维码显示工具。"""
from __future__ import annotations
import time as _time
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import QVBoxLayout, QLabel, QFrame
from beehive.common.ui.theme import Theme
def _render_qr(url: str, size: int = 260) -> QPixmap | None:
    """把字符串画成二维码位图（用 qrcode 纯 python 矩阵，不依赖 Pillow）。

    `get_matrix()` 已含 quiet zone（border），故整幅直接画；长授权 URL 会自动升版本。
    """
    try:
        import qrcode
        qr = qrcode.QRCode(border=2, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception:
        return None
    n = len(matrix)
    scale = max(1, size // n)
    pix = QPixmap(n * scale, n * scale)
    pix.fill(QColor("white"))
    painter = QPainter(pix)
    painter.setBrush(QColor("black"))
    for r, row in enumerate(matrix):
        for c, cell in enumerate(row):
            if cell:
                painter.drawRect(c * scale, r * scale, scale, scale)
    painter.end()
    return pix

def _card_frame(theme: Theme, caption: str) -> tuple[QFrame, QVBoxLayout]:
    """返回 (frame, layout)：调用方用返回的 layout 往里加内容，不要另建新 layout，
    否则会触发 QLayout "already has a layout" 且内容不渲染。"""
    c = theme.colors
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame {{ background: {c['card_bg']}; border: 1px solid {c['border_light']}; border-radius: 8px; }}"
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    if caption:
        lab = QLabel(caption)
        lab.setStyleSheet(
            f"color: {c['text']}; font-size: 14px; font-weight: bold;"
            "background: transparent; border: none;"
        )
        lay.addWidget(lab)
    return frame, lay

def time_now() -> float:
    return _time.monotonic()

def fmt_duration(seconds: int) -> str:
    seconds = max(0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def _progressbar(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QProgressBar {{ background: {c['border_light']}; border: none; border-radius: 4px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
    """
