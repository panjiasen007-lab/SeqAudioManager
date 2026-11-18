"""10 段均衡器面板：竖向滑杆 + 预设 + 100 ms 防抖。

为什么滑杆拖动要防抖
--------------------
每个增益变化都会触发引擎重算 10 个 biquad 的级联频响（``BiquadEq`` 的
``_rebuild_response``）。一次拖动会发出几十个 ``valueChanged``，不防抖就是
「拖一下滑杆，音频线程算几十遍」。100 ms 静默后再发一次，听感上仍是即时的。

默认值遵守产品定稿：EQ 默认关闭、全局 DSP 默认旁路（``playback_dsp_bypass``
为 ``True``）。打开 EQ 时由主窗口负责解开全局旁路 —— 面板本身不碰设置里的
其它字段，避免两处都写同一份配置。
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .widgets import Card, SectionHeader

log = logging.getLogger(__name__)

#: 10 段中心频率（Hz），与 ``player/constants.EQ_BANDS_HZ`` 一致。
#: UI 不得 import player（架构 2.3），所以这里按字面量复写并由测试守护一致。
EQ_BANDS_HZ: tuple[int, ...] = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_MIN_DB = -12.0
EQ_MAX_DB = 12.0
#: 滑杆内部刻度：0.5 dB = 5 个刻度单位
EQ_SCALE = 10
EQ_STEP = 5

#: 预设（dB，正负对称的常用曲线）。值是「听感」而非测量结果，仅作起点。
PRESETS: dict[str, tuple[float, ...]] = {
    "flat": (0.0,) * 10,
    "pop": (-1.0, 0.5, 2.0, 3.0, 1.5, -0.5, -1.0, 0.0, 1.0, 1.5),
    "classical": (3.0, 2.0, 0.0, 0.0, -0.5, -0.5, 0.0, 1.5, 2.5, 3.0),
    "rock": (4.0, 3.0, 1.0, -1.0, -1.5, 0.0, 1.5, 3.0, 3.5, 4.0),
    "vocal": (-1.0, -1.0, 0.0, 1.5, 3.0, 3.5, 2.5, 1.0, 0.0, -1.0),
}

#: 预设 -> i18n 文案
PRESET_LABELS: dict[str, str] = {
    "flat": "平直",
    "pop": "流行",
    "classical": "古典",
    "rock": "摇滚",
    "vocal": "人声",
}


def format_band(freq: int) -> str:
    """频率标签：1 kHz 以下给整数，以上给 ``2k`` 形式。"""
    if freq >= 1000:
        return f"{freq // 1000}k"
    return str(freq)


def format_gain(db: float) -> str:
    """增益显示：``+3.0`` / ``-1.5`` / ``0.0``。"""
    return f"{float(db):+.1f}" if abs(float(db)) >= 0.05 else "0.0"


class PlayerEqPanel(QWidget):
    """均衡器面板。

    Signals:
        gainsChanged(list): 10 段增益（dB），拖动停止 100 ms 后发出。
        enabledChanged(bool): 总开关变化。
    """

    gainsChanged = Signal(list)
    enabledChanged = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sliders: list[QSlider] = []
        self._value_labels: list[QLabel] = []
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(
            SectionHeader(i18n.tr_text("均衡器"), i18n.tr_text("10 段 · 峰值 ±12 dB"))
        )

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
        for key, label in PRESET_LABELS.items():
            self.preset_combo.addItem(i18n.tr_text(label), key)
        self.preset_combo.setMinimumWidth(96)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        toolbar.addWidget(self.preset_combo)

        self.reset_button = QPushButton(i18n.tr_text("重置"))
        self.reset_button.setObjectName("smallGhost")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.clicked.connect(lambda: self.apply_preset("flat"))
        toolbar.addWidget(self.reset_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        card = Card(i18n.tr_text("频段"), i18n.tr_text("0 dB 为直通，正为提升"))
        bands = QHBoxLayout()
        bands.setContentsMargins(0, 0, 0, 0)
        bands.setSpacing(6)
        for index, freq in enumerate(EQ_BANDS_HZ):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            value_label = QLabel("0.0")
            value_font = QFont(value_label.font())
            value_font.setBold(True)
            value_label.setFont(value_font)
            value_label.setAlignment(Qt.AlignCenter)

            slider = QSlider(Qt.Vertical)
            slider.setRange(int(EQ_MIN_DB * EQ_SCALE), int(EQ_MAX_DB * EQ_SCALE))
            slider.setSingleStep(EQ_STEP)
            slider.setPageStep(EQ_STEP * 4)
            slider.setValue(0)
            slider.setMinimumHeight(120)
            slider.setTickInterval(EQ_STEP * 2)
            slider.setTickPosition(QSlider.TicksBothSides)
            slider.setToolTip(f"{freq} Hz")
            slider.valueChanged.connect(
                lambda _value, i=index: self._on_slider_changed(i)
            )

            freq_label = QLabel(format_band(freq))
            freq_label.setObjectName("hint")
            freq_label.setAlignment(Qt.AlignCenter)

            column.addWidget(value_label)
            column.addWidget(slider, 1)
            column.addWidget(freq_label)
            bands.addLayout(column)
            self._sliders.append(slider)
            self._value_labels.append(value_label)
        card.add_layout(bands)
        root.addWidget(card)
        root.addStretch(1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(100)
        self._debounce.timeout.connect(self._emit_gains)

        self._refresh_enabled_state()

    # ------------------------------------------------------------ 接口

    def set_gains(self, gains) -> None:
        """外部灌入 10 段增益（不触发 ``gainsChanged``）。"""
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
        """套用预设并立即发出一次增益（按钮点击是离散动作，无需防抖）。"""
        values = PRESETS.get(str(key))
        if values is None:
            return
        self.set_gains(values)
        index = self.preset_combo.findData(str(key))
        if index >= 0:
            blocked = self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(index)
            self.preset_combo.blockSignals(blocked)
        self._emit_gains()

    def preset_key(self) -> str:
        return str(self.preset_combo.currentData() or "custom")

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _normalize(gains) -> list[float]:
        """把任意输入折成 10 个 [-12, 12] 的浮点（长度不符时补零/截断）。"""
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

    def _on_slider_changed(self, index: int) -> None:
        self._refresh_value_labels()
        if self._loading:
            return
        self._debounce.start()

    def _emit_gains(self) -> None:
        values = self.gains()
        log.debug("EQ 增益：%s", values)
        self.gainsChanged.emit(values)

    def _on_enabled_toggled(self, checked: bool) -> None:
        self._refresh_enabled_state()
        self.enabledChanged.emit(bool(checked))

    def _on_preset_changed(self, _index: int) -> None:
        key = str(self.preset_combo.currentData() or "flat")
        if key in PRESETS:
            self.set_gains(PRESETS[key])
            self._emit_gains()

    def _refresh_value_labels(self) -> None:
        for label, slider in zip(self._value_labels, self._sliders):
            db = slider.value() / EQ_SCALE
            label.setText(format_gain(db))
            if abs(db) < 0.05:
                label.setStyleSheet(f"color:{theme.FG_MUTE};")
            elif db > 0:
                label.setStyleSheet(f"color:{theme.OK};")
            else:
                label.setStyleSheet(f"color:{theme.WARN};")

    def _refresh_enabled_state(self) -> None:
        enabled = self.enable_check.isChecked()
        for slider in self._sliders:
            slider.setEnabled(enabled)
        self.preset_combo.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)

    def refresh_texts(self) -> None:
        """语言切换后刷新预设项文案。"""
        for index in range(self.preset_combo.count()):
            key = str(self.preset_combo.itemData(index) or "")
            if key in PRESET_LABELS:
                self.preset_combo.setItemText(index, i18n.tr_text(PRESET_LABELS[key]))
        self._refresh_value_labels()


__all__ = [
    "PlayerEqPanel",
    "PRESETS",
    "PRESET_LABELS",
    "EQ_BANDS_HZ",
    "format_band",
    "format_gain",
]
