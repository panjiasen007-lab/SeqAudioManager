"""频谱查看器：把判定依据摊开给用户看。

为什么值得单独做一个窗口
------------------------
「假无损」三个字本身没有说服力 —— 用户看到 1.4GB 的 FLAC 被判假，第一反应
一定是"软件误判"。而频谱图是有说服力的：截止频率那里一条悬崖，任何人一眼
就能看懂这是有损转码留下的痕迹。

所以这个窗口只做一件事：把那一段频谱画清楚，并把判定理由逐条列出。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
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
    QVBoxLayout,
    QWidget,
)

from ..core.authenticity import (
    VERDICT_COLORS,
    AuthenticityResult,
    analyze,
)
from ..core.model import Track
from . import theme
from .widgets import Card, Pill

log = logging.getLogger(__name__)


class _AnalyzeWorker(QThread):
    """重新解码分析一次，拿到频谱数据。

    不直接复用列表里的结果：判定结果里只存了"最差那一段"的统计量，
    但用户真正想看的是曲线，必须重新解码（约 0.3~1 秒）。
    """

    done = Signal(object)

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.track = track

    def run(self) -> None:
        try:
            result = analyze(self.track.path, self.track.info)
        except Exception as exc:  # noqa: BLE001
            log.exception("频谱分析失败")
            result = AuthenticityResult(error=f"分析失败：{exc}")
        self.done.emit(result)


class SpectrumCanvas(QWidget):
    """纯 QPainter 频谱图：对数感更强的相对 dB 曲线 + 截止标记。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self._freqs: Optional[np.ndarray] = None
        self._db: Optional[np.ndarray] = None
        self._relative: Optional[np.ndarray] = None
        self._cutoff = 0.0
        self._nyquist = 22050.0
        self._is_dsd = False
        self._message = "正在分析…"

    def set_result(self, result: AuthenticityResult) -> None:
        spectrum = getattr(result, "spectrum", None)
        if not spectrum:
            self._message = result.error or "无法取得频谱数据"
            self._freqs = None
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
        self._is_dsd = result.verdict.endswith("DSD") or "DSD" in result.reasons[0:1]
        self._message = ""
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1))
        painter.setBrush(QColor(6, 9, 14, 190))
        painter.drawRoundedRect(QRectF(rect), 10, 10)

        plot = QRectF(rect.left() + 46, rect.top() + 14, rect.width() - 62, rect.height() - 42)

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

        # 纵向：每 20dB 一条
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1, Qt.DotLine))
        for db in (0, -20, -40, -60, -80, -100):
            y = self._db_to_y(db, plot)
            if plot.top() <= y <= plot.bottom():
                painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                painter.setPen(QColor(theme.FG_MUTE))
                painter.drawText(QRectF(plot.left() - 42, y - 7, 36, 14),
                                 Qt.AlignRight | Qt.AlignVCenter, f"{db}dB")
                painter.setPen(QPen(QColor(theme.LINE_SOFT), 1, Qt.DotLine))

        # 横向：按奈奎斯特频率自适应挑刻度
        for hz in self._tick_frequencies():
            x = self._hz_to_x(hz, plot)
            painter.setPen(QPen(QColor(theme.LINE_SOFT), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(theme.FG_MUTE))
            label = f"{hz / 1000:.0f}k" if hz >= 1000 else f"{hz:.0f}"
            painter.drawText(QRectF(x - 22, plot.bottom() + 4, 44, 14),
                             Qt.AlignCenter, label)

    def _tick_frequencies(self) -> list[float]:
        nyquist = self._nyquist
        if nyquist <= 24000:
            return [1000, 5000, 10000, 15000, 20000]
        if nyquist <= 50000:
            return [1000, 10000, 20000, 30000, 40000]
        step = 20000 if nyquist > 120000 else 10000
        ticks = [1000.0]
        value = step
        while value < nyquist:
            ticks.append(float(value))
            value += step
        return ticks

    def _paint_curve(self, painter: QPainter, plot: QRectF) -> None:
        freqs = self._freqs
        rel = self._relative
        assert freqs is not None and rel is not None

        # 抽稀到 ~640 点：画 8 万个点既慢又看不出差别
        limit = 640
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
        x = self._hz_to_x(self._cutoff, plot)
        if not (plot.left() <= x <= plot.right()):
            return

        color = QColor(theme.WARN)
        pen = QPen(color, 1.2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        label = f"截止 {self._cutoff / 1000:.1f} kHz"
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
        nyquist = max(1.0, self._nyquist)
        return plot.left() + (min(hz, nyquist) / nyquist) * plot.width()

    def _db_to_y(self, db: float, plot: QRectF) -> float:
        # 固定 -105 ~ +8 dB 的纵向范围，不同文件之间可直接横向比较
        top, bottom = 8.0, -105.0
        ratio = (top - max(bottom, min(top, db))) / (top - bottom)
        return plot.top() + ratio * plot.height()


class SpectrumDialog(QDialog):
    """单个文件的频谱与判定详情。"""

    def __init__(self, track: Track, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.track = track
        self._worker: Optional[_AnalyzeWorker] = None
        self.setWindowTitle(f"频谱分析 — {track.path.name}")
        self.resize(880, 620)
        self.setMinimumSize(720, 520)

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
            (track.authenticity.verdict if track.authenticity else "分析中"),
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

        self.canvas = SpectrumCanvas()
        layout.addWidget(self.canvas, 1)

        self.reason_card = Card("判定依据", "", theme.CYAN)
        self.reason_label = QLabel("正在解码分析…")
        self.reason_label.setWordWrap(True)
        self.reason_label.setObjectName("hint")
        self.reason_card.add_widget(self.reason_label)
        layout.addWidget(self.reason_card)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn = buttons.button(QDialogButtonBox.Close)
        close_btn.setText("关闭")
        close_btn.setObjectName("ghost")
        close_btn.setCursor(Qt.PointingHandCursor)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 已有结果就直接画（不必重新解码），否则异步分析
        existing = track.authenticity
        if existing is not None and getattr(existing, "spectrum", None):
            self._apply(existing)
        else:
            self._start()

    def _start(self) -> None:
        self._worker = _AnalyzeWorker(self.track, self)
        self._worker.done.connect(self._apply)
        self._worker.start()

    def _apply(self, result: AuthenticityResult) -> None:
        self.canvas.set_result(result)
        color = VERDICT_COLORS.get(result.verdict, theme.FG_DIM)
        self.verdict_pill.set_text(result.verdict, color)
        if result.error and not result.reasons:
            self.reason_label.setText(result.error)
        else:
            lines = [f"· {r}" for r in result.reasons] or ["（无更多依据）"]
            lines.append("")
            lines.append(
                f"判定分数 {result.score:.2f} · 分析时长 {result.analyzed_seconds:.0f}s · "
                f"采样 {result.nyquist_hz * 2 / 1000:.1f}kHz"
            )
            self.reason_label.setText("\n".join(lines))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(2000)
        super().closeEvent(event)


def open_spectrum(track: Track, parent: Optional[QWidget] = None) -> None:
    """打开频谱窗口（供右键菜单直接调用）。"""
    dialog = SpectrumDialog(track, parent)
    dialog.exec()
