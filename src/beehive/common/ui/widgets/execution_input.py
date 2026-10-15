"""输入区拖拽条与运行/暂停动画按钮。"""
from __future__ import annotations
import random
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget
from beehive.common.ui.theme import Theme

# 运行中：活跃按钮（运行/发送）音频均衡器动效 —— 5 根细竖条自绘，底部对齐，随机跃升 + 指数回落
_RUN_EQ_BARS = 5
_RUN_EQ_BAR_W = 3        # 竖条宽度（细）
_RUN_EQ_GAP = 4          # 竖条间距
_RUN_EQ_PAD_TOP = 6
_RUN_EQ_PAD_BOTTOM = 6
_RUN_EQ_INTERVAL = 70    # 帧间隔（毫秒）
_RUN_EQ_DECAY = 0.82     # 每帧回落系数（真实均衡器风格）
_RUN_EQ_PEAK = 0.35      # 高于该值的随机量才触发跃升


class InputResizeHandle(QWidget):
    """输入框顶部拖拽条：向上拖高、向下拖矮。"""

    drag_delta = Signal(int)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setFixedSize(100, 8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("拖动调整高度")
        self._dragging = False
        self._last_y = 0

    def set_available_width(self, width: int):
        self.setFixedWidth(max(28, int(width) // 3))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self._theme.colors.get("border", "#e5e5e5")))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        y = self.height() // 2
        painter.drawLine(8, y, self.width() - 8, y)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_y = event.globalPosition().toPoint().y()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            y = event.globalPosition().toPoint().y()
            delta = self._last_y - y
            self._last_y = y
            if delta:
                self.drag_delta.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class EqButton(QPushButton):
    """通用启停按钮：空闲显示原文字，运行中自绘均衡器动画并保持可点击（再点一次即暂停）。"""

    def __init__(self, theme: Theme, *, text: str, width: int, bg: str, bg_hover: str, parent=None):
        super().__init__(text, parent)
        self._theme = theme
        self._idle_text = text
        self._spinning = False
        self._levels = [0.0] * _RUN_EQ_BARS
        self._eq_timer = QTimer(self)
        self._eq_timer.setInterval(_RUN_EQ_INTERVAL)
        self._eq_timer.timeout.connect(self._tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width, 34)
        c = theme.colors
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: white;
                border: none; border-radius: 6px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {bg_hover}; }}
            QPushButton:pressed {{ background: {bg}; }}
            QPushButton:disabled {{ background: {c['btn_bg']}; color: {c['text_hint']}; }}
        """)

    def set_spinning(self, spinning: bool):
        self._spinning = spinning
        if spinning:
            self._levels = [0.0] * _RUN_EQ_BARS
            self._eq_timer.start()
            self.setToolTip("点击暂停")
        else:
            self._eq_timer.stop()
            self.setText(self._idle_text)
            self.setToolTip("")
        self.update()

    def _tick(self):
        # 真实均衡器：随机跃升 + 指数回落
        for i in range(_RUN_EQ_BARS):
            peak = random.random()
            if peak > _RUN_EQ_PEAK:
                self._levels[i] = max(self._levels[i], peak)
            self._levels[i] *= _RUN_EQ_DECAY
        self.update()

    def paintEvent(self, event):
        if not self._spinning:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        c = self._theme.colors
        painter.setBrush(QColor(c["input_bg"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 6, 6)
        self._draw_equalizer(painter, rect, c)
        painter.end()

    def _draw_equalizer(self, painter: QPainter, rect, c: dict):
        inner_h = (rect.height() - _RUN_EQ_PAD_TOP - _RUN_EQ_PAD_BOTTOM) * 0.8
        base_y = rect.y() + rect.height() - _RUN_EQ_PAD_BOTTOM
        total_w = _RUN_EQ_BARS * _RUN_EQ_BAR_W + (_RUN_EQ_BARS - 1) * _RUN_EQ_GAP
        start_x = rect.x() + (rect.width() - total_w) // 2
        lit = QColor(c["accent"])
        dim = QColor(c["input_bg"])
        for i in range(_RUN_EQ_BARS):
            bar_h = round(self._levels[i] * inner_h)
            x = start_x + i * (_RUN_EQ_BAR_W + _RUN_EQ_GAP)
            painter.setBrush(dim)
            painter.drawRoundedRect(
                x, base_y - inner_h, _RUN_EQ_BAR_W, inner_h, 1, 1
            )
            if bar_h > 0:
                painter.setBrush(lit)
                painter.drawRoundedRect(
                    x, base_y - bar_h, _RUN_EQ_BAR_W, bar_h, 1, 1
                )
