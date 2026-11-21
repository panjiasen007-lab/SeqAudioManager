"""31 段图示均衡器：实时曲线预览 + 竖向滑杆 + 内置/自定义预设 + 100 ms 防抖。

为什么滑杆拖动要防抖
--------------------
每个增益变化都会触发引擎重算 31 个 biquad 的级联频响（``BiquadEq`` 的
``_rebuild_response``）。一次拖动会发出几十个 ``valueChanged``，不防抖就是
「拖一下滑杆，音频线程算几十遍」。100 ms 静默后再发一次，听感上仍是即时的。

架构约束
--------
UI **不得** import ``seqaudio.player``（架构 2.3），所以 31 个频点在本文件按
字面量复写，并由 ``tests/test_player_ui.py`` 守护与
``player.constants.EQ_BANDS_HZ`` 逐项一致。

默认值遵守产品定稿：EQ 默认关闭、全局 DSP 默认旁路（``playback_dsp_bypass``
为 ``True``）。打开 EQ 时由主窗口负责解开全局旁路 —— 面板本身不碰设置里的
其它字段，避免两处都写同一份配置。

自定义预设的落盘
----------------
面板只负责「内存里的字典 + 信号」，写盘由主窗口做（它持有 ``Settings``）：
``customPresetSaved(name, gains)`` / ``customPresetDeleted(name)``。
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .widgets import Card, SectionHeader

log = logging.getLogger(__name__)

#: 31 段 1/3 倍频程中心频率（Hz），与 ``player/constants.EQ_BANDS_HZ`` 一致。
#: UI 不得 import player，故此处复写；一致性由测试守护。
#: 下标语义：20 Hz = 0，1 kHz = 17，20 kHz = 30。
EQ_BANDS_HZ: tuple[int, ...] = (
    20, 25, 31, 40, 50, 63, 80, 100, 125, 160,
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000,
    20000,
)
EQ_MIN_DB = -12.0
EQ_MAX_DB = 12.0
#: 滑杆内部刻度：0.5 dB = 5 个刻度单位
EQ_SCALE = 10
EQ_STEP = 5

#: 自定义预设项的 data 前缀（``custom:<name>``）
CUSTOM_PREFIX = "custom:"
#: 内置预设项的 data 前缀（``builtin:<key>``）
BUILTIN_PREFIX = "builtin:"

#: 内置预设的原始听感曲线（对应 20 段半倍频程频点，v1.7 时代定的值）。
#: 31 段升级后按对数频率插值到新频点，保留原有听感意图，不手写 8×31 个数字。
_OLD_BANDS_HZ: tuple[int, ...] = (
    22, 31, 42, 63, 88, 125, 177, 250, 350, 500,
    710, 1000, 1400, 2000, 2800, 4000, 5600, 8000, 11000, 16000,
)
_LEGACY_CURVES: dict[str, tuple[float, ...]] = {
    "flat": (0.0,) * 20,
    "pop": (
        1.5, 1.5, 1.2, 0.8, 0.4, 0.0, -0.3, -0.5, -0.3, 0.0,
        0.6, 1.0, 1.4, 1.6, 1.6, 1.4, 1.2, 1.0, 0.8, 0.5,
    ),
    "rock": (
        4.0, 3.8, 3.2, 2.4, 1.5, 0.8, 0.0, -0.8, -1.2, -1.0,
        -0.4, 0.4, 1.4, 2.2, 2.8, 3.0, 3.0, 2.8, 2.4, 2.0,
    ),
    "classical": (
        2.2, 2.0, 1.6, 1.0, 0.4, 0.0, 0.0, 0.2, 0.4, 0.4,
        0.2, 0.0, 0.2, 0.6, 1.0, 1.4, 1.8, 2.2, 2.6, 2.8,
    ),
    "vocal": (
        -1.5, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.2, 1.0, 0.6,
        0.4, 0.6, 1.2, 2.0, 2.8, 3.0, 2.6, 1.8, 0.8, -0.5,
    ),
    "bass": (
        6.0, 6.0, 5.6, 5.0, 4.2, 3.2, 2.2, 1.4, 0.8, 0.4,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    ),
    "treble": (
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.4, 1.0, 1.8, 2.6, 3.2, 3.8, 4.2, 4.6, 4.8,
    ),
    "loudness": (
        5.5, 5.5, 5.2, 4.6, 3.8, 2.8, 1.6, 0.4, -0.8, -1.8,
        -2.4, -2.2, -1.4, -0.2, 1.0, 2.2, 3.2, 4.0, 4.6, 5.0,
    ),
}


def _resample_curve(values: tuple[float, ...]) -> tuple[float, ...]:
    """把 20 段听感曲线按对数频率线性插值到 31 段（两端按最近值外推）。"""
    log_old = [math.log10(freq) for freq in _OLD_BANDS_HZ]
    result: list[float] = []
    for freq in EQ_BANDS_HZ:
        x = math.log10(freq)
        if x <= log_old[0]:
            result.append(values[0])
            continue
        if x >= log_old[-1]:
            result.append(values[-1])
            continue
        for index in range(len(log_old) - 1):
            if log_old[index] <= x <= log_old[index + 1]:
                span = log_old[index + 1] - log_old[index]
                ratio = (x - log_old[index]) / span if span else 0.0
                result.append(values[index] + (values[index + 1] - values[index]) * ratio)
                break
    return tuple(round(value, 2) for value in result)


#: 内置预设（dB，31 个值对应 1/3 倍频程频点）。值是「听感」而非测量结果，仅作起点。
PRESETS: dict[str, tuple[float, ...]] = {
    key: _resample_curve(curve) for key, curve in _LEGACY_CURVES.items()
}

#: 预设 -> i18n 文案
PRESET_LABELS: dict[str, str] = {
    "flat": "平直",
    "pop": "流行",
    "rock": "摇滚",
    "classical": "古典",
    "vocal": "人声",
    "bass": "低音",
    "treble": "高音",
    "loudness": "响度",
}

#: 「自定义」项的文案
CUSTOM_LABEL = "自定义"


def format_band(freq: int) -> str:
    """频率标签：1 kHz 以下给整数，以上给 ``1k`` / ``1.4k`` 形式。"""
    value = int(freq)
    if value >= 1000:
        return f"{value / 1000:g}k"
    return str(value)


def format_gain(db: float) -> str:
    """增益显示：``+3.0`` / ``-1.5`` / ``0.0``。"""
    return f"{float(db):+.1f}" if abs(float(db)) >= 0.05 else "0.0"


class EqCurveWidget(QWidget):
    """EQ 频响曲线预览：对数频率轴 + 平滑曲线 + 刻度标注 + 渐变填充。

    31 段节点用 Catmull-Rom 转三次贝塞尔平滑连接，视觉上就是一条连续的
    响应曲线；±12/±6/0 dB 的中线、频率刻度随主题取色，一眼可读。
    """

    #: 频率轴范围（Hz）
    FREQ_MIN = 20.0
    FREQ_MAX = 20000.0
    #: 网格线频率（Hz）
    GRID_HZ = (20.0, 100.0, 1000.0, 10000.0, 20000.0)
    #: 频率轴标注（频率, 文案）——落在左右边缘的标注靠内侧对齐避免被裁
    AXIS_LABELS: tuple[tuple[float, str], ...] = (
        (20.0, "20"), (100.0, "100"), (1000.0, "1k"),
        (10000.0, "10k"), (20000.0, "20k"),
    )

    def __init__(
        self,
        bands: Sequence[int] = EQ_BANDS_HZ,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._bands: tuple[float, ...] = tuple(float(f) for f in bands)
        self._gains: list[float] = [0.0] * len(self._bands)
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_gains(self, gains: Sequence[float]) -> None:
        """更新曲线（长度不符时补零/截断），不触发任何信号。"""
        values = [0.0] * len(self._bands)
        for index, raw in enumerate(list(gains or [])[: len(self._bands)]):
            try:
                values[index] = max(EQ_MIN_DB, min(EQ_MAX_DB, float(raw)))
            except (TypeError, ValueError):
                values[index] = 0.0
        self._gains = values
        self.update()

    def refresh_theme(self) -> None:
        """主题切换后重绘（颜色都在 ``paintEvent`` 里现取）。"""
        self.update()

    # ------------------------------------------------------------ 绘制

    def _x_for_freq(self, freq: float, width: float) -> float:
        """对数频率轴：20 Hz–20 kHz 映射到 [0, width]。"""
        low = math.log10(self.FREQ_MIN)
        high = math.log10(self.FREQ_MAX)
        ratio = (math.log10(max(self.FREQ_MIN, min(self.FREQ_MAX, freq))) - low) / (high - low)
        return ratio * width

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        width, height = rect.width(), rect.height()
        if width <= 2 or height <= 2:
            painter.end()
            return

        # 背板
        panel = QPainterPath()
        panel.addRoundedRect(rect, 10.0, 10.0)
        painter.fillPath(panel, QColor(theme.SURFACE_INPUT))
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(panel)

        mid_y = rect.center().y()
        half = height / 2.0 - 16.0

        # 网格：20 / 100 / 1k / 10k / 20k
        grid_pen = QPen(QColor(theme.LINE_SOFT), 1.0, Qt.DotLine)
        painter.setPen(grid_pen)
        for freq in self.GRID_HZ:
            x = rect.left() + self._x_for_freq(freq, width)
            painter.drawLine(QPointF(x, rect.top() + 10), QPointF(x, rect.bottom() - 10))
        # ±12 / ±6 / 0 dB 水平线（0 dB 实线、其余虚线）
        for db in (EQ_MAX_DB, 6.0, 0.0, -6.0, EQ_MIN_DB):
            y = mid_y - (db / EQ_MAX_DB) * half
            painter.setPen(
                QPen(QColor(theme.LINE), 1.0)
                if db == 0.0
                else QPen(QColor(theme.LINE_SOFT), 1.0, Qt.DashLine)
            )
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # 增益刻度标签（+12 / 0 / -12），用小字号贴在左侧
        tick_font = QFont(painter.font())
        tick_font.setPixelSize(9)
        painter.setFont(tick_font)
        painter.setPen(QColor(theme.FG_MUTE))
        for db in (EQ_MAX_DB, 0.0, EQ_MIN_DB):
            y = mid_y - (db / EQ_MAX_DB) * half
            painter.drawText(
                QRectF(rect.left() + 3, y - 6, 26, 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{db:+.0f}",
            )

        # 频率轴标注（贴在底部）
        for freq, text in self.AXIS_LABELS:
            x = rect.left() + self._x_for_freq(freq, width)
            label_rect = QRectF(x - 12, rect.bottom() - 12, 24, 11)
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignVCenter, text)

        # 曲线节点
        points: list[QPointF] = []
        for band, gain in zip(self._bands, self._gains):
            x = rect.left() + self._x_for_freq(band, width)
            y = mid_y - (gain / EQ_MAX_DB) * half
            points.append(QPointF(x, y))
        if not points:
            painter.end()
            return

        # Catmull-Rom → 三次贝塞尔，得到平滑连续的频响曲线
        curve = QPainterPath(points[0])
        count = len(points)
        for index in range(count - 1):
            p0 = points[index - 1] if index > 0 else points[index]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[index + 2] if index + 2 < count else p2
            control1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0,
                               p1.y() + (p2.y() - p0.y()) / 6.0)
            control2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0,
                               p2.y() - (p3.y() - p1.y()) / 6.0)
            curve.cubicTo(control1, control2, p2)

        # 曲线下方淡渐变填充
        fill = QPainterPath(curve)
        fill.lineTo(QPointF(points[-1].x(), rect.bottom()))
        fill.lineTo(QPointF(points[0].x(), rect.bottom()))
        fill.closeSubpath()
        fill_gradient = QLinearGradient(0.0, mid_y - half, 0.0, rect.bottom())
        top_color = QColor(theme.ACCENT)
        top_color.setAlpha(70)
        bottom_color = QColor(theme.ACCENT)
        bottom_color.setAlpha(0)
        fill_gradient.setColorAt(0.0, top_color)
        fill_gradient.setColorAt(1.0, bottom_color)
        painter.setPen(Qt.NoPen)
        painter.fillPath(fill, fill_gradient)

        stroke = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
        stroke.setColorAt(0.0, QColor(theme.ACCENT))
        stroke.setColorAt(1.0, QColor(theme.CYAN))
        pen = QPen()
        pen.setBrush(stroke)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(curve)

        # 每个频点一个小圆点（31 段曲线节点）
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.CYAN))
        for point in points:
            painter.drawEllipse(point, 1.8, 1.8)
        painter.end()


class PlayerEqPanel(QWidget):
    """31 段均衡器面板。

    Signals:
        gainsChanged(list): 31 段增益（dB），拖动停止 100 ms 后发出。
        enabledChanged(bool): 总开关变化。
        customPresetSaved(str, list): 用户保存了一个命名预设（名称, 31 个 dB 值）。
        customPresetDeleted(str): 用户删除了一个命名预设。
    """

    gainsChanged = Signal(list)
    enabledChanged = Signal(bool)
    customPresetSaved = Signal(str, list)
    customPresetDeleted = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sliders: list[QSlider] = []
        self._value_labels: list[QLabel] = []
        self._cells: list[QFrame] = []
        self._custom_presets: dict[str, list[float]] = {}
        self._loading = False

        self.setMinimumWidth(860)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(9)

        root.addWidget(
            SectionHeader(i18n.tr_text("均衡器"), i18n.tr_text("31 段 · 峰值 ±12 dB"))
        )

        root.addLayout(self._build_toolbar())

        self.curve = EqCurveWidget(EQ_BANDS_HZ)
        root.addWidget(self.curve)

        root.addWidget(self._build_band_area(), 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(100)
        self._debounce.timeout.connect(self._emit_gains)

        self._refresh_enabled_state()

    # ------------------------------------------------------------ 构建

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.enable_check = QCheckBox(i18n.tr_text("启用均衡器"))
        self.enable_check.setToolTip(
            i18n.tr_text("启用后全局 DSP 直通会自动关闭（音量与 EQ 参与处理）")
        )
        self.enable_check.toggled.connect(self._on_enabled_toggled)
        toolbar.addWidget(self.enable_check)

        self.preset_combo = QComboBox()
        self.preset_combo.setObjectName("eqPreset")
        self.preset_combo.setMinimumWidth(132)
        self.preset_combo.setToolTip(i18n.tr_text("预设曲线（内置 + 自定义）"))
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        toolbar.addWidget(self.preset_combo)

        self.save_button = QPushButton(i18n.tr_text("保存"))
        self.save_button.setObjectName("smallGhost")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setToolTip(i18n.tr_text("把当前 31 段曲线保存为命名预设"))
        self.save_button.clicked.connect(self._on_save_clicked)
        toolbar.addWidget(self.save_button)

        self.delete_button = QPushButton(i18n.tr_text("删除"))
        self.delete_button.setObjectName("smallGhost")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setToolTip(i18n.tr_text("删除当前自定义预设"))
        self.delete_button.clicked.connect(self._on_delete_clicked)
        toolbar.addWidget(self.delete_button)

        self.reset_button = QPushButton(i18n.tr_text("重置"))
        self.reset_button.setObjectName("smallGhost")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setToolTip(i18n.tr_text("回到平直（全 0 dB）"))
        self.reset_button.clicked.connect(lambda: self.apply_preset("flat"))
        toolbar.addWidget(self.reset_button)

        toolbar.addStretch(1)
        self._rebuild_combo()
        return toolbar

    def _build_band_area(self) -> QScrollArea:
        """31 个频段列：横向滚动兜底（窗口极窄时不至于把滑杆挤成一条线）。"""
        card = Card(i18n.tr_text("频段"), i18n.tr_text("0 dB 为直通，正为提升"))
        bands = QHBoxLayout()
        bands.setContentsMargins(0, 0, 0, 0)
        bands.setSpacing(4)
        for index, freq in enumerate(EQ_BANDS_HZ):
            bands.addWidget(self._build_band_cell(index, freq))

        holder = QWidget()
        holder.setMinimumWidth(800)
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        holder_layout.addLayout(bands)
        holder_layout.addStretch(0)

        self.band_holder = holder
        card.add_widget(holder)

        scroll = QScrollArea()
        scroll.setObjectName("eqScroll")
        scroll.setWidget(card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.band_scroll = scroll
        return scroll

    def _build_band_cell(self, index: int, freq: int) -> QFrame:
        """单个频段列：数值标签 + 竖向滑杆 + 频率标签（圆角玻璃小卡）。"""
        cell = QFrame()
        cell.setObjectName("eqBandCell")
        cell.setFixedWidth(38)
        column = QVBoxLayout(cell)
        column.setContentsMargins(2, 4, 2, 4)
        column.setSpacing(2)

        value_label = QLabel("0.0")
        value_font = QFont(self.font())
        value_font.setPixelSize(9)
        value_font.setWeight(QFont.Weight.DemiBold)
        value_label.setFont(value_font)
        value_label.setAlignment(Qt.AlignCenter)
        # 高度写死：QLabel 的默认策略会把多余高度吃掉，把滑杆挤成一根线
        value_label.setFixedHeight(13)

        slider = QSlider(Qt.Vertical)
        slider.setObjectName("eqBand")
        slider.setRange(int(EQ_MIN_DB * EQ_SCALE), int(EQ_MAX_DB * EQ_SCALE))
        slider.setSingleStep(EQ_STEP)
        slider.setPageStep(EQ_STEP * 4)
        slider.setValue(0)
        # 最小高 150（面板更高时跟着伸展，越长越好拖）
        slider.setMinimumHeight(150)
        slider.setCursor(Qt.PointingHandCursor)
        slider.setToolTip(f"{freq} Hz")
        slider.valueChanged.connect(lambda _value, i=index: self._on_slider_changed(i))

        freq_label = QLabel(format_band(freq))
        freq_label.setObjectName("hint")
        freq_font = QFont(self.font())
        freq_font.setPixelSize(9)
        freq_label.setFont(freq_font)
        freq_label.setAlignment(Qt.AlignCenter)
        freq_label.setFixedHeight(13)

        column.addWidget(value_label)
        column.addWidget(slider, 1)
        column.addWidget(freq_label)

        self._cells.append(cell)
        self._sliders.append(slider)
        self._value_labels.append(value_label)
        return cell

    # ------------------------------------------------------------ 接口

    def set_gains(self, gains) -> None:
        """外部灌入 31 段增益（不触发 ``gainsChanged``）。"""
        values = self._normalize(gains)
        self._loading = True
        try:
            for slider, value in zip(self._sliders, values):
                slider.setValue(int(round(value * EQ_SCALE)))
        finally:
            self._loading = False
        self._refresh_value_labels()

    def gains(self) -> list[float]:
        return [round(slider.value() / EQ_SCALE, 1) for slider in self._sliders]

    def set_enabled(self, enabled: bool) -> None:
        """设置总开关（不触发 ``enabledChanged``）。"""
        blocked = self.enable_check.blockSignals(True)
        self.enable_check.setChecked(bool(enabled))
        self.enable_check.blockSignals(blocked)
        self._refresh_enabled_state()

    def is_eq_enabled(self) -> bool:
        return self.enable_check.isChecked()

    def apply_preset(self, key: str) -> None:
        """套用内置预设并立即发出一次增益（按钮点击是离散动作，无需防抖）。"""
        values = PRESETS.get(str(key))
        if values is None:
            return
        self.set_gains(values)
        index = self.preset_combo.findData(f"{BUILTIN_PREFIX}{key}")
        if index >= 0:
            blocked = self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(index)
            self.preset_combo.blockSignals(blocked)
        self._refresh_delete_enabled()
        self._emit_gains()

    def preset_key(self) -> str:
        """当前预设标识：内置返回短键（``rock``），自定义返回名字，其余 ``custom``。"""
        data = str(self.preset_combo.currentData() or "")
        if data.startswith(BUILTIN_PREFIX):
            return data[len(BUILTIN_PREFIX):]
        if data.startswith(CUSTOM_PREFIX):
            return data[len(CUSTOM_PREFIX):]
        return "custom"

    # ---- 自定义预设 ----

    def set_custom_presets(self, presets: dict[str, list[float]]) -> None:
        """灌入用户自定义预设（不触发保存/删除信号）。"""
        clean: dict[str, list[float]] = {}
        for name, gains in dict(presets or {}).items():
            key = str(name or "").strip()
            if not key:
                continue
            clean[key] = self._normalize(gains)
        self._custom_presets = clean
        self._rebuild_combo(self.preset_combo.currentData())

    def custom_presets(self) -> dict[str, list[float]]:
        """当前自定义预设（副本，避免外部改到内部状态）。"""
        return {name: list(values) for name, values in self._custom_presets.items()}

    def save_custom_preset(self, name: str) -> bool:
        """把当前 31 段曲线存为命名预设并发信号；名字非法返回 ``False``。

        非法情况：空名、与内置预设重名（内置项不可覆盖）、与本面板同名（应走删除）。
        """
        key = str(name or "").strip()
        if not key:
            return False
        if key in PRESET_LABELS or key in PRESETS:
            return False
        self._custom_presets[key] = self.gains()
        self._rebuild_combo(f"{CUSTOM_PREFIX}{key}")
        self._refresh_delete_enabled()
        self.customPresetSaved.emit(key, list(self._custom_presets[key]))
        return True

    def delete_custom_preset(self, name: str) -> bool:
        """删除一个命名预设并发信号；不存在返回 ``False``。"""
        key = str(name or "").strip()
        if key not in self._custom_presets:
            return False
        del self._custom_presets[key]
        self._rebuild_combo("")
        self._refresh_delete_enabled()
        self.customPresetDeleted.emit(key)
        return True

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _normalize(gains) -> list[float]:
        """把任意输入折成 31 个 [-12, 12] 的浮点（长度不符时补零/截断）。"""
        values: list[float] = []
        for value in list(gains or [])[: len(EQ_BANDS_HZ)]:
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = 0.0
            values.append(max(EQ_MIN_DB, min(EQ_MAX_DB, number)))
        while len(values) < len(EQ_BANDS_HZ):
            values.append(0.0)
        return values

    def _rebuild_combo(self, selected: object = None) -> None:
        """重建下拉框：8 个内置 → 用户预设 → 末尾「自定义」。"""
        wanted = str(selected or "")
        blocked = self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for key, label in PRESET_LABELS.items():
                self.preset_combo.addItem(
                    i18n.tr_text(label), f"{BUILTIN_PREFIX}{key}"
                )
            for name in sorted(self._custom_presets):
                self.preset_combo.addItem(name, f"{CUSTOM_PREFIX}{name}")
            self.preset_combo.addItem(i18n.tr_text(CUSTOM_LABEL), "")
            index = self.preset_combo.findData(wanted) if wanted else -1
            if index < 0:
                index = self.preset_combo.findData(f"{BUILTIN_PREFIX}flat")
            if index < 0:
                index = 0
            self.preset_combo.setCurrentIndex(index)
        finally:
            self.preset_combo.blockSignals(blocked)
        self._refresh_delete_enabled()

    def _current_custom_name(self) -> str:
        data = str(self.preset_combo.currentData() or "")
        if data.startswith(CUSTOM_PREFIX):
            return data[len(CUSTOM_PREFIX):]
        return ""

    def _on_slider_changed(self, index: int) -> None:
        self._refresh_value_labels()
        if self._loading:
            return
        self._sync_combo_with_gains()
        self._debounce.start()

    def _sync_combo_with_gains(self) -> None:
        """曲线与所选预设不一致时，把下拉切到「自定义」（不触发预设覆盖）。"""
        if self._loading:
            return
        data = str(self.preset_combo.currentData() or "")
        if data.startswith(BUILTIN_PREFIX):
            expected = PRESETS.get(data[len(BUILTIN_PREFIX):])
            if expected is not None and list(expected) == self.gains():
                return
        elif data.startswith(CUSTOM_PREFIX):
            expected = self._custom_presets.get(data[len(CUSTOM_PREFIX):])
            if expected is not None and self._normalize(expected) == self.gains():
                return
        elif data == "":
            return  # 已经是「自定义」，不必再切
        index = self.preset_combo.findData("")
        if index >= 0:
            blocked = self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(index)
            self.preset_combo.blockSignals(blocked)
        self._refresh_delete_enabled()

    def _emit_gains(self) -> None:
        values = self.gains()
        log.debug("EQ 增益：%s", values)
        self.curve.set_gains(values)
        self.gainsChanged.emit(values)

    def _on_enabled_toggled(self, checked: bool) -> None:
        self._refresh_enabled_state()
        self.enabledChanged.emit(bool(checked))

    def _on_preset_changed(self, _index: int) -> None:
        data = str(self.preset_combo.currentData() or "")
        if data.startswith(BUILTIN_PREFIX):
            key = data[len(BUILTIN_PREFIX):]
            values = PRESETS.get(key)
            if values is not None:
                self.set_gains(values)
                self._emit_gains()
        elif data.startswith(CUSTOM_PREFIX):
            name = data[len(CUSTOM_PREFIX):]
            values = self._custom_presets.get(name)
            if values is not None:
                self.set_gains(values)
                self._emit_gains()
        self._refresh_delete_enabled()

    def _on_save_clicked(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            i18n.tr_text("保存预设"),
            i18n.tr_text("预设名称（不能与内置预设重名）"),
        )
        if not accepted:
            return
        key = str(name or "").strip()
        if not key:
            QMessageBox.information(
                self, i18n.tr_text("保存预设"), i18n.tr_text("名称不能为空")
            )
            return
        if key in PRESET_LABELS or key in PRESETS:
            QMessageBox.information(
                self,
                i18n.tr_text("保存预设"),
                i18n.tr_text("该名称与内置预设重名，请换一个"),
            )
            return
        self.save_custom_preset(key)

    def _on_delete_clicked(self) -> None:
        name = self._current_custom_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            i18n.tr_text("删除预设"),
            i18n.tr_text("确定删除预设「{}」？").format(name),
        )
        if answer != QMessageBox.Yes:
            return
        self.delete_custom_preset(name)

    def _refresh_value_labels(self) -> None:
        gains = self.gains()
        for label, slider, db in zip(self._value_labels, self._sliders, gains):
            label.setText(format_gain(db))
            if abs(db) < 0.05:
                color = theme.FG_MUTE
            elif db > 0:
                color = theme.OK
            else:
                color = theme.WARN
            label.setStyleSheet(f"color:{color}; background:transparent;")
        self.curve.set_gains(gains)

    def _refresh_enabled_state(self) -> None:
        enabled = self.enable_check.isChecked()
        for slider in self._sliders:
            slider.setEnabled(enabled)
        for cell in self._cells:
            cell.setProperty("eqActive", enabled)
            cell.style().unpolish(cell)
            cell.style().polish(cell)
        self.preset_combo.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self._refresh_delete_enabled()

    def _refresh_delete_enabled(self) -> None:
        self.delete_button.setEnabled(
            self.enable_check.isChecked() and bool(self._current_custom_name())
        )

    def sizeHint(self):  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QSize

        return QSize(940, 430)

    def refresh_theme(self) -> None:
        """主题切换后重绘曲线与数值颜色。"""
        self._refresh_value_labels()
        self.curve.refresh_theme()
        self.update()

    def refresh_texts(self) -> None:
        """语言切换后刷新预设项与按钮文案。"""
        self._rebuild_combo(self.preset_combo.currentData())
        self.save_button.setText(i18n.tr_text("保存"))
        self.delete_button.setText(i18n.tr_text("删除"))
        self.reset_button.setText(i18n.tr_text("重置"))
        self.enable_check.setText(i18n.tr_text("启用均衡器"))
        self._refresh_value_labels()
        self._refresh_enabled_state()


__all__ = [
    "PlayerEqPanel",
    "EqCurveWidget",
    "PRESETS",
    "PRESET_LABELS",
    "EQ_BANDS_HZ",
    "EQ_MIN_DB",
    "EQ_MAX_DB",
    "EQ_SCALE",
    "EQ_STEP",
    "CUSTOM_PREFIX",
    "BUILTIN_PREFIX",
    "format_band",
    "format_gain",
]
