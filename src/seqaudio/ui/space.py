"""星空背景：在宇宙里持续前行，并不时切换星区。

设计动机
--------
「深色主题」和「宇宙感」是两件事。前者靠配色和层次就能做到；后者必须有
**运动**和**深度** —— 静止的星点壁纸看久了只是一张图，只有让星星持续朝镜头
飞过来，界面才会"活"。

三层视差
--------
恒星按 ``z``（距镜头的深度）分层，投影用透视公式 ``screen = center + xy/z``：
近处星星走得快、拖出尾迹，远处几乎不动。这一层视差是人眼判断"在移动"的
主要依据，比单纯让点移动有效得多。

场景切换
--------
每 24 秒换一个星区（深空 / 猎户 / 玫瑰 / 船底 / 天鹅），换场时：

1. 调色板用 2.6 秒做线性插值，星云颜色平滑过渡而不是硬切；
2. 星星速度临时拉到 3 倍再回落 —— 视觉上就是一次"跃迁"，把换场解释成
   「飞船加速进入了另一片星域」；
3. 同时星星被拉长成短线（尾迹按速度延长），强化加速感。

性能
----
星星坐标全部放在 numpy 数组里做向量化推进，绘制时按「尺寸档 × 色调」分组成
少量 ``drawPoints`` 调用，整帧只有十几次 Qt 调用，25fps 下 CPU 占用可以忽略。
窗口最小化或关闭动态背景时计时器直接停掉，不做无谓计算。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

#: 每 24 秒换一个星区
SCENE_SECONDS = 24.0
#: 换场的调色板插值时长
FADE_SECONDS = 2.6
#: 跃迁加速持续时长
WARP_SECONDS = 3.4
#: 帧间隔（毫秒）。25fps 对背景足够，再高只是白烧 CPU
FRAME_MS = 40


@dataclass(frozen=True)
class Palette:
    """一个星区的配色。"""

    name: str
    base: str          # 窗口底色（最深处）
    nebula_a: str      # 星云主色
    nebula_b: str      # 星云副色
    haze: str          # 地平线方向的薄雾


#: 星区序列。全部落在冷色域内，保证界面元素（强调蓝/青）始终压得住背景。
PALETTES: tuple[Palette, ...] = (
    Palette("深空", "#04060b", "#1d3a7a", "#0d4a63", "#16224a"),
    Palette("猎户座旋臂", "#05050d", "#3a2470", "#173a6e", "#1b1a3c"),
    Palette("玫瑰星云", "#07040a", "#5c2350", "#1d2266", "#2a1430"),
    Palette("船底座", "#04070a", "#0f5163", "#1a5691", "#0c2430"),
    Palette("天鹅座", "#05060c", "#27336e", "#183056", "#101830"),
)

#: 星色：0 冷白、1 纯白、2 淡蓝、3 少量暖黄（真实星空的色温差异）
STAR_TINTS: tuple[str, ...] = ("#dfe9ff", "#ffffff", "#a9c8ff", "#ffd9a8")

#: 近处大星单独绘制，数量少但最抢眼
BIG_STAR_COUNT = 26


class SpaceScene:
    """星场状态机：推动星星、切换星区、绘制。

    与 Qt 解耦（只依赖 QPainter），便于离屏渲染做视觉回归。
    """

    def __init__(self, star_count: int = 760, seed: int = 20261031):
        self.rng = np.random.default_rng(seed)
        random.seed(seed)

        count = star_count
        # x/y 用 [-1,1] 的归一化坐标，z 是深度（越小越近）
        self.x = self.rng.uniform(-1.75, 1.75, count)
        self.y = self.rng.uniform(-1.15, 1.15, count)
        self.z = self.rng.uniform(0.02, 1.0, count) ** 0.85
        self.core = self.rng.uniform(0.45, 1.0, count)
        self.tint = self.rng.integers(0, len(STAR_TINTS), count)
        # 少数星偏大，制造层次
        self.big = self.rng.random(count) < 0.06

        self.scene_index = 0
        self.next_index = 1
        self.scene_age = 0.0
        self.fade = 0.0
        self.warp = 0.0
        self.base_speed = 0.145
        self.time = 0.0
        self._palette_cache = self._blend_palette(PALETTES[0], PALETTES[1], 0.0)

        # 星云团的位置（归一化），缓慢漂移，避免整块背景死板
        self._clouds = [
            (self.rng.uniform(0.12, 0.34), self.rng.uniform(0.16, 0.40), 0.62, 0.0),
            (self.rng.uniform(0.62, 0.88), self.rng.uniform(0.20, 0.46), 0.50, 2.1),
            (self.rng.uniform(0.34, 0.66), self.rng.uniform(0.64, 0.90), 0.72, 4.2),
        ]

    # ------------------------------------------------------------ 推进

    def tick(self, dt: float) -> None:
        dt = min(dt, 0.12)          # 掉帧时不跳跃
        self.time += dt
        self.scene_age += dt

        # 换场：到点后开始插值，插值完成才切换索引
        if self.scene_age >= SCENE_SECONDS and self.fade <= 0.0:
            self.warp = 1.0
            self.fade = 1e-4
            self.scene_age = 0.0
        if self.fade > 0.0:
            self.fade = min(1.0, self.fade + dt / FADE_SECONDS)
            if self.fade >= 1.0:
                self.scene_index = self.next_index
                self.next_index = (self.next_index + 1) % len(PALETTES)
                self.fade = 0.0

        self.warp = max(0.0, self.warp - dt / WARP_SECONDS)
        # 加速曲线：起手猛、收尾缓，读起来更像一次"跃迁后减速"
        warp_boost = 1.0 + 2.6 * (self.warp ** 1.6)
        speed = self.base_speed * warp_boost

        self.z -= speed * dt
        # 越过镜头就重新投放到远端，保证星数恒定
        passed = self.z <= 0.012
        if np.any(passed):
            n = int(np.count_nonzero(passed))
            self.z[passed] = self.rng.uniform(0.86, 1.0, n)
            self.x[passed] = self.rng.uniform(-1.75, 1.75, n)
            self.y[passed] = self.rng.uniform(-1.15, 1.15, n)
            self.core[passed] = self.rng.uniform(0.45, 1.0, n)
            self.tint[passed] = self.rng.integers(0, len(STAR_TINTS), n)
            self.big[passed] = self.rng.random(n) < 0.06

        a = PALETTES[self.scene_index]
        b = PALETTES[self.next_index]
        self._palette_cache = self._blend_palette(a, b, self._ease(self.fade))

    @staticmethod
    def _ease(t: float) -> float:
        """smoothstep：两端平缓，中段快，比线性自然得多。"""
        t = max(0.0, min(1.0, t))
        return t * t * (3 - 2 * t)

    @staticmethod
    def _blend_palette(a: Palette, b: Palette, t: float) -> dict[str, QColor]:
        def mix(c1: str, c2: str) -> QColor:
            ca, cb = QColor(c1), QColor(c2)
            return QColor(
                int(ca.red() + (cb.red() - ca.red()) * t),
                int(ca.green() + (cb.green() - ca.green()) * t),
                int(ca.blue() + (cb.blue() - ca.blue()) * t),
            )

        return {
            "base": mix(a.base, b.base),
            "nebula_a": mix(a.nebula_a, b.nebula_a),
            "nebula_b": mix(a.nebula_b, b.nebula_b),
            "haze": mix(a.haze, b.haze),
            "name": a.name if t < 0.5 else b.name,
        }

    @property
    def palette(self) -> dict[str, QColor]:
        return self._palette_cache

    @property
    def scene_name(self) -> str:
        return str(self._palette_cache["name"])

    # ------------------------------------------------------------ 绘制

    def paint(self, painter: QPainter, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = self._palette_cache

        # 1) 底色
        painter.fillRect(0, 0, width, height, pal["base"])

        # 2) 星云：三个大半径径向渐变叠出"气体"感
        self._paint_nebula(painter, width, height, pal)

        # 3) 星场
        self._paint_stars(painter, width, height)

        # 4) 暗角：压住四角，让中间的表格与文字更聚焦
        self._paint_vignette(painter, width, height)

    def _paint_nebula(self, painter: QPainter, w: int, h: int, pal: dict[str, QColor]) -> None:
        painter.setPen(Qt.NoPen)

        # 银河带：一条斜向的宽带，先铺出整体结构。
        # 只有云团没有带，背景会像几团孤立的雾而不像星系。
        band = QLinearGradient(0, h * 0.15, w, h * 0.85)
        clear = QColor(pal["nebula_a"])
        clear.setAlpha(0)
        band.setColorAt(0.0, clear)
        band.setColorAt(0.38, QColor(pal["haze"].red(), pal["haze"].green(), pal["haze"].blue(), 60))
        band.setColorAt(0.55, QColor(pal["nebula_a"].red(), pal["nebula_a"].green(), pal["nebula_a"].blue(), 48))
        band.setColorAt(0.78, QColor(pal["nebula_b"].red(), pal["nebula_b"].green(), pal["nebula_b"].blue(), 40))
        band.setColorAt(1.0, clear)
        painter.setBrush(band)
        painter.drawRect(0, 0, w, h)

        colors = (pal["nebula_a"], pal["nebula_b"], pal["haze"])
        for index, (cx, cy, radius, phase) in enumerate(self._clouds):
            # 缓慢漂移，周期不同，视觉上永远不会完全重复
            dx = math.sin(self.time * 0.021 + phase) * 0.045
            dy = math.cos(self.time * 0.017 + phase * 1.7) * 0.035
            px = (cx + dx) * w
            py = (cy + dy) * h
            r = radius * max(w, h) * 0.78

            color = QColor(colors[index % len(colors)])
            color.setAlpha(104 if index == 0 else 82)
            gradient = QRadialGradient(px, py, r)
            gradient.setColorAt(0.0, color)
            mid = QColor(color)
            mid.setAlpha(max(14, color.alpha() // 3))
            gradient.setColorAt(0.5, mid)
            gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.setBrush(gradient)
            painter.drawEllipse(QRectF(px - r, py - r, r * 2, r * 2))

            if index == 0:
                # 主云团加一颗亮核：没有核心的星云看起来只是一片脏雾
                core_r = r * 0.34
                core = QRadialGradient(px, py, core_r)
                hot = QColor(color)
                hot.setAlpha(150)
                core.setColorAt(0.0, hot)
                core.setColorAt(0.45, QColor(color.red(), color.green(), color.blue(), 56))
                core.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
                painter.setBrush(core)
                painter.drawEllipse(QRectF(px - core_r, py - core_r, core_r * 2, core_r * 2))

    def _paint_stars(self, painter: QPainter, w: int, h: int) -> None:
        focal = min(w, h) * 0.95
        cx, cy = w / 2.0, h / 2.0

        z = self.z
        # 透视投影：屏幕位置 = 中心 + 归一化坐标 / 深度 × 焦距
        sx = cx + (self.x / z) * focal * 0.5
        sy = cy + (self.y / z) * focal * 0.5

        # 可见性：太贴近边缘的星不画，避免屏幕外溢出
        visible = (sx > -40) & (sx < w + 40) & (sy > -40) & (sy < h + 40)
        if not np.any(visible):
            return

        # 亮度随深度衰减；近处略过曝，模拟真实镜头
        brightness = np.clip((1.05 - z) * 1.55, 0.06, 1.0)
        # 拖尾长度：基础速度 + 跃迁加成
        trail = (0.006 + 0.055 * self.warp) / np.maximum(z, 0.02)

        # 分组：尺寸档 × 色调。整帧只有十几次 draw 调用
        self._draw_grouped(painter, w, h, sx, sy, brightness, trail, visible, focal, (cx, cy))

        # 近处大星单独画，带光晕
        self._draw_big_stars(painter, sx, sy, brightness, visible)

    def _draw_grouped(
        self,
        painter: QPainter,
        w: int,
        h: int,
        sx: np.ndarray,
        sy: np.ndarray,
        brightness: np.ndarray,
        trail: np.ndarray,
        visible: np.ndarray,
        focal: float,
        center: tuple[float, float],
    ) -> None:
        _center_x, _center_y = center
        near = visible & (self.z < 0.55)
        mid = visible & (self.z >= 0.55) & (self.z < 0.82)
        far = visible & (self.z >= 0.82)

        # 拖尾比例：star 沿 z 轴前进 dz，投影位置按 z/(z+dz) 收缩，
        # 所以尾巴一定落在「中心 -> 当前位置」这条射线上，长度和 1/z 成正比。
        # 早先用竖直短线是错的 —— 那样所有星星像下雨，不像在飞。
        streak = 0.022 + 0.55 * self.warp

        layers = (
            (far, 0.8, 0.55, False),       # (掩码, 线宽, 亮度系数, 是否画拖尾)
            (mid, 1.1, 0.85, True),
            (near, 1.6, 1.0, True),
        )
        for mask, width, gain, with_trail in layers:
            if not np.any(mask):
                continue
            idx = np.where(mask)[0]
            for tint_index, tint in enumerate(STAR_TINTS):
                sel = idx[self.tint[idx] == tint_index]
                if sel.size == 0:
                    continue
                color = QColor(tint)
                # 透明度跟着深度走：远处暗、近处亮，否则整片星空平得像贴纸
                mean_brightness = float(np.mean(brightness[sel]))
                color.setAlpha(max(34, min(210, int(210 * gain * mean_brightness))))
                pen = QPen(color)
                pen.setWidthF(width)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)

                if not with_trail:
                    painter.drawPoints(QPolygonF([QPointF(float(sx[i]), float(sy[i])) for i in sel]))
                    continue

                lines = []
                for i in sel:
                    x0, y0 = float(sx[i]), float(sy[i])
                    shrink = min(0.82, streak / max(float(self.z[i]), 0.02))
                    if shrink < 0.02:
                        lines.append((QPointF(x0, y0), QPointF(x0, y0)))
                    else:
                        lines.append((
                            QPointF(_center_x + (x0 - _center_x) * (1.0 - shrink),
                                    _center_y + (y0 - _center_y) * (1.0 - shrink)),
                            QPointF(x0, y0),
                        ))
                painter.drawLines([line for pair in lines for line in pair])

    def _draw_big_stars(
        self,
        painter: QPainter,
        sx: np.ndarray,
        sy: np.ndarray,
        brightness: np.ndarray,
        visible: np.ndarray,
    ) -> None:
        indices = np.where(visible & self.big & (self.z < 0.5))[0]
        if indices.size == 0:
            return
        painter.setPen(Qt.NoPen)
        for i in indices[:BIG_STAR_COUNT]:
            radius = 1.6 + (1.0 - float(self.z[i])) * 3.4 * float(self.core[i])
            alpha = int(120 * float(brightness[i]))
            halo = QColor(STAR_TINTS[int(self.tint[i])])
            halo.setAlpha(max(10, alpha // 3))
            gradient = QRadialGradient(float(sx[i]), float(sy[i]), radius * 3.2)
            gradient.setColorAt(0.0, QColor(halo.red(), halo.green(), halo.blue(), max(20, alpha // 2)))
            gradient.setColorAt(0.35, halo)
            gradient.setColorAt(1.0, QColor(halo.red(), halo.green(), halo.blue(), 0))
            painter.setBrush(gradient)
            painter.drawEllipse(
                QRectF(float(sx[i]) - radius * 3.2, float(sy[i]) - radius * 3.2,
                       radius * 6.4, radius * 6.4)
            )
            painter.setBrush(QColor(255, 255, 255, min(255, alpha + 90)))
            painter.drawEllipse(QRectF(float(sx[i]) - radius * 0.5,
                                       float(sy[i]) - radius * 0.5,
                                       radius, radius))

    def _paint_vignette(self, painter: QPainter, w: int, h: int) -> None:
        radius = max(w, h) * 0.86
        gradient = QRadialGradient(w / 2.0, h / 2.0, radius)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.62, QColor(0, 0, 0, 30))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 150))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(0, 0, w, h)


class SpaceView(QWidget):
    """把 SpaceScene 挂到窗口上的容器：只管时钟与重绘。

    窗口隐藏时自动停表 —— 后台跑一个 25fps 的动画没有任何意义，
    笔记本上还挺费电。
    """

    sceneChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, star_count: int = 760):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)   # 全屏重绘，不需要擦背景
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.scene = SpaceScene(star_count=star_count)
        self._last_scene = self.scene.scene_name

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._on_tick)
        self._running = True
        self._timer.start()

    # ---- 控制
    def set_running(self, running: bool) -> None:
        self._running = running
        if running:
            self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def is_running(self) -> bool:
        return self._running

    def _on_tick(self) -> None:
        self.scene.tick(FRAME_MS / 1000.0)
        if self.scene.scene_name != self._last_scene:
            self._last_scene = self.scene.scene_name
            self.sceneChanged.emit(self._last_scene)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        self.scene.paint(painter, self.width(), self.height())
        painter.end()

    # 窗口最小化时停表；恢复时接着跑
    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        if self._running:
            self._timer.start()
        super().showEvent(event)
