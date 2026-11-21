"""共享矢量图标 + 自绘「玻璃」按钮。

为什么要有这个模块
------------------
播放条与迷你播条都需要**不依赖字体**的图标（emoji 在 Win7/精简字体环境下会退化成
方块或缺字，用户看到的"播放键只剩一片蓝色"就是这么来的）。图标绘制原先只存在于
``mini_player`` 里，主播放条用的是 ``QPushButton("▶")`` + QSS —— QSS 的
``background`` 是实心块，播放态一换 objectName 就变成一坨蓝色。

于是把矢量图标提到本模块共享，并新增 ``IconButton``：它继承 ``QPushButton``
（保留 ``clicked`` / ``text()`` / 无障碍语义，既有用例不必改），但 **``paintEvent``
完全不调用 super()**，所有像素自己画 —— QSS 的实心背景再也影响不到它。

配色一律取自 ``theme``（11 套主题切换时跟着变），不写死任何色值。
"""

from __future__ import annotations

import math
import re
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QPushButton, QWidget

from . import theme


#: ``rgb(r,g,b)`` / ``rgba(r,g,b,a)`` 解析（``QColor`` 不认这种函数式写法，
#: 而 ``theme`` 的半透明面（``panel`` / ``card`` / ``hover``）正是这种格式）
_FUNC_COLOR_RE = re.compile(
    r"rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*(?:,\s*([0-9.]+)\s*)?\)",
    re.IGNORECASE,
)


def qcolor(color: str, alpha: float = 1.0) -> QColor:
    """``theme`` 里的任意颜色字符串 → 指定透明度的 ``QColor``。

    支持 ``#rrggbb`` 与 ``rgba(r,g,b,a)`` 两种写法（后者自带的基础透明度会与
    ``alpha`` 相乘，这样「半透明面板再叠一层 10%」的效果才符合直觉）。
    """
    text = str(color or "").strip()
    base_alpha = 1.0
    match = _FUNC_COLOR_RE.search(text)
    if match:
        red, green, blue = (int(float(match.group(index))) for index in (1, 2, 3))
        if match.group(4) is not None:
            base_alpha = float(match.group(4))
        value = QColor(red, green, blue)
    else:
        value = QColor(text)
    if not value.isValid():  # 兜底：绝不因为一个坏色值让整条播放条画不出来
        value = QColor("#ffffff")
        base_alpha = 1.0
    value.setAlphaF(max(0.0, min(1.0, float(alpha) * base_alpha)))
    return value


