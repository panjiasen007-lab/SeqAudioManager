"""频谱查看器：整首完整瀑布图 + 平均频谱 + 判定依据。

为什么值得单独做一个窗口
------------------------
「假无损」三个字本身没有说服力 —— 用户看到 1.4GB 的 FLAC 被判假，第一反应
一定是"软件误判"。而频谱图是有说服力的：截止频率那里一条悬崖，任何人一眼
就能看懂这是有损转码留下的痕迹。

主视图是 **Spek 式完整瀑布图**（横轴整首时间、纵轴频率、颜色表示能量），
不再只取几个采样点的平均 —— 前奏静音、结尾淡出、中途的频段变化都能一眼看到。
平均频谱作为第二个页签保留，看截止位置更直观。

配色只影响**显示**：鉴定结论来自 `core/authenticity.py` 在 dB 域算出的
数值（截止、断崖落差、扩展带能量等），与本文件的 `_LUT` / 主题色无关。
"""

from __future__ import annotations

import logging
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


def _freq_ticks(nyquist: float) -> list[float]:
    if nyquist <= 24000:
        return [1000, 5000, 10000, 15000, 20000]
    if nyquist <= 100000:
        return [1000, 10000, 20000, 40000, 60000, 80000]
    step = 20000.0 if nyquist <= 200000 else 50000.0
    ticks: list[float] = [1000.0]
    value = step
    while value < nyquist:
        ticks.append(value)
        value += step
    return ticks


def _time_ticks(duration: float) -> list[float]:
    if duration <= 0:
        return []
    # 目标 4~6 个刻度；短音频用 1/2/5 秒，长音频用分钟级
    candidates = (1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0)
    step = candidates[-1]
    for cand in candidates:
        if duration / cand <= 6:
            step = cand
            break
    ticks: list[float] = []
    value = 0.0
    while value <= duration + 1e-6:
        ticks.append(value)
        value += step
    return ticks


class SpectrogramCanvas(QWidget):
    """完整频谱瀑布图：横轴整首时间、纵轴频率、颜色表示能量。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(300)
        self._image: Optional[QImage] = None
        self._rgb: Optional[np.ndarray] = None   # 保持引用，防止 QImage 悬垂
        self._nyquist = 22050.0
        self._duration = 0.0
        self._cutoff = 0.0
        self._is_dsd = False
        self._message = i18n.tr_text("正在解码整首音频…")

    def set_message(self, text: str) -> None:
        self._message = text
        self._image = None
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

        self._nyquist = float(freqs[-1]) if freqs.size else (sample_rate / 2.0)
        self._cutoff = float(cutoff)
        self._is_dsd = bool(is_dsd)
        self._duration = float(duration or 0.0)

        # (frames, bins) → (bins, frames)
        mat = db.T
        height = max(140, min(480, self.height() - 44))
        mat = _pool_rows(mat, height)

        width = max(240, min(1600, self.width() - 76))
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
        self._message = ""
        self.update()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1))
        painter.setBrush(QColor(6, 9, 14, 200))
        painter.drawRoundedRect(QRectF(rect), 10, 10)

        plot = QRectF(rect.left() + 56, rect.top() + 14, rect.width() - 72, rect.height() - 42)

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
        for hz in _freq_ticks(nyq):
            if hz > nyq:
                continue
            y = plot.bottom() - (hz / nyq) * plot.height()
            painter.setPen(QPen(QColor(255, 255, 255, 46), 1, Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.FG_MUTE))
            label = f"{hz / 1000:.0f}k" if hz >= 1000 else f"{hz:.0f}"
            painter.drawText(
                QRectF(plot.left() - 50, y - 7, 44, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                label,
            )

        for sec in _time_ticks(self._duration):
            x = plot.left() + (sec / max(1e-9, self._duration)) * plot.width()
            painter.setPen(QPen(QColor(255, 255, 255, 46), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(theme.FG_MUTE))
            painter.drawText(
                QRectF(x - 28, plot.bottom() + 5, 56, 14),
                Qt.AlignCenter,
                f"{int(sec) // 60}:{int(sec) % 60:02d}",
            )

    def _paint_cutoff(self, painter: QPainter, plot: QRectF) -> None:
        if self._cutoff <= 0:
            return
        y = plot.bottom() - (self._cutoff / max(1.0, self._nyquist)) * plot.height()
        if not (plot.top() <= y <= plot.bottom()):
            return

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

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self._freqs: Optional[np.ndarray] = None
        self._db: Optional[np.ndarray] = None
        self._relative: Optional[np.ndarray] = None
        self._cutoff = 0.0
        self._nyquist = 22050.0
        self._is_dsd = False
        self._message = i18n.tr_text("正在分析…")

    def set_result(self, result: AuthenticityResult) -> None:
        spectrum = getattr(result, "spectrum", None)
        if not spectrum:
            self._message = (
                i18n.tr_text(result.error) if result.error
                else i18n.tr_text("无法取得频谱数据")
            )
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
        self._is_dsd = result.verdict.endswith("DSD") or "DSD" in result.verdict
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
        for hz in _freq_ticks(self._nyquist):
            if hz > self._nyquist:
                continue
            x = self._hz_to_x(hz, plot)
            painter.setPen(QPen(QColor(theme.LINE_SOFT), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(theme.FG_MUTE))
            label = f"{hz / 1000:.0f}k" if hz >= 1000 else f"{hz:.0f}"
            painter.drawText(QRectF(x - 22, plot.bottom() + 4, 44, 14),
                             Qt.AlignCenter, label)

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
        nyquist = max(1.0, self._nyquist)
        return plot.left() + (min(hz, nyquist) / nyquist) * plot.width()

    def _db_to_y(self, db: float, plot: QRectF) -> float:
        # 固定 -105 ~ +8 dB 的纵向范围，不同文件之间可直接横向比较
        top, bottom = 8.0, -105.0
        ratio = (top - max(bottom, min(top, db))) / (top - bottom)
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

        # ---- 双视图：完整瀑布图 / 平均频谱 ----
        self.tabs = QTabWidget()
        self.spectrogram = SpectrogramCanvas()
        self.curve = SpectrumCanvas()
        self.tabs.addTab(self.spectrogram, i18n.tr_text("完整频谱"))
        self.tabs.addTab(self.curve, i18n.tr_text("平均频谱"))
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

        self._start()

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

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(2000)
        super().closeEvent(event)


def open_spectrum(track: Track, parent: Optional[QWidget] = None) -> None:
    """打开频谱窗口（供右键菜单直接调用）。"""
    dialog = SpectrumDialog(track, parent)
    dialog.exec()
