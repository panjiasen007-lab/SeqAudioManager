"""主题背景：用 QPainter 画出每套主题专属的氛围笔触。

不引入外部图片资源，所有背景都由调色板 + 程序化笔触生成并缓存为
QPixmap。这样主题切换、窗口缩放都不会反复解析大图，也不会增加安装包体积。

每套主题对应一种 ``pattern``：

* 深空 stars —— 星点、星云、星座连线
* 黑胶 vinyl —— 唱片纹路与暖色高光
* 春 blossom —— 花瓣与枝影
* 夏 sun —— 日光晕与射线
* 秋 leaves —— 落叶与暖色弧线
* 冬 snow —— 六角冰晶与雪点
* 海岸 waves —— 海浪曲线与浪花
* 暮光 dusk —— 晚霞色带与星点
* 太极 taiji —— 极淡的阴阳圆弧
* 纸白 paper —— 纸张纤维与颗粒
* 黑白极简 grid —— 细网格与对角线
"""

from __future__ import annotations

import math
import random
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import theme


def _alpha(color: str, value: int) -> QColor:
    result = QColor(color)
    result.setAlpha(value)
    return result


class BackdropWidget(QWidget):
    """铺满窗口的程序化主题背景；子控件全部叠在它上面。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._theme_name = theme.CURRENT
        self._cache: Optional[QPixmap] = None
        self._cache_key: tuple = ()
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_theme(self, name: str) -> None:
        if name not in theme.THEMES:
            name = theme.CURRENT
        self._theme_name = name
        self._cache = None
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._cache = None
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        key = (self._theme_name, width, height, round(self.devicePixelRatioF(), 2))
        if self._cache is None or self._cache_key != key:
            self._cache = self._render(width, height)
            self._cache_key = key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
        painter.end()

    # ---------------------------------------------------------------- 渲染

    def _render(self, width: int, height: int) -> QPixmap:
        palette = theme.THEMES.get(self._theme_name, theme.THEMES[theme.CURRENT])
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(
            max(1, int(round(width * ratio))),
            max(1, int(round(height * ratio))),
        )
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(palette.base))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0, 0, width, height)

        self._paint_base(painter, rect, width, height, palette)
        self._paint_glow(painter, width, height, palette)

        pattern = getattr(self, f"_paint_{palette.pattern}", None)
        if pattern is not None:
            pattern(painter, width, height, palette)
        self._paint_vignette(painter, rect, width, height, palette)
        painter.end()
        return pixmap

    @staticmethod
    def _paint_base(painter, rect, width, height, palette) -> None:
        gradient = QLinearGradient(0, 0, width * 0.9, height * 1.15)
        gradient.setColorAt(
            0.0, QColor(theme._mix(palette.base, palette.accent, 0.16))
        )
        gradient.setColorAt(0.46, QColor(palette.base))
        gradient.setColorAt(
            1.0, QColor(theme._mix(palette.base, palette.cyan, 0.07))
        )
        painter.fillRect(rect, gradient)

    @staticmethod
    def _paint_glow(painter, width, height, palette) -> None:
        radius = max(width, height) * 0.72
        glow = QRadialGradient(width * 0.82, height * 0.06, radius)
        alpha = 18 if palette.light else 42
        glow.setColorAt(0.0, _alpha(palette.accent, alpha))
        glow.setColorAt(0.45, _alpha(palette.accent, max(4, alpha // 3)))
        glow.setColorAt(1.0, _alpha(palette.accent, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(
            QRectF(
                width * 0.82 - radius,
                height * 0.06 - radius,
                radius * 2,
                radius * 2,
            )
        )

    @staticmethod
    def _paint_vignette(painter, rect, width, height, palette) -> None:
        radius = max(width, height) * 0.78
        vignette = QRadialGradient(width * 0.5, height * 0.42, radius)
        edge = 42 if palette.light else 70
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.62, QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QColor(0, 0, 0, edge))
        painter.setPen(Qt.NoPen)
        painter.setBrush(vignette)
        painter.drawRect(rect)

    # ---------------------------------------------------------------- 笔触

    @staticmethod
    def _rng(name: str, width: int, height: int) -> random.Random:
        return random.Random(f"{name}:{width}x{height}")

    def _paint_stars(self, painter, width, height, palette) -> None:
        rng = self._rng("stars", width, height)
        painter.setPen(Qt.NoPen)
        for _ in range(max(45, int(width * height / 16000))):
            x, y = rng.uniform(0, width), rng.uniform(0, height * 0.9)
            radius = rng.uniform(0.6, 2.2)
            color = palette.accent if rng.random() < 0.65 else palette.cyan
            painter.setBrush(_alpha(color, rng.randint(35, 150)))
            painter.drawEllipse(QPointF(x, y), radius, radius)

        for index in range(3):
            cx = rng.uniform(width * 0.05, width * 0.75)
            cy = rng.uniform(height * 0.08, height * 0.6)
            radius = rng.uniform(width * 0.12, width * 0.28)
            nebula = QRadialGradient(cx, cy, radius)
            color = palette.purple if index % 2 else palette.accent
            nebula.setColorAt(0.0, _alpha(color, 30))
            nebula.setColorAt(0.5, _alpha(color, 12))
            nebula.setColorAt(1.0, _alpha(color, 0))
            painter.setBrush(nebula)
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        points = [
            (rng.uniform(width * 0.1, width * 0.7), rng.uniform(height * 0.08, height * 0.55))
            for _ in range(5)
        ]
        painter.setPen(QPen(_alpha(palette.cyan, 24), 1))
        for start, end in zip(points, points[1:]):
            painter.drawLine(QPointF(start[0], start[1]), QPointF(end[0], end[1]))

    def _paint_vinyl(self, painter, width, height, palette) -> None:
        cx, cy = width * 0.80, height * 0.34
        radius = min(width, height) * 0.52
        painter.setBrush(Qt.NoBrush)
        for index in range(18):
            level = 0.20 + index * 0.045
            painter.setPen(QPen(_alpha(palette.accent, 8 + index), 1))
            painter.drawEllipse(QPointF(cx, cy), radius * level, radius * level)

        painter.setPen(QPen(_alpha(palette.cyan, 26), 2))
        painter.drawArc(
            QRectF(cx - radius * 0.62, cy - radius * 0.62, radius * 1.24, radius * 1.24),
            35 * 16,
            80 * 16,
        )
        painter.setPen(QPen(_alpha(palette.accent, 18), 6))
        painter.drawArc(
            QRectF(cx - radius * 0.94, cy - radius * 0.94, radius * 1.88, radius * 1.88),
            160 * 16,
            42 * 16,
        )

        glow = QRadialGradient(cx - radius * 0.2, cy - radius * 0.2, radius)
        glow.setColorAt(0.0, _alpha(palette.accent, 22))
        glow.setColorAt(1.0, _alpha(palette.accent, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

    def _paint_blossom(self, painter, width, height, palette) -> None:
        rng = self._rng("blossom", width, height)
        painter.setPen(QPen(_alpha(palette.cyan, 18), 2))
        for index in range(4):
            path = QPainterPath()
            start_x = width * (0.04 + index * 0.08)
            start_y = height * (0.9 - index * 0.12)
            path.moveTo(start_x, start_y)
            path.cubicTo(
                width * 0.24,
                height * 0.55,
                width * 0.12,
                height * 0.28,
                width * 0.42,
                height * 0.10,
            )
            painter.drawPath(path)

        for _ in range(26):
            x, y = rng.uniform(width * 0.05, width * 0.95), rng.uniform(0, height * 0.9)
            painter.save()
            painter.translate(x, y)
            painter.rotate(rng.uniform(0, 360))
            painter.setPen(Qt.NoPen)
            color = palette.purple if rng.random() < 0.45 else palette.accent
            painter.setBrush(_alpha(color, rng.randint(18, 42)))
            petal_w, petal_h = rng.uniform(7, 15), rng.uniform(3, 7)
            painter.drawEllipse(QRectF(-petal_w / 2, -petal_h / 2, petal_w, petal_h))
            painter.restore()

    def _paint_sun(self, painter, width, height, palette) -> None:
        cx, cy = width * 0.80, height * 0.10
        radius = min(width, height) * 0.24
        sun = QRadialGradient(cx, cy, radius * 1.8)
        sun.setColorAt(0.0, _alpha(palette.accent, 46))
        sun.setColorAt(0.42, _alpha(palette.accent, 18))
        sun.setColorAt(1.0, _alpha(palette.accent, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(sun)
        painter.drawEllipse(
            QRectF(cx - radius * 1.8, cy - radius * 1.8, radius * 3.6, radius * 3.6)
        )

        painter.setPen(QPen(_alpha(palette.cyan, 16), 2))
        for index in range(16):
            angle = index * math.pi / 8
            painter.drawLine(
                QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius),
                QPointF(
                    cx + math.cos(angle) * radius * 2.6,
                    cy + math.sin(angle) * radius * 2.6,
                ),
            )

        haze = QLinearGradient(0, height * 0.55, 0, height)
        haze.setColorAt(0.0, _alpha(palette.accent, 0))
        haze.setColorAt(1.0, _alpha(palette.accent, 16))
        painter.setBrush(haze)
        painter.drawRect(QRectF(0, height * 0.55, width, height * 0.45))

    def _paint_leaves(self, painter, width, height, palette) -> None:
        rng = self._rng("leaves", width, height)
        for _ in range(18):
            x, y = rng.uniform(0, width), rng.uniform(0, height * 0.9)
            painter.save()
            painter.translate(x, y)
            painter.rotate(rng.uniform(-70, 70))
            color = palette.purple if rng.random() < 0.35 else palette.accent
            painter.setPen(QPen(_alpha(color, rng.randint(20, 45)), 1))
            painter.setBrush(_alpha(color, rng.randint(8, 24)))
            path = QPainterPath()
            path.moveTo(0, 0)
            path.cubicTo(10, -8, 22, 4, 30, 0)
            path.cubicTo(22, 8, 10, 8, 0, 0)
            painter.drawPath(path)
            painter.restore()

        painter.setPen(QPen(_alpha(palette.cyan, 16), 2))
        for index in range(3):
            path = QPainterPath()
            path.moveTo(width * 0.05, height * (0.7 + index * 0.08))
            path.cubicTo(
                width * 0.4,
                height * (0.5 + index * 0.08),
                width * 0.6,
                height * (0.3 + index * 0.06),
                width * 0.95,
                height * (0.18 + index * 0.04),
            )
            painter.drawPath(path)

    def _paint_snow(self, painter, width, height, palette) -> None:
        rng = self._rng("snow", width, height)
        painter.setPen(Qt.NoPen)
        for _ in range(60):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            painter.setBrush(_alpha(palette.accent, rng.randint(20, 80)))
            radius = rng.uniform(0.8, 2.8)
            painter.drawEllipse(QPointF(x, y), radius, radius)

        painter.setPen(QPen(_alpha(palette.cyan, 26), 1.2))
        for _ in range(14):
            cx, cy = rng.uniform(0, width), rng.uniform(0, height * 0.85)
            radius = rng.uniform(6, 18)
            for arm in range(6):
                angle = arm * math.pi / 3
                painter.drawLine(
                    QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius),
                    QPointF(cx - math.cos(angle) * radius, cy - math.sin(angle) * radius),
                )

    def _paint_waves(self, painter, width, height, palette) -> None:
        painter.setBrush(Qt.NoBrush)
        for band in range(6):
            base_y = height * 0.70 + band * height * 0.045
            color = palette.cyan if band % 2 == 0 else palette.accent
            painter.setPen(QPen(_alpha(color, 10 + band * 3), 1.4))
            path = QPainterPath()
            for x in range(0, width + 8, 6):
                y = base_y + math.sin(x / 92 + band * 1.35) * (10 + band * 2)
                if x == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.drawPath(path)

        rng = self._rng("waves", width, height)
        painter.setPen(Qt.NoPen)
        for _ in range(45):
            x = rng.uniform(0, width)
            y = rng.uniform(height * 0.72, height * 0.96)
            radius = rng.uniform(1, 3.5)
            painter.setBrush(_alpha(palette.cyan, rng.randint(15, 40)))
            painter.drawEllipse(QPointF(x, y), radius, radius)

    def _paint_dusk(self, painter, width, height, palette) -> None:
        for index, (top, alpha) in enumerate(
            ((0.30, 12), (0.46, 18), (0.62, 24), (0.78, 14))
        ):
            band = QLinearGradient(0, height * top, 0, height * (top + 0.22))
            color = palette.purple if index % 2 == 0 else palette.accent
            band.setColorAt(0.0, _alpha(color, 0))
            band.setColorAt(0.5, _alpha(color, alpha))
            band.setColorAt(1.0, _alpha(color, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(band)
            painter.drawRect(QRectF(0, height * top, width, height * 0.22))

        rng = self._rng("dusk", width, height)
        painter.setPen(Qt.NoPen)
        for _ in range(38):
            x, y = rng.uniform(0, width), rng.uniform(0, height * 0.5)
            radius = rng.uniform(0.5, 1.8)
            painter.setBrush(_alpha(palette.cyan, rng.randint(40, 120)))
            painter.drawEllipse(QPointF(x, y), radius, radius)

        painter.setPen(QPen(_alpha(palette.cyan, 30), 2))
        painter.drawLine(
            QPointF(0, height * 0.70), QPointF(width, height * 0.70)
        )

    def _paint_taiji(self, painter, width, height, palette) -> None:
        radius = min(width, height) * 0.36
        cx, cy = width * 0.82, height * 0.42
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_alpha("#ffffff", 10), 1))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        path = QPainterPath()
        path.moveTo(cx, cy - radius)
        path.arcTo(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), 90, -180)
        path.arcTo(
            QRectF(cx, cy - radius, radius, radius), 90, 180
        )
        path.arcTo(
            QRectF(cx - radius, cy, radius, radius), 270, 180
        )
        painter.setBrush(_alpha("#ffffff", 7))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        painter.setBrush(_alpha("#000000", 18))
        painter.drawEllipse(QPointF(cx, cy + radius * 0.5), radius * 0.5, radius * 0.5)
        painter.setBrush(_alpha("#ffffff", 14))
        painter.drawEllipse(QPointF(cx, cy - radius * 0.5), radius * 0.5, radius * 0.5)

    def _paint_paper(self, painter, width, height, palette) -> None:
        rng = self._rng("paper", width, height)
        painter.setPen(Qt.NoPen)
        for _ in range(int(width * height / 5200)):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            radius = rng.uniform(0.3, 1.1)
            painter.setBrush(_alpha(palette.fg_dim, rng.randint(6, 16)))
            painter.drawEllipse(QPointF(x, y), radius, radius)

        painter.setPen(QPen(_alpha(palette.accent, 10), 1))
        for _ in range(26):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            length = rng.uniform(20, 90)
            painter.drawLine(QPointF(x, y), QPointF(x + length, y + rng.uniform(-4, 4)))

        for _ in range(3):
            cx, cy = rng.uniform(0, width), rng.uniform(0, height)
            radius = rng.uniform(width * 0.12, width * 0.3)
            stain = QRadialGradient(cx, cy, radius)
            stain.setColorAt(0.0, _alpha(palette.accent, 10))
            stain.setColorAt(0.55, _alpha(palette.accent, 4))
            stain.setColorAt(1.0, _alpha(palette.accent, 0))
            painter.setBrush(stain)
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

    def _paint_grid(self, painter, width, height, palette) -> None:
        step = 54
        painter.setPen(QPen(_alpha(palette.fg_dim, 9), 1))
        for x in range(0, width + step, step):
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
        for y in range(0, height + step, step):
            painter.drawLine(QPointF(0, y), QPointF(width, y))

        painter.setPen(QPen(_alpha(palette.accent, 14), 2))
        painter.drawLine(QPointF(0, height * 0.18), QPointF(width, height * 0.18))
        painter.drawLine(QPointF(width * 0.12, 0), QPointF(width * 0.12, height))
        painter.setPen(QPen(_alpha(palette.cyan, 10), 8))
        painter.drawLine(QPointF(0, height), QPointF(width * 0.34, 0))