def draw_glyph(painter: QPainter, key: str, rect: QRectF, color: QColor) -> None:
    """在 ``rect`` 里画一个矢量图标（不依赖任何字体）。

    Args:
        painter: 已经创建好的画笔（本函数自己 save/restore，不改调用方状态）。
        key: 图标键，见 ``mini_player`` / ``player_bar`` 的用法。
        rect: 图标所占的矩形（图标按比例缩放到该矩形内）。
        color: 描边与填充颜色。
    """
    painter.save()
    painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    def tri(points: list[tuple[float, float]]) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(
            QPolygonF([QPointF(x + px * w, y + py * h) for px, py in points])
        )
        painter.setBrush(Qt.NoBrush)

    if key == "play":
        tri([(0.18, 0.04), (0.18, 0.96), (0.94, 0.5)])
    elif key == "pause":
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(x + 0.16 * w, y + 0.06 * h, 0.24 * w, 0.88 * h), 1.4, 1.4
        )
        painter.drawRoundedRect(
            QRectF(x + 0.60 * w, y + 0.06 * h, 0.24 * w, 0.88 * h), 1.4, 1.4
        )
        painter.setBrush(Qt.NoBrush)
    elif key == "stop":
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(x + 0.16 * w, y + 0.16 * h, 0.68 * w, 0.68 * h), 2.2, 2.2
        )
        painter.setBrush(Qt.NoBrush)
    elif key == "prev":
        tri([(0.62, 0.06), (0.62, 0.94), (0.12, 0.5)])
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            QPointF(x + 0.80 * w, y + 0.08 * h), QPointF(x + 0.80 * w, y + 0.92 * h)
        )
    elif key == "next":
        tri([(0.38, 0.06), (0.38, 0.94), (0.88, 0.5)])
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            QPointF(x + 0.20 * w, y + 0.08 * h), QPointF(x + 0.20 * w, y + 0.92 * h)
        )
    elif key == "view":
        # 展开到主界面：两条对角箭头
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            QPointF(x + 0.18 * w, y + 0.82 * h), QPointF(x + 0.82 * w, y + 0.18 * h)
        )
        tri([(0.58, 0.10), (0.92, 0.10), (0.92, 0.44)])
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            QPointF(x + 0.82 * w, y + 0.18 * h), QPointF(x + 0.18 * w, y + 0.82 * h)
        )
        tri([(0.42, 0.90), (0.08, 0.90), (0.08, 0.56)])
    elif key == "list":
        # 播放列表：三条「项目 + 文本」的简图
        painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
        for offset in (0.18, 0.5, 0.82):
            cy = y + offset * h
            painter.drawPoint(QPointF(x + 0.12 * w, cy))
            painter.drawLine(QPointF(x + 0.30 * w, cy), QPointF(x + 0.90 * w, cy))
    elif key == "mode_seq":
        painter.drawLine(
            QPointF(x + 0.08 * w, y + 0.5 * h), QPointF(x + 0.86 * w, y + 0.5 * h)
        )
        tri([(0.62, 0.22), (0.94, 0.5), (0.62, 0.78)])
    elif key == "mode_shuffle":
        painter.drawLine(
            QPointF(x + 0.08 * w, y + 0.18 * h), QPointF(x + 0.86 * w, y + 0.82 * h)
        )
        painter.drawLine(
            QPointF(x + 0.08 * w, y + 0.82 * h), QPointF(x + 0.86 * w, y + 0.18 * h)
        )
        tri([(0.62, 0.10), (0.94, 0.10), (0.94, 0.42)])
        tri([(0.94, 0.58), (0.94, 0.90), (0.62, 0.90)])
    elif key in ("mode_one", "mode_all"):
        painter.drawArc(
            QRectF(x + 0.12 * w, y + 0.12 * h, 0.76 * w, 0.76 * h), 40 * 16, 285 * 16
        )
        tri([(0.62, 0.02), (0.98, 0.12), (0.72, 0.42)])
        if key == "mode_one":
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x + 0.5 * w, y + 0.5 * h), w * 0.09, h * 0.09)
            painter.setBrush(Qt.NoBrush)
    elif key in ("volume", "mute"):
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(x + 0.06 * w, y + 0.34 * h),
                    QPointF(x + 0.28 * w, y + 0.34 * h),
                    QPointF(x + 0.52 * w, y + 0.12 * h),
                    QPointF(x + 0.52 * w, y + 0.88 * h),
                    QPointF(x + 0.28 * w, y + 0.66 * h),
                    QPointF(x + 0.06 * w, y + 0.66 * h),
                ]
            )
        )
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap))
        if key == "volume":
            painter.drawArc(
                QRectF(x + 0.40 * w, y + 0.24 * h, 0.34 * w, 0.52 * h),
                -60 * 16,
                120 * 16,
            )
            painter.drawArc(
                QRectF(x + 0.34 * w, y + 0.12 * h, 0.56 * w, 0.76 * h),
                -60 * 16,
                120 * 16,
            )
        else:
            painter.drawLine(
                QPointF(x + 0.62 * w, y + 0.34 * h), QPointF(x + 0.90 * w, y + 0.66 * h)
            )
            painter.drawLine(
                QPointF(x + 0.90 * w, y + 0.34 * h), QPointF(x + 0.62 * w, y + 0.66 * h)
            )
    elif key == "min":
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            QPointF(x + 0.12 * w, y + 0.5 * h), QPointF(x + 0.88 * w, y + 0.5 * h)
        )
    elif key == "max":
        painter.drawRoundedRect(
            QRectF(x + 0.14 * w, y + 0.14 * h, 0.72 * w, 0.72 * h), 1.0, 1.0
        )
    elif key == "close":
        painter.drawLine(
            QPointF(x + 0.16 * w, y + 0.16 * h), QPointF(x + 0.84 * w, y + 0.84 * h)
        )
        painter.drawLine(
            QPointF(x + 0.84 * w, y + 0.16 * h), QPointF(x + 0.16 * w, y + 0.84 * h)
        )
    elif key == "eq":
        # 均衡器语义：三条竖向滑杆 + 各自的圆形把手
        painter.setPen(QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap))
        columns = (0.22, 0.50, 0.78)
        knobs = (0.64, 0.34, 0.70)
        for cx in columns:
            painter.drawLine(
                QPointF(x + cx * w, y + 0.08 * h), QPointF(x + cx * w, y + 0.92 * h)
            )
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for cx, ky in zip(columns, knobs):
            painter.drawEllipse(QPointF(x + cx * w, y + ky * h), w * 0.11, h * 0.11)
        painter.setBrush(Qt.NoBrush)
    elif key == "signal":
        # 信号路径语义：一条正弦波
        path = QPainterPath()
        steps = 26
        for index in range(steps + 1):
            ratio = index / steps
            px = 0.05 + 0.90 * ratio
            py = 0.5 - 0.33 * math.sin(ratio * 2.0 * math.pi * 1.5)
            point = QPointF(x + px * w, y + py * h)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
    painter.restore()


