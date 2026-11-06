"""频谱查看器：整首完整瀑布图 + 平均频谱 + 判定依据。

为什么值得单独做一个窗口
------------------------
「假无损」三个字本身没有说服力 —— 用户看到 1.4GB 的 FLAC 被判假，第一反应
一定是"软件误判"。而频谱图是有说服力的：截止频率那里一条悬崖，任何人一眼
就能看懂这是有损转码留下的痕迹。

主视图是 **Spek 式完整瀑布图**（横轴整首时间、纵轴频率、颜色表示能量），
不再只取几个采样点的平均 —— 前奏静音、结尾淡出、中途的频段变化都能一眼看到。
平均频谱作为第二个页签保留，看截止位置更直观。

V1.5.3 起两个视图都支持查看缩放：滚轮缩放、左键拖动平移、双击复位，
完整频谱另支持 Ctrl+滚轮只缩放频率、Shift+滚轮只缩放时间。

配色只影响**显示**：鉴定结论来自 `core/authenticity.py` 在 dB 域算出的
数值（截止、断崖落差、扩展带能量等），与本文件的 `_LUT` / 主题色无关。
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.authenticity import (
    VERDICT_COLORS,
    AuthenticityResult,
    analyze,
    full_spectrum,
)
from ..core.model import Track
from . import i18n, theme
from .widgets import Card, Pill

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 颜色映射

#: Spek 风格色阶：黑 → 深蓝 → 蓝 → 青 → 绿 → 黄 → 橙 → 白
_COLOR_STOPS = [
    (0.00, (0, 0, 0)),
    (0.12, (8, 16, 48)),
    (0.30, (18, 60, 140)),
    (0.48, (20, 140, 160)),
    (0.65, (40, 190, 120)),
    (0.80, (220, 200, 60)),
    (0.92, (250, 140, 50)),
    (1.00, (255, 255, 255)),
]

#: dB 显示范围
_DB_FLOOR = -100.0
_DB_CEIL = 0.0


def _build_lut() -> np.ndarray:
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for k in range(len(_COLOR_STOPS) - 1):
            t0, c0 = _COLOR_STOPS[k]
            t1, c1 = _COLOR_STOPS[k + 1]
            if t0 <= t <= t1:
                span = max(1e-9, t1 - t0)
                f = (t - t0) / span
                lut[i] = [
                    int(c0[0] + (c1[0] - c0[0]) * f),
                    int(c0[1] + (c1[1] - c0[1]) * f),
                    int(c0[2] + (c1[2] - c0[2]) * f),
                ]
                break
    return lut


_LUT = _build_lut()


def _pool_rows(mat: np.ndarray, target: int) -> np.ndarray:
    """把频率方向池化到 target 行（每组取最大值，保留弱信号细节）。"""
    bins = mat.shape[0]
    if bins <= target:
        return mat
    edges = np.linspace(0, bins, target + 1).astype(int)
    out = np.empty((target, mat.shape[1]), dtype=mat.dtype)
    for i in range(target):
        lo = edges[i]
        hi = max(edges[i] + 1, edges[i + 1])
        out[i] = mat[lo:hi].max(axis=0)
    return out


def _step_for(span: float, candidates: tuple[float, ...], target: int = 5) -> float:
    """按目标刻度数从「好看的步长」里挑一个。"""
    for candidate in candidates:
        if span / candidate <= target:
            return candidate
    return candidates[-1]


def _freq_ticks_between(lo: float, hi: float) -> list[float]:
    """视口内的频率刻度（自适应，缩放后仍保持 4~6 条）。"""
    span = hi - lo
    if span <= 0:
        return []
    if span <= 2000:
        candidates = (50.0, 100.0, 200.0, 250.0, 500.0, 1000.0)
    elif span <= 10000:
        candidates = (500.0, 1000.0, 2000.0, 2500.0, 5000.0)
    elif span <= 50000:
        candidates = (1000.0, 2000.0, 5000.0, 10000.0)
    else:
        candidates = (5000.0, 10000.0, 20000.0, 50000.0)
    step = _step_for(span, candidates)
    value = math.ceil(lo / step) * step
    ticks: list[float] = []
    while value <= hi + 1e-6:
        ticks.append(value)
        value += step
    return ticks


def _time_ticks_between(lo: float, hi: float) -> list[float]:
    span = hi - lo
    if span <= 0:
        return []
    step = _step_for(
        span, (0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0)
    )
    value = math.ceil(lo / step) * step
    ticks: list[float] = []
    while value <= hi + 1e-6:
        ticks.append(value)
        value += step
    return ticks


def _db_ticks_between(top: float, bottom: float) -> list[float]:
    span = top - bottom
    if span <= 0:
        return []
    step = _step_for(span, (5.0, 10.0, 20.0, 25.0, 50.0))
    value = math.ceil(bottom / step) * step
    ticks: list[float] = []
    while value <= top + 1e-6:
        ticks.append(value)
        value += step
    return ticks


def _format_hz(hz: float, step: float) -> str:
    if hz <= 0:
        return "0"
    if hz >= 1000:
        if step < 1000:
            return f"{hz / 1000:.1f}k"
        return f"{hz / 1000:.0f}k"
    return f"{hz:.0f}"


def _format_time(seconds: float, step: float) -> str:
    seconds = max(0.0, seconds)
    if step < 1:
        return f"{int(seconds // 60)}:{seconds % 60:04.1f}"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _format_db(value: float) -> str:
    if abs(value) < 0.5:
        return "0dB"
    return f"{value:+.0f}dB"


def _zoom_axis(view: list[float], anchor: float, factor: float, minimum: float) -> None:
    """围绕 anchor（0~1）缩放 [lo, hi]，并保证不越出 [0, 1]。"""
    span = view[1] - view[0]
    new_span = min(1.0, max(minimum, span * factor))
    lo = view[0] + anchor * span - anchor * new_span
    lo = min(max(0.0, lo), 1.0 - new_span)
    view[0] = lo
    view[1] = lo + new_span


def _pan_axis(view: list[float], delta: float) -> None:
    span = view[1] - view[0]
    lo = min(max(0.0, view[0] + delta), 1.0 - span)
    view[0] = lo
    view[1] = lo + span


class SpectrogramCanvas(QWidget):
    """完整频谱瀑布图：横轴整首时间、纵轴频率、颜色表示能量。"""

    viewChanged = Signal()

    #: 视口跨度下限（占整轴的比例），即最大放大倍数
    _MIN_TIME_SPAN = 0.01
    _MIN_FREQ_SPAN = 0.01

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(300)
        self.setCursor(Qt.OpenHandCursor)
        self._image: Optional[QImage] = None
        self._rgb: Optional[np.ndarray] = None   # 保持引用，防止 QImage 悬垂
        self._db: Optional[np.ndarray] = None    # 原始 (frames, bins)
        self._freqs: Optional[np.ndarray] = None
        self._nyquist = 22050.0
        self._duration = 0.0
        self._cutoff = 0.0
        self._is_dsd = False
        self._message = i18n.tr_text("正在解码整首音频…")
        self._view_t = [0.0, 1.0]
        self._view_f = [0.0, 1.0]
        self._drag: Optional[QPointF] = None

    def set_message(self, text: str) -> None:
        self._message = text
        self._image = None
        self._db = None
        self._freqs = None
        self.update()

    def set_data(
        self,
        db: np.ndarray,
        freqs: np.ndarray,
        sample_rate: int,
        *,
        duration: float = 0.0,
        cutoff: float = 0.0,
        is_dsd: bool = False,
    ) -> None:
        if db is None or freqs is None or db.size == 0:
            self.set_message(i18n.tr_text("无法取得频谱数据"))
            return
        db = np.asarray(db, dtype=np.float32)
        freqs = np.asarray(freqs, dtype=np.float64)
        if db.ndim != 2 or db.shape[1] != freqs.size:
            self.set_message(i18n.tr_text("频谱数据格式异常"))
            return

        self._db = db
        self._freqs = freqs
        self._nyquist = float(freqs[-1]) if freqs.size else (sample_rate / 2.0)
        self._cutoff = float(cutoff)
        self._is_dsd = bool(is_dsd)
        self._duration = float(duration or 0.0)
        self._view_t = [0.0, 1.0]
        self._view_f = [0.0, 1.0]
        self._message = ""
        self._rebuild()

    # ------------------------------------------------------------ 视口

    def _plot_rect(self) -> QRectF:
        rect = QRectF(self.rect().adjusted(0, 0, -1, -1))
        return QRectF(
            rect.left() + 56,
            rect.top() + 14,
            rect.width() - 72,
            rect.height() - 42,
        )

    def _rebuild(self) -> None:
        """按当前视口从原始矩阵重建图像（缩放/平移/尺寸变化时调用）。"""
        if self._db is None:
            return
        frames_n, bins_n = self._db.shape
        t0, t1 = self._view_t
        f0, f1 = self._view_f
        r0 = int(t0 * frames_n)
        r1 = max(r0 + 2, int(math.ceil(t1 * frames_n)))
        b0 = int(f0 * bins_n)
        b1 = max(b0 + 2, int(math.ceil(f1 * bins_n)))
        mat = self._db[r0:r1, b0:b1].T

        height = max(120, min(900, int(self._plot_rect().height())))
        mat = _pool_rows(mat, height)

        width = max(200, min(2000, int(self._plot_rect().width())))
        if mat.shape[1] > width:
            step = mat.shape[1] / width
            idx = np.clip((np.arange(width) * step).astype(int), 0, mat.shape[1] - 1)
            mat = mat[:, idx]

        norm = (mat - _DB_FLOOR) / (_DB_CEIL - _DB_FLOOR)
        idxs = np.clip((norm * 255.0).astype(np.int32), 0, 255)
        rgb = _LUT[idxs]                    # (height, width, 3)
        rgb = np.flipud(rgb)                # 高频在上
        rgb = np.ascontiguousarray(rgb)
        self._rgb = rgb
        h, w = rgb.shape[:2]
        self._image = QImage(rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rebuild()

    def view_text(self) -> str:
        t0, t1 = self._view_t[0] * self._duration, self._view_t[1] * self._duration
        f0, f1 = self._view_f[0] * self._nyquist, self._view_f[1] * self._nyquist
        return (
            f"{_format_time(t0, 1)}–{_format_time(t1, 1)}  |  "
            f"{_format_hz(f0, 1)}–{_format_hz(f1, 1)}"
        )

    def is_zoomed(self) -> bool:
        return (
            self._db is not None
            and (self._view_t[1] - self._view_t[0] < 0.999
                 or self._view_f[1] - self._view_f[0] < 0.999)
        )

    def has_data(self) -> bool:
        return self._db is not None

    def zoom_in(self) -> None:
        self._apply_zoom(0.5, 0.5, 0.75)

    def zoom_out(self) -> None:
        self._apply_zoom(0.5, 0.5, 1.35)

    def reset_view(self) -> None:
        if self._db is None:
            return
        self._view_t = [0.0, 1.0]
        self._view_f = [0.0, 1.0]
        self._rebuild()
        self.viewChanged.emit()

    def _apply_zoom(
        self, anchor_x: float, anchor_y: float, factor: float,
        *, zoom_time: bool = True, zoom_freq: bool = True,
    ) -> None:
        if self._db is None:
            return
        if zoom_time:
            _zoom_axis(self._view_t, anchor_x, factor, self._MIN_TIME_SPAN)
        if zoom_freq:
            _zoom_axis(self._view_f, anchor_y, factor, self._MIN_FREQ_SPAN)
        self._rebuild()
        self.viewChanged.emit()

    def _pan(self, dx: float, dy: float) -> None:
        """拖动平移：图像跟着鼠标走，所以时间轴反向、频率轴正向。"""
        if self._db is None:
            return
        _pan_axis(self._view_t, -dx * (self._view_t[1] - self._view_t[0]))
        _pan_axis(self._view_f, dy * (self._view_f[1] - self._view_f[0]))
        self._rebuild()
        self.viewChanged.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._db is None:
            return
        plot = self._plot_rect()
        pos = event.position()
        if not plot.contains(pos) or not event.angleDelta().y():
            event.ignore()
            return
        factor = 0.8 if event.angleDelta().y() > 0 else 1.25
        anchor_x = (pos.x() - plot.left()) / max(1.0, plot.width())
        anchor_y = 1.0 - (pos.y() - plot.top()) / max(1.0, plot.height())
        modifiers = event.modifiers()
        self._apply_zoom(
            anchor_x,
            anchor_y,
            factor,
            zoom_time=not bool(modifiers & Qt.ControlModifier),
            zoom_freq=not bool(modifiers & Qt.ShiftModifier),
        )
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and self._db is not None
            and self._plot_rect().contains(event.position())
        ):
            self._drag = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            return
        pos = event.position()
        plot = self._plot_rect()
        dx = (pos.x() - self._drag.x()) / max(1.0, plot.width())
        dy = (pos.y() - self._drag.y()) / max(1.0, plot.height())
        self._drag = pos
        self._pan(dx, dy)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._drag is not None:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._db is not None:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1))
        painter.setBrush(QColor(6, 9, 14, 200))
        painter.drawRoundedRect(QRectF(rect), 10, 10)

        plot = self._plot_rect()

        if self._image is None:
            painter.setPen(QColor(theme.FG_MUTE))
            painter.setFont(QFont(self.font()))
            painter.drawText(rect, Qt.AlignCenter, self._message)
            painter.end()
            return

        painter.setClipRect(plot)
        painter.drawImage(plot, self._image)
        painter.setClipping(False)

        self._paint_axes(painter, plot)
        self._paint_cutoff(painter, plot)
        painter.end()

    def _paint_axes(self, painter: QPainter, plot: QRectF) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)

        nyq = max(1.0, self._nyquist)
        f_lo, f_hi = self._view_f[0] * nyq, self._view_f[1] * nyq
        f_span = max(1.0, f_hi - f_lo)
        freq_ticks = _freq_ticks_between(f_lo, f_hi)
        freq_step = freq_ticks[1] - freq_ticks[0] if len(freq_ticks) >= 2 else f_span
        for hz in freq_ticks:
            y = plot.bottom() - ((hz - f_lo) / f_span) * plot.height()
            painter.setPen(QPen(QColor(255, 255, 255, 46), 1, Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.FG_MUTE))
            painter.drawText(
                QRectF(plot.left() - 50, y - 7, 44, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                _format_hz(hz, freq_step),
            )

        t0, t1 = self._view_t[0] * self._duration, self._view_t[1] * self._duration
        t_span = max(1e-6, t1 - t0)
        time_ticks = _time_ticks_between(t0, t1)
        time_step = time_ticks[1] - time_ticks[0] if len(time_ticks) >= 2 else t_span
        for sec in time_ticks:
            x = plot.left() + ((sec - t0) / t_span) * plot.width()
            painter.setPen(QPen(QColor(255, 255, 255, 46), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(theme.FG_MUTE))
            painter.drawText(
                QRectF(x - 30, plot.bottom() + 5, 60, 14),
                Qt.AlignCenter,
                _format_time(sec, time_step),
            )

    def _paint_cutoff(self, painter: QPainter, plot: QRectF) -> None:
        if self._cutoff <= 0:
            return
        nyq = max(1.0, self._nyquist)
        f_lo, f_hi = self._view_f[0] * nyq, self._view_f[1] * nyq
        if not (f_lo <= self._cutoff <= f_hi):
            return
        y = plot.bottom() - (
            (self._cutoff - f_lo) / max(1e-6, f_hi - f_lo)
        ) * plot.height()

        color = QColor(theme.WARN)
        painter.setPen(QPen(color, 1.4, Qt.DashLine))
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        label = i18n.tr_format(
            "截止 {value} kHz", value=f"{self._cutoff / 1000:.1f}"
        )
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 12
        box = QRectF(plot.left() + 6, y - 19, width, 17)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(30, 24, 10, 220))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(color)
        painter.drawText(box, Qt.AlignCenter, label)


class SpectrumCanvas(QWidget):
    """平均频谱曲线：对数感更强的相对 dB 曲线 + 截止标记。"""

    viewChanged = Signal()

    _MIN_FREQ_SPAN = 0.01
    _MIN_DB_SPAN = 10.0
    _MAX_DB_SPAN = 113.0

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setCursor(Qt.OpenHandCursor)
        self._freqs: Optional[np.ndarray] = None
        self._db: Optional[np.ndarray] = None
        self._relative: Optional[np.ndarray] = None
        self._cutoff = 0.0
        self._nyquist = 22050.0
        self._is_dsd = False
        self._message = i18n.tr_text("正在分析…")
        self._view_hz = [0.0, 22050.0]
        self._view_db = [8.0, -105.0]
        self._drag: Optional[QPointF] = None

    def set_result(self, result: AuthenticityResult) -> None:
        spectrum = getattr(result, "spectrum", None)
        if not spectrum:
            self._message = (
                i18n.tr_text(result.error) if result.error
                else i18n.tr_text("无法取得频谱数据")
            )
            self._freqs = None
            self._db = None
            self._relative = None
            self.update()
            return
        freqs, db = spectrum
        self._freqs = np.asarray(freqs, dtype=np.float64)
        self._db = np.asarray(db, dtype=np.float64)
        # 转成「相对音乐主体」的 dB：绝对值没有参照，相对值才看得懂
        reference_mask = (self._freqs >= 300) & (self._freqs <= 3000)
        reference = (
            float(np.median(self._db[reference_mask]))
            if np.any(reference_mask)
            else float(np.max(self._db))
        )
        self._relative = self._db - reference
        self._cutoff = float(result.cutoff_hz)
        self._nyquist = float(result.nyquist_hz or 22050)
        self._is_dsd = result.verdict.endswith("DSD") or "DSD" in result.verdict
        self._message = ""
        self.reset_view()

    def set_message(self, text: str) -> None:
        self._message = text
        self._freqs = None
        self._db = None
        self._relative = None
        self.update()

    # ------------------------------------------------------------ 视口

    def _plot_rect(self) -> QRectF:
        rect = QRectF(self.rect().adjusted(0, 0, -1, -1))
        return QRectF(
            rect.left() + 46,
            rect.top() + 14,
            rect.width() - 62,
            rect.height() - 42,
        )

    def reset_view(self) -> None:
        self._view_hz = [0.0, max(1.0, self._nyquist)]
        self._view_db = [8.0, -105.0]
        self.update()
        self.viewChanged.emit()

    def is_zoomed(self) -> bool:
        return (
            self._relative is not None
            and (self._view_hz[1] - self._view_hz[0] < self._nyquist * 0.999
                 or self._view_db[0] - self._view_db[1] < self._MAX_DB_SPAN * 0.999)
        )

    def has_data(self) -> bool:
        return self._relative is not None

    def view_text(self) -> str:
        f0, f1 = self._view_hz
        top, bottom = self._view_db
        return (
            f"{_format_hz(f0, 1)}–{_format_hz(f1, 1)}  |  "
            f"{top:.0f}…{bottom:.0f} dB"
        )

    def zoom_in(self) -> None:
        self._apply_zoom(0.5, 0.5, 0.75)

    def zoom_out(self) -> None:
        self._apply_zoom(0.5, 0.5, 1.35)

    def _apply_zoom(
        self, anchor_x: float, anchor_y: float, factor: float,
        *, zoom_freq: bool = True, zoom_db: bool = True,
    ) -> None:
        if self._relative is None:
            return
        nyq = max(1.0, self._nyquist)
        if zoom_freq:
            span = self._view_hz[1] - self._view_hz[0]
            new_span = min(nyq, max(nyq * self._MIN_FREQ_SPAN, span * factor))
            lo = self._view_hz[0] + anchor_x * span - anchor_x * new_span
            lo = min(max(0.0, lo), nyq - new_span)
            self._view_hz = [lo, lo + new_span]
        if zoom_db:
            top, bottom = self._view_db
            span = top - bottom
            new_span = min(self._MAX_DB_SPAN, max(self._MIN_DB_SPAN, span * factor))
            top_new = top - anchor_y * span + anchor_y * new_span
            top_new = min(8.0, max(-105.0 + new_span, top_new))
            self._view_db = [top_new, top_new - new_span]
        self.update()
        self.viewChanged.emit()

    def _pan(self, dx: float, dy: float) -> None:
        if self._relative is None:
            return
        nyq = max(1.0, self._nyquist)
        span = self._view_hz[1] - self._view_hz[0]
        lo = min(max(0.0, self._view_hz[0] - dx * span), nyq - span)
        self._view_hz = [lo, lo + span]
        span_db = self._view_db[0] - self._view_db[1]
        top = min(8.0, max(-105.0 + span_db, self._view_db[0] + dy * span_db))
        self._view_db = [top, top - span_db]
        self.update()
        self.viewChanged.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._relative is None:
            return
        plot = self._plot_rect()
        pos = event.position()
        if not plot.contains(pos) or not event.angleDelta().y():
            event.ignore()
            return
        factor = 0.8 if event.angleDelta().y() > 0 else 1.25
        anchor_x = (pos.x() - plot.left()) / max(1.0, plot.width())
        anchor_y = (pos.y() - plot.top()) / max(1.0, plot.height())
        modifiers = event.modifiers()
        self._apply_zoom(
            anchor_x,
            anchor_y,
            factor,
            zoom_freq=not bool(modifiers & Qt.ShiftModifier),
            zoom_db=not bool(modifiers & Qt.ControlModifier),
        )
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and self._relative is not None
            and self._plot_rect().contains(event.position())
        ):
            self._drag = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            return
        pos = event.position()
        plot = self._plot_rect()
        dx = (pos.x() - self._drag.x()) / max(1.0, plot.width())
        dy = (pos.y() - self._drag.y()) / max(1.0, plot.height())
        self._drag = pos
        self._pan(dx, dy)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._drag is not None:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._relative is not None:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1))
        painter.setBrush(QColor(6, 9, 14, 190))
        painter.drawRoundedRect(QRectF(rect), 10, 10)

        plot = self._plot_rect()

        if self._relative is None or self._freqs is None:
            painter.setPen(QColor(theme.FG_MUTE))
            painter.setFont(QFont(self.font()))
            painter.drawText(rect, Qt.AlignCenter, self._message)
            painter.end()
            return

        self._paint_grid(painter, plot)
        self._paint_curve(painter, plot)
        self._paint_marker(painter, plot)
        painter.end()

    def _paint_grid(self, painter: QPainter, plot: QRectF) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)

        for db in _db_ticks_between(self._view_db[0], self._view_db[1]):
            y = self._db_to_y(db, plot)
            if plot.top() <= y <= plot.bottom():
                painter.setPen(QPen(QColor(theme.LINE_SOFT), 1, Qt.DotLine))
                painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                painter.setPen(QColor(theme.FG_MUTE))
                painter.drawText(
                    QRectF(plot.left() - 42, y - 7, 36, 14),
                    Qt.AlignRight | Qt.AlignVCenter,
                    _format_db(db),
                )

        freq_ticks = _freq_ticks_between(self._view_hz[0], self._view_hz[1])
        freq_step = (
            freq_ticks[1] - freq_ticks[0]
            if len(freq_ticks) >= 2
            else max(1.0, self._view_hz[1] - self._view_hz[0])
        )
        for hz in freq_ticks:
            x = self._hz_to_x(hz, plot)
            painter.setPen(QPen(QColor(theme.LINE_SOFT), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(theme.FG_MUTE))
            painter.drawText(
                QRectF(x - 26, plot.bottom() + 4, 52, 14),
                Qt.AlignCenter,
                _format_hz(hz, freq_step),
            )

    def _paint_curve(self, painter: QPainter, plot: QRectF) -> None:
        freqs = self._freqs
        rel = self._relative
        assert freqs is not None and rel is not None

        visible = (freqs >= self._view_hz[0]) & (freqs <= self._view_hz[1])
        if not np.any(visible):
            return
        freqs = freqs[visible]
        rel = rel[visible]

        # 抽稀到 ~800 点：画 8 万个点既慢又看不出差别
        limit = 800
        if freqs.size > limit:
            step = max(1, freqs.size // limit)
            freqs = freqs[::step]
            rel = rel[::step]

        points = [
            QPointF(self._hz_to_x(float(f), plot), self._db_to_y(float(v), plot))
            for f, v in zip(freqs, rel)
        ]
        if not points:
            return

        # 曲线下方填充：渐变收口，视觉上像"能量体"
        path = QPainterPath(QPointF(points[0].x(), plot.bottom()))
        for point in points:
            path.lineTo(point)
        path.lineTo(QPointF(points[-1].x(), plot.bottom()))
        path.closeSubpath()

        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top = QColor(theme.ACCENT)
        top.setAlpha(120)
        bottom = QColor(theme.ACCENT)
        bottom.setAlpha(6)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(path)

        pen = QPen(QColor(theme.CYAN), 1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(QPolygonF(points))

    def _paint_marker(self, painter: QPainter, plot: QRectF) -> None:
        if self._cutoff <= 0:
            return
        if not (self._view_hz[0] <= self._cutoff <= self._view_hz[1]):
            return
        x = self._hz_to_x(self._cutoff, plot)

        color = QColor(theme.WARN)
        pen = QPen(color, 1.2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        label = i18n.tr_format(
            "截止 {value} kHz", value=f"{self._cutoff / 1000:.1f}"
        )
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 12
        box = QRectF(min(x + 6, plot.right() - width), plot.top() + 4, width, 17)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(30, 24, 10, 220))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(color)
        painter.drawText(box, Qt.AlignCenter, label)

    # ---- 坐标变换
    def _hz_to_x(self, hz: float, plot: QRectF) -> float:
        lo, hi = self._view_hz
        span = max(1e-6, hi - lo)
        value = min(max(hz, lo), hi)
        return plot.left() + ((value - lo) / span) * plot.width()

    def _db_to_y(self, db: float, plot: QRectF) -> float:
        top, bottom = self._view_db
        span = max(1e-6, top - bottom)
        ratio = (top - max(bottom, min(top, db))) / span
        return plot.top() + ratio * plot.height()


class _AnalyzeWorker(QThread):
    """重新解码：拿判定结果 + 整首完整频谱。"""

    done = Signal(object, object)

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.track = track

    def run(self) -> None:
        result: AuthenticityResult
        try:
            result = analyze(self.track.path, self.track.info)
        except Exception as exc:  # noqa: BLE001
            log.exception("频谱分析失败")
            result = AuthenticityResult(
                error=i18n.tr_format("分析失败：{message}", message=str(exc))
            )

        spectrogram = None
        if not result.error:
            try:
                spectrogram = full_spectrum(self.track.path, self.track.info)
            except Exception:  # noqa: BLE001
                log.exception("完整频谱提取失败")
        self.done.emit(result, spectrogram)


class SpectrumDialog(QDialog):
    """单个文件的完整频谱、平均频谱与判定详情。"""

    def __init__(self, track: Track, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.track = track
        self._worker: Optional[_AnalyzeWorker] = None
        self.setWindowTitle(f"频谱分析 — {track.path.name}")
        self.resize(980, 700)
        self.setMinimumSize(760, 580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        # ---- 头部：文件名 + 判定 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel(track.path.name)
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.verdict_pill = Pill(
            (
                i18n.tr_verdict(track.authenticity.verdict)
                if track.authenticity
                else i18n.tr_text("分析中")
            ),
            (VERDICT_COLORS.get(track.authenticity.verdict, theme.FG_DIM)
             if track.authenticity else theme.FG_DIM),
        )
        head.addWidget(self.verdict_pill)
        layout.addLayout(head)

        info = QLabel(
            f"{track.info.fmt} · {track.info.spec_text} · {track.info.duration_text} · "
            f"{track.info.file_size / 1024 / 1024:.0f} MB"
        )
        info.setObjectName("hint")
        layout.addWidget(info)

        # ---- 缩放工具条 ----
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(8)
        self.zoom_out_btn = QPushButton(i18n.tr_text("缩小"))
        self.zoom_in_btn = QPushButton(i18n.tr_text("放大"))
        self.reset_btn = QPushButton(i18n.tr_text("复位"))
        for button in (self.zoom_out_btn, self.zoom_in_btn, self.reset_btn):
            button.setObjectName("ghost")
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(28)
            zoom_row.addWidget(button)
        hint = QLabel(i18n.tr_text("滚轮缩放 · 拖动平移 · 双击复位"))
        hint.setObjectName("hint")
        zoom_row.addWidget(hint)
        zoom_row.addStretch(1)
        self.view_label = QLabel("")
        self.view_label.setObjectName("hint")
        zoom_row.addWidget(self.view_label)
        layout.addLayout(zoom_row)

        self.zoom_out_btn.clicked.connect(lambda: self._active_canvas().zoom_out())
        self.zoom_in_btn.clicked.connect(lambda: self._active_canvas().zoom_in())
        self.reset_btn.clicked.connect(lambda: self._active_canvas().reset_view())

        # ---- 双视图：完整瀑布图 / 平均频谱 ----
        self.tabs = QTabWidget()
        self.spectrogram = SpectrogramCanvas()
        self.curve = SpectrumCanvas()
        self.tabs.addTab(self.spectrogram, i18n.tr_text("完整频谱"))
        self.tabs.addTab(self.curve, i18n.tr_text("平均频谱"))
        self.tabs.setTabToolTip(0, i18n.tr_text("滚轮缩放 · 拖动平移 · 双击复位"))
        self.tabs.setTabToolTip(1, i18n.tr_text("滚轮缩放 · 拖动平移 · 双击复位"))
        self.tabs.currentChanged.connect(self._sync_zoom_ui)
        self.spectrogram.viewChanged.connect(self._sync_zoom_ui)
        self.curve.viewChanged.connect(self._sync_zoom_ui)
        layout.addWidget(self.tabs, 1)

        self.reason_card = Card(i18n.tr_text("判定依据"), "", theme.CYAN)
        self.reason_label = QLabel(i18n.tr_text("正在解码分析…"))
        self.reason_label.setWordWrap(True)
        self.reason_label.setObjectName("hint")
        self.reason_card.add_widget(self.reason_label)
        layout.addWidget(self.reason_card)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn = buttons.button(QDialogButtonBox.Close)
        close_btn.setText(i18n.tr_text("关闭"))
        close_btn.setObjectName("ghost")
        close_btn.setCursor(Qt.PointingHandCursor)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_zoom_ui()
        self._start()

    def _active_canvas(self):
        return self.curve if self.tabs.currentIndex() == 1 else self.spectrogram

    def _sync_zoom_ui(self) -> None:
        canvas = self._active_canvas()
        has_data = canvas.has_data()
        self.zoom_in_btn.setEnabled(has_data)
        self.zoom_out_btn.setEnabled(has_data and canvas.is_zoomed())
        self.reset_btn.setEnabled(has_data and canvas.is_zoomed())
        self.view_label.setText(canvas.view_text() if has_data else "")

    def _start(self) -> None:
        self._worker = _AnalyzeWorker(self.track, self)
        self._worker.done.connect(self._apply)
        self._worker.start()

    def _apply(self, result: AuthenticityResult, spectrogram) -> None:
        self.curve.set_result(result)

        if spectrogram:
            db, freqs, sample_rate = spectrogram
            self.spectrogram.set_data(
                db,
                freqs,
                sample_rate,
                duration=self.track.info.duration or 0.0,
                cutoff=result.cutoff_hz,
                is_dsd="DSD" in result.verdict,
            )
        else:
            self.spectrogram.set_message(
                i18n.tr_text("无法生成完整频谱（解码失败或文件过长）")
            )

        color = VERDICT_COLORS.get(result.verdict, theme.FG_DIM)
        self.verdict_pill.set_text(i18n.tr_verdict(result.verdict), color)
        if result.error and not result.reasons:
            self.reason_label.setText(i18n.tr_text(result.error))
        else:
            if i18n.current() in ("zh_CN", "zh_TW"):
                lines = [f"· {r}" for r in result.reasons] or [i18n.tr_text("（无更多依据）")]
            else:
                # 判定依据是内核生成的中文长句；非中文界面显示结构化摘要，
                # 避免整段中文混进译文界面
                detail = i18n.tr_text(result.detail_text)
                lines = [f"· {detail}"] if detail else []
            lines.append("")
            lines.append(
                i18n.tr_format(
                    "判定分数 {score} · 分析时长 {seconds}s · 采样 {rate}kHz",
                    score=f"{result.score:.2f}",
                    seconds=f"{result.analyzed_seconds:.0f}",
                    rate=f"{result.nyquist_hz * 2 / 1000:.1f}",
                )
            )
            self.reason_label.setText("\n".join(lines))

        self._sync_zoom_ui()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(2000)
        super().closeEvent(event)


def open_spectrum(track: Track, parent: Optional[QWidget] = None) -> None:
    """打开频谱窗口（供右键菜单直接调用）。"""
    dialog = SpectrumDialog(track, parent)
    dialog.exec()
