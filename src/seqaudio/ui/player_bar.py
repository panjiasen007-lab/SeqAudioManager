"""底部播放条：封面 / 曲目 / 传输控制 / 进度 / 音量 / 模式 / 折叠。

设计约束
--------
* **不持有引擎**：本控件只发信号、只接收 ``set_*`` 方法，命令转发全部由
  ``main_window`` 经 ``PlayerBridge`` 完成。这样播放条可以在没有引擎的测试里
  独立渲染（``tests/test_player_ui.py`` 就是这么用的）。
* 高度 56 px（折叠 32 px）：折叠态只留播放/暂停与曲名，符合「不打断现有布局」
  的最小侵入要求。
* 颜色全部来自 ``theme``，不写死色值 —— 11 套主题切换时（TC-GUI-27）才跟着变。
* 尺寸用 ``fontMetrics`` 推算，高 DPI 下不会把图标挤没。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme

log = logging.getLogger(__name__)

#: 播放条展开 / 折叠高度（px）
BAR_HEIGHT = 56
BAR_COLLAPSED_HEIGHT = 32
#: 封面边长（px，高 DPI 下按 devicePixelRatio 放大位图）
COVER_SIZE = 40

#: 模式循环顺序（FR-PL-002 的四种）
MODE_ORDER: tuple[str, ...] = ("sequential", "repeat_one", "repeat_all", "shuffle")

#: 模式 -> 图标（单字符，避免依赖 emoji 字体）
MODE_GLYPH = {
    "sequential": "→",
    "repeat_one": "①",
    "repeat_all": "↻",
    "shuffle": "⤨",
}

#: 音量滑块：内部用 0.1 dB 的整数，避免浮点抖动
VOLUME_SCALE = 10


def format_mmss(ms: int) -> str:
    """毫秒 → ``mm:ss``（负数与 None 按 0 处理）。"""
    total = max(0, int(ms or 0)) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


class _VolumeSlider(QSlider):
    """音量滑块：双击归零（0 dB = 旁路）。

    ``QSlider`` 没有双击信号，双击默认还会把滑块位置跳到点击处 —— 对音量来说
    这是「手一抖就静音」的体验，所以这里吞掉双击并改成归零。
    """

    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PlayerBar(QFrame):
    """底部单行播放条。

    Signals:
        playRequested: 请求播放（无曲目或已暂停时）。
        pauseRequested: 请求暂停。
        nextRequested / prevRequested: 上/下一首。
        seekRequested(int): 拖进度条松手后的目标位置（毫秒）。
        volumeChanged(float): 音量（dB，0 表示旁路）。
        modeChanged(str): 模式键（``sequential`` / ``repeat_one`` /
            ``repeat_all`` / ``shuffle``）。
        collapseToggled(bool): 折叠状态变化。
        signalPanelToggled(bool): 「信号路径」开关按钮。
    """

    playRequested = Signal()
    pauseRequested = Signal()
    nextRequested = Signal()
    prevRequested = Signal()
    seekRequested = Signal(int)
    volumeChanged = Signal(float)
    modeChanged = Signal(str)
    collapseToggled = Signal(bool)
    signalPanelToggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("playerBar")
        self.setFixedHeight(BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._state = "IDLE"
        self._mode = "sequential"
        self._track: Optional[dict[str, Any]] = None
        self._total_ms = 0
        self._position_ms = 0
        self._volume_db = 0.0
        self._dragging = False
        self._collapsed = False
        self._enabled = True
        self._volume_mode = "bypass"
        self._cover_bytes: Optional[bytes] = None
        self._signal_visible = False

        metrics = QFontMetrics(self.font())
        self._unit = max(8, metrics.height() // 2 + 2)
        self._button_size = int(metrics.height() * 1.7)

        self._build()
        self._restyle()
        self.set_track(None)
        self.set_state("IDLE")

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(10)

        # 封面
        self.cover = QLabel()
        self.cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.cover.setObjectName("hint")
        self.cover.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.cover)

        # 曲名 / 艺术家
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        self.title_label = QLabel(i18n.tr_text("未在播放"))
        title_font = QFont(self.title_label.font())
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setMinimumWidth(60)
        self.artist_label = QLabel("")
        self.artist_label.setObjectName("hint")
        self.artist_label.setMinimumWidth(60)
        text_col.addWidget(self.title_label)
        text_col.addWidget(self.artist_label)
        self.text_widget = QWidget()
        self.text_widget.setLayout(text_col)
        self.text_widget.setMinimumWidth(90)
        self.text_widget.setMaximumWidth(280)
        outer.addWidget(self.text_widget, 0)

        # 传输控制
        self.btn_prev = self._small_button("|◀", i18n.tr_text("上一首"))
        self.btn_play = self._small_button("▶", i18n.tr_text("播放"))
        self.btn_next = self._small_button("▶|", i18n.tr_text("下一首"))
        self.btn_prev.clicked.connect(self.prevRequested.emit)
        self.btn_play.clicked.connect(self._on_play_clicked)
        self.btn_next.clicked.connect(self.nextRequested.emit)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.btn_prev)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_next)
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(controls)
        outer.addWidget(self.controls_widget)

        # 进度条 + 时间
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setObjectName("playerProgress")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setMinimumWidth(80)
        self.progress.setCursor(Qt.PointingHandCursor)
        self.progress.sliderPressed.connect(self._on_slider_pressed)
        self.progress.sliderReleased.connect(self._on_slider_released)
        self.progress.sliderMoved.connect(self._on_slider_moved)
        self.progress.setToolTip(i18n.tr_text("进度"))

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("hint")
        time_font = QFont(self.font())
        time_font.setStyleHint(QFont.Monospace)
        self.time_label.setFont(time_font)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(8)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.time_label)
        self.progress_widget = QWidget()
        self.progress_widget.setLayout(progress_row)
        outer.addWidget(self.progress_widget, 1)

        # 音量
        self.volume_slider = _VolumeSlider(Qt.Horizontal)
        self.volume_slider.setObjectName("playerVolume")
        self.volume_slider.setRange(-60 * VOLUME_SCALE, 0)
        self.volume_slider.setValue(0)
        self.volume_slider.setFixedWidth(int(self._button_size * 2.6))
        self.volume_slider.setToolTip(i18n.tr_text("音量"))
        self.volume_slider.valueChanged.connect(self._on_volume_slider)
        self.volume_slider.doubleClicked.connect(self._reset_volume)
        self.volume_label = QLabel("旁路")
        self.volume_label.setObjectName("hint")
        self.volume_label.setMinimumWidth(int(self._button_size * 1.6))
        volume_row = QHBoxLayout()
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(6)
        volume_row.addWidget(self.volume_slider)
        volume_row.addWidget(self.volume_label)
        self.volume_widget = QWidget()
        self.volume_widget.setLayout(volume_row)
        outer.addWidget(self.volume_widget)

        # 模式
        self.btn_mode = self._small_button(MODE_GLYPH["sequential"], i18n.tr_text("顺序播放"))
        self.btn_mode.clicked.connect(self.cycle_mode)
        outer.addWidget(self.btn_mode)

        # 信号路径开关
        self.btn_signal = self._small_button("≈", i18n.tr_text("信号路径"))
        self.btn_signal.setCheckable(True)
        self.btn_signal.toggled.connect(self._on_signal_toggled)
        outer.addWidget(self.btn_signal)

        # 折叠
        self.btn_collapse = self._small_button("▾", i18n.tr_text("收起播放条"))
        self.btn_collapse.clicked.connect(self.toggle_collapsed)
        outer.addWidget(self.btn_collapse)

    def _small_button(self, text: str, tip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("smallGhost")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(QSize(self._button_size, self._button_size))
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.TabFocus)
        return btn

    # ------------------------------------------------------------ 主题

    def _restyle(self) -> None:
        """按当前主题重刷自绘配色（主题切换后由 ``apply_theme`` 再调一次）。"""
        self.setStyleSheet(
            f"""
            QFrame#playerBar {{
                background: {theme.SURFACE_CARD};
                border-top: 1px solid {theme.LINE};
                border-bottom: 1px solid {theme.LINE};
            }}
            QSlider#playerProgress::groove:horizontal {{
                height: 4px; background: {theme.LINE_SOFT}; border-radius: 2px;
            }}
            QSlider#playerProgress::sub-page:horizontal {{
                background: {theme.ACCENT}; border-radius: 2px;
            }}
            QSlider#playerProgress::handle:horizontal {{
                width: 10px; margin: -4px 0; border-radius: 5px;
                background: {theme.CYAN};
            }}
            QSlider#playerVolume::groove:horizontal {{
                height: 4px; background: {theme.LINE_SOFT}; border-radius: 2px;
            }}
            QSlider#playerVolume::sub-page:horizontal {{
                background: {theme.CYAN}; border-radius: 2px;
            }}
            QSlider#playerVolume::handle:horizontal {{
                width: 9px; margin: -3px 0; border-radius: 4px;
                background: {theme.FG_DIM};
            }}
            """
        )
        self.cover.setStyleSheet(
            f"background:{theme.SURFACE_INPUT}; border:1px solid {theme.LINE_SOFT};"
            f"border-radius:4px; color:{theme.FG_MUTE}; font-size:9px;"
        )
        self._render_cover()

    def refresh_theme(self) -> None:
        """主题切换后调用（颜色来自 ``theme``，必须重算）。"""
        self._restyle()

    # ------------------------------------------------------------ 数据接口

    def set_track(self, track: Optional[dict[str, Any]]) -> None:
        """设置当前曲目；``None`` 表示没有在播的曲目。"""
        self._track = dict(track) if isinstance(track, dict) else None
        self._cover_bytes = None
        if self._track:
            cover = self._track.get("cover")
            if isinstance(cover, (bytes, bytearray)):
                self._cover_bytes = bytes(cover)
            title = str(self._track.get("title") or "").strip()
            if not title:
                path_text = str(self._track.get("path") or "")
                title = path_text.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                title = title.rsplit(".", 1)[0] if "." in title else title
            artist = str(self._track.get("artist") or "").strip()
            album = str(self._track.get("album") or "").strip()
            self.artist_label.setText(artist or album or "—")
            self.artist_label.setToolTip(" · ".join(p for p in (artist, album) if p))
            self.title_label.setText(title or i18n.tr_text("未在播放"))
            self.title_label.setToolTip(str(self._track.get("path") or ""))
            duration_ms = int(self._track.get("duration_ms") or 0)
            if duration_ms <= 0:
                duration_ms = int(round(float(self._track.get("duration") or 0.0) * 1000.0))
            self._total_ms = max(0, duration_ms)
        else:
            self.title_label.setText(i18n.tr_text("未在播放"))
            self.title_label.setToolTip("")
            self.artist_label.setText("")
            self.artist_label.setToolTip("")
            self._total_ms = 0
            self._position_ms = 0
        self._render_cover()
        self._refresh_progress()
        self._refresh_enabled()

    def set_position(self, ms: int, total: Optional[int] = None) -> None:
        """更新播放位置；``total`` 给定且变化时同步总长。"""
        if total is not None:
            new_total = max(0, int(total))
            if new_total != self._total_ms:
                self._total_ms = new_total
        self._position_ms = max(0, int(ms or 0))
        self._refresh_progress()

    def set_state(self, state: str) -> None:
        """更新状态（``IDLE`` / ``LOADING`` / ``PLAYING`` / ``PAUSED`` / ``ERROR``）。"""
        value = str(getattr(state, "value", state) or "IDLE").upper()
        if value not in ("IDLE", "LOADING", "PLAYING", "PAUSED", "ERROR"):
            value = "IDLE"
        self._state = value
        playing = value in ("PLAYING", "LOADING")
        self.btn_play.setText("❚❚" if playing else "▶")
        self.btn_play.setToolTip(
            i18n.tr_text("暂停") if playing else i18n.tr_text("播放")
        )
        self.btn_play.setObjectName("primary" if playing else "smallGhost")
        self.btn_play.setStyleSheet("")
        self.btn_play.style().unpolish(self.btn_play)
        self.btn_play.style().polish(self.btn_play)
        if value == "ERROR":
            self.title_label.setText(
                self.title_label.text() or i18n.tr_text("播放错误")
            )
        self._refresh_enabled()

    def set_volume(self, db: float) -> None:
        """设置音量显示（dB）。0 dB 显示「旁路」。"""
        value = float(db or 0.0)
        self._volume_db = value
        slider_value = int(round(max(-60.0, min(0.0, value)) * VOLUME_SCALE))
        blocked = self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(slider_value)
        self.volume_slider.blockSignals(blocked)
        self._refresh_volume_label()

    def set_volume_mode(self, mode: str) -> None:
        """``bypass`` 时音量控件置灰并说明「系统音量仍可能衰减」。"""
        self._volume_mode = "software" if str(mode) == "software" else "bypass"
        self._refresh_enabled()

    def set_mode(self, mode: str) -> None:
        """设置播放模式（未知值回落顺序播放）。"""
        value = str(getattr(mode, "value", mode) or "sequential")
        if value not in MODE_ORDER:
            value = "sequential"
        self._mode = value
        self.btn_mode.setText(MODE_GLYPH.get(value, "→"))
        self.btn_mode.setToolTip(self._mode_text(value))

    def set_playback_enabled(self, enabled: bool) -> None:
        """批处理/功能开关：禁用起播控件（不是隐藏）。"""
        self._enabled = bool(enabled)
        self._refresh_enabled()

    def set_signal_panel_visible(self, visible: bool) -> None:
        """同步「信号路径」开关按钮的按下态（不触发信号）。"""
        self._signal_visible = bool(visible)
        blocked = self.btn_signal.blockSignals(True)
        self.btn_signal.setChecked(self._signal_visible)
        self.btn_signal.blockSignals(blocked)

    def cycle_mode(self) -> None:
        """顺序 → 单曲 → 列表循环 → 随机 → 顺序。"""
        try:
            index = MODE_ORDER.index(self._mode)
        except ValueError:
            index = 0
        value = MODE_ORDER[(index + 1) % len(MODE_ORDER)]
        self.set_mode(value)
        self.modeChanged.emit(value)

    # ------------------------------------------------------------ 状态查询

    def is_collapsed(self) -> bool:
        return self._collapsed

    def current_mode(self) -> str:
        return self._mode

    def volume_db(self) -> float:
        """当前音量（dB）。"""
        return self._volume_db

    def current_state(self) -> str:
        return self._state

    def total_ms(self) -> int:
        return self._total_ms

    # ------------------------------------------------------------ 内部槽

    def _on_play_clicked(self) -> None:
        if self._state in ("PLAYING", "LOADING"):
            self.pauseRequested.emit()
        else:
            self.playRequested.emit()

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_moved(self, value: int) -> None:
        # 拖动期间只更新文字，不 seek，避免每移动一像素就重开解码器
        self.time_label.setText(
            f"{format_mmss(value)} / {format_mmss(self._total_ms)}"
        )

    def _on_slider_released(self) -> None:
        self._dragging = False
        value = int(self.progress.value())
        self.time_label.setText(
            f"{format_mmss(value)} / {format_mmss(self._total_ms)}"
        )
        self.seekRequested.emit(value)

    def _on_volume_slider(self, value: int) -> None:
        db = round(float(value) / VOLUME_SCALE, 1)
        self._volume_db = db
        self._refresh_volume_label()
        self.volumeChanged.emit(db)

    def _reset_volume(self) -> None:
        """双击归零：0 dB 即「旁路」，不是最大音量。"""
        self.set_volume(0.0)
        self.volumeChanged.emit(0.0)

    def _on_signal_toggled(self, checked: bool) -> None:
        self._signal_visible = bool(checked)
        self.signalPanelToggled.emit(self._signal_visible)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed, emit=True)

    def set_collapsed(self, collapsed: bool, *, emit: bool = False) -> None:
        """折叠/展开：折叠态只留播放暂停与曲名，高度 32 px。"""
        self._collapsed = bool(collapsed)
        self.cover.setVisible(not self._collapsed)
        self.artist_label.setVisible(not self._collapsed)
        self.btn_prev.setVisible(not self._collapsed)
        self.btn_next.setVisible(not self._collapsed)
        self.progress_widget.setVisible(not self._collapsed)
        self.volume_widget.setVisible(not self._collapsed)
        self.btn_mode.setVisible(not self._collapsed)
        self.btn_signal.setVisible(not self._collapsed)
        self.btn_collapse.setText("▴" if self._collapsed else "▾")
        self.btn_collapse.setToolTip(
            i18n.tr_text("展开播放条") if self._collapsed else i18n.tr_text("收起播放条")
        )
        self.setFixedHeight(BAR_COLLAPSED_HEIGHT if self._collapsed else BAR_HEIGHT)
        if emit:
            self.collapseToggled.emit(self._collapsed)

    # ------------------------------------------------------------ 刷新

    def _refresh_progress(self) -> None:
        total = max(1, self._total_ms)
        self.progress.setMaximum(total)
        if not self._dragging:
            self.progress.setValue(min(max(0, self._position_ms), total))
        self.time_label.setText(
            f"{format_mmss(self._position_ms)} / {format_mmss(self._total_ms)}"
        )

    def _refresh_volume_label(self) -> None:
        if self._volume_mode == "bypass":
            self.volume_label.setText(i18n.tr_text("旁路"))
        elif abs(self._volume_db) < 0.05:
            self.volume_label.setText(i18n.tr_text("旁路"))
        else:
            self.volume_label.setText(f"{self._volume_db:+.1f} dB")

    def _refresh_enabled(self) -> None:
        """可用性：批处理 / 播放功能关闭 / 旁路模式下控件的灰显规则。"""
        has_track = self._track is not None
        playable = self._enabled and self.settings_playable()
        for btn in (self.btn_play, self.btn_prev, self.btn_next):
            btn.setEnabled(playable)
        self.btn_mode.setEnabled(self._enabled)
        self.progress.setEnabled(playable and has_track)
        software = self._volume_mode == "software"
        self.volume_slider.setEnabled(self._enabled and software)
        if software:
            self.volume_slider.setToolTip(i18n.tr_text("音量"))
        else:
            self.volume_slider.setToolTip(
                i18n.tr_text("软件音量已旁路：系统音量仍可能衰减")
            )
        self._refresh_volume_label()

    def settings_playable(self) -> bool:
        """无曲目时仍允许按播放（会起播队列当前项），所以只受 ``_enabled`` 限制。"""
        return bool(self._enabled)

    def _mode_text(self, mode: str) -> str:
        """模式说明（tooltip 用完整文案，按钮上只放图标）。"""
        key = {
            "sequential": "顺序播放：播到队尾停止",
            "repeat_one": "单曲循环：当前曲目重复播放",
            "repeat_all": "列表循环：播完从头再来",
            "shuffle": "随机播放：一轮内不重复",
        }.get(mode, "顺序播放：播到队尾停止")
        return i18n.tr_text(key)

    def _render_cover(self) -> None:
        """封面：有字节就画，没有就画占位（音乐符号），不留空白。"""
        if self._cover_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(self._cover_bytes):
                self.cover.setPixmap(
                    pixmap.scaled(
                        COVER_SIZE,
                        COVER_SIZE,
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )
                )
                return
        self.cover.setPixmap(QPixmap())
        self.cover.setText("♪" if not self._collapsed else "")

    # ---- 主题切换（主窗口 apply_theme 后调用）----

    def apply_theme(self) -> None:
        self._restyle()

    # ---- 事件 ----

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide_text()

    def _elide_text(self) -> None:
        """窄窗口下用省略号截断曲名（QLabel 默认会直接裁掉，看不出被截）。"""
        if not self._track:
            return
        metrics = QFontMetrics(self.title_label.font())
        width = max(60, self.text_widget.width())
        title = str(self._track.get("title") or "")
        if title:
            self.title_label.setText(metrics.elidedText(title, Qt.ElideRight, width))
        artist = str(self._track.get("artist") or self._track.get("album") or "")
        if artist:
            self.artist_label.setText(
                QFontMetrics(self.artist_label.font()).elidedText(artist, Qt.ElideRight, width)
            )


__all__ = ["PlayerBar", "MODE_ORDER", "MODE_GLYPH", "format_mmss", "BAR_HEIGHT"]