class IconButton(QPushButton):
    """自绘圆形「玻璃」按钮：图标矢量绘制，绝不被 QSS 的实心背景污染。

    两种外观（``variant``）：

    * ``glass`` + ``accent=True``（播放键）：半透明 accent 圆底 + 亮色描边。
      **任何状态（含 hover / pressed / 播放中）都不会填满实心 accent** —— 这正是
      用户反馈的「播放之后也是一坨蓝色」的根因修复。
    * ``glass``：透明底，hover 时出现一层极淡的圆底 + 图标由 ``FG_DIM`` 提到 ``FG``。

    Signals:
        clicked: 继承自 ``QPushButton``（无障碍与既有用例都用它）。
    """

    #: 图标相对按钮尺寸的内缩比例（越大图标越小）
    ICON_INSET_RATIO = 0.28

    def __init__(
        self,
        icon_key: str,
        size: int = 30,
        *,
        accent: bool = False,
        tip: str = "",
        variant: str = "glass",
        text: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self.icon_key = str(icon_key)
        self._accent = bool(accent)
        self._variant = str(variant or "glass")
        self._emphasized = False
        self._hovered = False
        self.setFixedSize(int(size), int(size))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover, True)
        # 按钮自己不画文字，但保留 text 供无障碍/断言使用
        self.setFlat(True)
        if tip:
            self.setToolTip(tip)

    # ------------------------------------------------------------ 状态

    def set_icon(self, icon_key: str) -> None:
        """切换图标（播放/暂停、播放模式），只重绘不改样式。"""
        key = str(icon_key)
        if key == self.icon_key:
            return
        self.icon_key = key
        self.update()

    def set_emphasized(self, emphasized: bool) -> None:
        """``True`` 时描边更亮（用于「正在播放」的视觉反馈）。"""
        value = bool(emphasized)
        if value == self._emphasized:
            return
        self._emphasized = value
        self.update()

    def is_emphasized(self) -> bool:
        return self._emphasized

    # ------------------------------------------------------------ 事件

    def sizeHint(self):  # noqa: N802 - Qt 命名
        return self.size()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        # 可用性变化会导致配色变化，必须重绘（QSS 圆角不参与，但颜色参与）
        self.update()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = float(min(self.width(), self.height()))
        rect = QRectF(0.0, 0.0, side, side).adjusted(1.0, 1.0, -1.0, -1.0)
        enabled = self.isEnabled()
        hovered = self._hovered and enabled
        pressed = self.isDown() and enabled
        icon_color = self._accent_icon_color(enabled, hovered, pressed)

        if self._accent:
            self._paint_accent_body(painter, rect, enabled, hovered, pressed)
        else:
            self._paint_glass_body(painter, rect, enabled, hovered, pressed)

        inset = side * self.ICON_INSET_RATIO
        draw_glyph(
            painter,
            self.icon_key,
            QRectF(rect).adjusted(inset, inset, -inset, -inset),
            icon_color,
        )

        if self.hasFocus() and enabled:
            painter.setPen(QPen(qcolor(theme.ACCENT, 0.55), 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect.adjusted(-0.5, -0.5, 0.5, 0.5))
        painter.end()

    def _accent_icon_color(self, enabled: bool, hovered: bool, pressed: bool) -> QColor:
        """图标颜色：accent 键用亮色描边色，普通键用前景色。"""
        if not enabled:
            return qcolor(theme.FG_MUTE)
        if self._accent:
            if pressed:
                return qcolor(theme.ACCENT)
            return qcolor(theme.ACCENT_HOVER if (hovered or self._emphasized) else theme.ACCENT)
        return qcolor(theme.FG if (hovered or pressed or self._emphasized) else theme.FG_DIM)

    def _paint_accent_body(
        self, painter: QPainter, rect: QRectF, enabled: bool, hovered: bool, pressed: bool
    ) -> None:
        """播放键：淡 accent 圆底 + 亮描边；**永不实心填充**。"""
        fill_alpha = 0.14 if self._emphasized else 0.10
        border_alpha = 0.85 if self._emphasized else 0.65
        border_width = 1.5
        if pressed:
            fill_alpha = max(fill_alpha, 0.24)
            border_alpha = 1.0
            border_width = 1.6
        elif hovered:
            fill_alpha = 0.18
            border_alpha = 1.0
            border_width = 1.6
        if not enabled:
            fill_alpha = 0.05
            border_alpha = 0.20
            border_width = 1.0

        if hovered:
            # 外侧 2px 光环：悬停时的「微光」，不改变填充饱和度
            painter.setPen(Qt.NoPen)
            painter.setBrush(qcolor(theme.ACCENT, 0.20))
            painter.drawEllipse(rect.adjusted(-1.5, -1.5, 1.5, 1.5))

        painter.setPen(QPen(qcolor(theme.ACCENT, border_alpha), border_width))
        painter.setBrush(qcolor(theme.ACCENT, fill_alpha))
        painter.drawEllipse(rect)

    def _paint_glass_body(
        self, painter: QPainter, rect: QRectF, enabled: bool, hovered: bool, pressed: bool
    ) -> None:
        """普通键：默认完全透明，只在交互时浮出一层极淡圆底。"""
        painter.setPen(Qt.NoPen)
        if pressed:
            painter.setBrush(qcolor(theme.FG, 0.10))
        elif hovered:
            painter.setBrush(qcolor(theme.FG, 0.06))
        elif not enabled:
            painter.setBrush(qcolor(theme.FG, 0.02))
        else:
            painter.setBrush(Qt.NoBrush)
        if painter.brush().style() != Qt.NoBrush:
            painter.drawEllipse(rect)


__all__ = ["draw_glyph", "IconButton", "qcolor"]
