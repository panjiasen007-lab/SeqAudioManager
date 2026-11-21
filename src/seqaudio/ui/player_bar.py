"""底部播放条：封面 / 曲目 / 传输控制 / 进度 / 音量 / 模式 / EQ。

设计约束
--------
* **不持有引擎**：本控件只发信号、只接收 ``set_*`` 方法，命令转发全部由
  ``main_window`` 经 ``PlayerBridge`` 完成。这样播放条可以在没有引擎的测试里
  独立渲染（``tests/test_player_ui.py`` 就是这么用的）。
* 高度 56 px（折叠 32 px 仅供程序化使用：「收起播放条」按钮已按用户要求移除，
  改成了 EQ 按钮；``set_collapsed`` 保留是因为自适应布局与旧配置仍在调用）。
* **自绘悬浮玻璃卡**：``paintEvent`` 画 16 px 圆角 + 竖向渐变 + 1 px accent 描边 +
  顶部一条水平渐变高光。按钮全部换成 ``glyphs.IconButton``（矢量图标、自绘圆形），
  因此播放态不再出现「一坨实心蓝」。
* 颜色全部来自 ``theme``，不写死色值 —— 11 套主题切换时（TC-GUI-27）才跟着变。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .glyphs import IconButton, qcolor

log = logging.getLogger(__name__)

#: 播放条展开 / 折叠高度（px）
BAR_HEIGHT = 56
BAR_COLLAPSED_HEIGHT = 32
#: 封面边长（px，高 DPI 下按 devicePixelRatio 放大位图）
COVER_SIZE = 40
#: 卡片圆角半径（px）
CARD_RADIUS = 16

#: 模式循环顺序（FR-PL-002 的四种）
MODE_ORDER: tuple[str, ...] = ("sequential", "repeat_one", "repeat_all", "shuffle")

#: 模式 -> 图标（单字符，避免依赖 emoji 字体；同时供无障碍与既有用例断言）
MODE_GLYPH = {
    "sequential": "→",
    "repeat_one": "①",
    "repeat_all": "↻",
    "shuffle": "⤨",
}

#: 模式 -> 矢量图标键（走 ``glyphs.draw_glyph``）
MODE_ICON = {
    "sequential": "mode_seq",
    "repeat_one": "mode_one",
    "repeat_all": "mode_all",
    "shuffle": "mode_shuffle",
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


class _ProgressSlider(QSlider):
    """进度条：点击任意位置直接跳到该处。

    ``QSlider`` 默认点击只把 handle 移动一页 —— 对进度条来说就是"点了没反应"，
    用户想听哪就点哪的预期完全落空，所以重写按下事件按比例换算并发出跳转。
    """

    jumpRequested = Signal(int)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            from PySide6.QtWidgets import QStyle, QStyleOptionSlider

            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            groove = self.style().subControlRect(
                QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self
            )
            handle = self.style().subControlRect(
                QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self
            )
            span = max(1, groove.width() - handle.width())
            x = event.position().x() - groove.x() - handle.width() / 2
            ratio = min(1.0, max(0.0, x / span))
            value = self.minimum() + round(ratio * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.jumpRequested.emit(value)
            event.accept()
            return
        super().mousePressEvent(event)


class PlayerBar(QFrame):
    """底部单行播放条（自绘圆角玻璃卡）。

    Signals:
        playRequested: 请求播放（无曲目或已暂停时）。
        pauseRequested: 请求暂停。
        nextRequested / prevRequested: 上/下一首。
        seekRequested(int): 拖进度条松手后的目标位置（毫秒）。
        volumeChanged(float): 音量（dB，0 表示旁路）。
        modeChanged(str): 模式键（``sequential`` / ``repeat_one`` /
            ``repeat_all`` / ``shuffle``）。
        collapseToggled(bool): 折叠状态变化（按钮已移除，仅供程序化折叠/布局用）。
        signalPanelToggled(bool): 「信号路径」开关按钮。
        viewModeToggled(): 切到迷你条。
        eqRequested: 请求打开均衡器面板（最右侧 EQ 按钮）。
    """

    playRequested = Signal()
    pauseRequested = Signal()
    stopRequested = Signal()
    nextRequested = Signal()
    prevRequested = Signal()
    seekRequested = Signal(int)
    volumeChanged = Signal(float)
    modeChanged = Signal(str)
    collapseToggled = Signal(bool)
    signalPanelToggled = Signal(bool)
    #: 视图切换（主界面 ↔ 迷你条）。两个形态的按钮都在「上一曲」左边。
    viewModeToggled = Signal()
    #: 均衡器面板（替代原来的「收起播放条」按钮）。
    eqRequested = Signal()

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
        self._nav_enabled = False
        self._volume_mode = "bypass"
        self._cover_bytes: Optional[bytes] = None
        self._signal_visible = False

        metrics = QFontMetrics(self.font())
        self._unit = max(8, metrics.height() // 2 + 2)
        self._button_size = max(30, int(round(metrics.height() * 1.5)))

        self._build()
        self._restyle()
        self.set_track(None)
        self.set_state("IDLE")

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 4, 14, 4)
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
        title_font.setWeight(QFont.Weight.DemiBold)
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

        # 传输控制。视图切换按钮固定在「上一曲」左边（主界面/迷你条同位置）
        self.btn_view_mode = IconButton(
            "view", 30, tip=i18n.tr_text("切到迷你"), text="▣"
        )
        self.btn_prev = IconButton("prev", 30, tip=i18n.tr_text("上一首"), text="|◀")
        self.btn_play = IconButton(
            "play", 38, accent=True, tip=i18n.tr_text("播放"), text="▶"
        )
        self.btn_play.setObjectName("playerPlay")
        self.btn_stop = IconButton("stop", 30, tip=i18n.tr_text("停止播放"), text="■")
        self.btn_next = IconButton("next", 30, tip=i18n.tr_text("下一首"), text="▶|")
        self.btn_view_mode.clicked.connect(self.viewModeToggled.emit)
        self.btn_prev.clicked.connect(self.prevRequested.emit)
        self.btn_play.clicked.connect(self._on_play_clicked)
        self.btn_stop.clicked.connect(self.stopRequested.emit)
        self.btn_next.clicked.connect(self.nextRequested.emit)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.btn_view_mode)
        controls.addWidget(self.btn_prev)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_next)
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(controls)
        outer.addWidget(self.controls_widget)

        # 进度条 + 时间
        self.progress = _ProgressSlider(Qt.Horizontal)
        self.progress.setObjectName("playerProgress")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setMinimumWidth(80)
        self.progress.setCursor(Qt.PointingHandCursor)
        self.progress.jumpRequested.connect(self._on_progress_jump)
        self.progress.sliderPressed.connect(self._on_slider_pressed)
        self.progress.sliderReleased.connect(self._on_slider_released)
        self.progress.sliderMoved.connect(self._on_slider_moved)
        self.progress.setToolTip(i18n.tr_text("进度"))

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("hint")
        time_font = QFont(self.font())
        time_font.setStyleHint(QFont.Monospace)
        time_font.setPixelSize(11)
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
        self.volume_slider.setFixedWidth(96)
        self.volume_slider.setCursor(Qt.PointingHandCursor)
        self.volume_slider.setToolTip(i18n.tr_text("音量"))
        self.volume_slider.valueChanged.connect(self._on_volume_slider)
        self.volume_slider.doubleClicked.connect(self._reset_volume)
        self.volume_label = QLabel("旁路")
        self.volume_label.setObjectName("hint")
        self.volume_label.setMinimumWidth(48)
        volume_row = QHBoxLayout()
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(6)
        volume_row.addWidget(self.volume_slider)
        volume_row.addWidget(self.volume_label)
        self.volume_widget = QWidget()
        self.volume_widget.setLayout(volume_row)
        outer.addWidget(self.volume_widget)

        # 模式
        self.btn_mode = IconButton(
            MODE_ICON["sequential"], 30, tip=i18n.tr_text("顺序播放"), text="→"
        )
        self.btn_mode.clicked.connect(self.cycle_mode)
        outer.addWidget(self.btn_mode)

        # 信号路径开关
        self.btn_signal = IconButton("signal", 30, tip=i18n.tr_text("信号路径"), text="≈")
        self.btn_signal.setCheckable(True)
        self.btn_signal.toggled.connect(self._on_signal_toggled)
        outer.addWidget(self.btn_signal)

        # 均衡器（取代原来的「收起播放条」按钮，位置仍在最右）
        self.btn_eq = IconButton("eq", 30, tip=i18n.tr_text("均衡器（20 段）"), text="EQ")
        self.btn_eq.clicked.connect(self.eqRequested.emit)
        outer.addWidget(self.btn_eq)

    # ------------------------------------------------------------ 主题

    def _restyle(self) -> None:
        """按当前主题重刷自绘配色（主题切换后由 ``apply_theme`` 再调一次）。

        注意：这里**只有**滑块与容器的透明规则 —— 按钮是 ``IconButton`` 自绘的，
        任何 ``QPushButton#...`` 的实心背景规则都不能存在（那正是播放键变蓝块的成因）。
        """
        self.setStyleSheet(
            f"""
            QFrame#playerBar {{
                background: transparent;
                border: none;
            }}
            QSlider#playerProgress::groove:horizontal {{
                height: 4px; background: {theme.LINE}; border-radius: 2px;
            }}
            QSlider#playerProgress::sub-page:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {theme.ACCENT}, stop:1 {theme.CYAN});
                border-radius: 2px;
            }}
            QSlider#playerProgress::handle:horizontal {{
                width: 10px; height: 10px; margin: -4px 0; border-radius: 5px;
                background: {theme.CYAN};
                border: 1px solid {theme.FG};
            }}
            QSlider#playerVolume::groove:horizontal {{
                height: 4px; background: {theme.LINE_SOFT}; border-radius: 2px;
            }}
            QSlider#playerVolume::sub-page:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {theme.ACCENT}, stop:1 {theme.CYAN});
                border-radius: 2px;
            }}
            QSlider#playerVolume::handle:horizontal {{
                width: 9px; height: 9px; margin: -3px 0; border-radius: 4px;
                background: {theme.FG_DIM};
            }}
            QSlider#playerVolume::handle:horizontal:hover {{ background: {theme.ACCENT_HOVER}; }}
            """
        )
        self.cover.setStyleSheet(
            f"background:{theme.SURFACE_INPUT}; border:1px solid {theme.LINE_SOFT};"
            f"border-radius:8px; color:{theme.FG_DIM}; font-size:16px;"
        )
        self.time_label.setStyleSheet(
            f"color:{theme.FG_MUTE}; background:transparent; font-size:11px;"
        )
        self.artist_label.setStyleSheet(
            f"color:{theme.FG_MUTE}; background:transparent; font-size:11px;"
        )
        for button in self._icon_buttons():
            button.update()
        self.update()
        self._render_cover()

    def _icon_buttons(self) -> tuple[IconButton, ...]:
        """所有自绘按钮（主题切换时要重绘）。"""
        return (
            self.btn_view_mode,
            self.btn_prev,
            self.btn_play,
            self.btn_stop,
            self.btn_next,
            self.btn_mode,
            self.btn_signal,
            self.btn_eq,
        )

    def refresh_theme(self) -> None:
        """主题切换后调用（颜色来自 ``theme``，必须重算）。"""
        self._restyle()

    # ------------------------------------------------------------ 自绘卡片

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """圆角 16 px 玻璃卡：渐变底 + accent 描边 + 顶部微光高光。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        path = QPainterPath()
        path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, qcolor(theme.SURFACE_CARD, 1.0))
        gradient.setColorAt(1.0, qcolor(theme.SURFACE_PANEL, 1.0))
        painter.fillPath(path, gradient)

        painter.setPen(QPen(qcolor(theme.ACCENT, 0.22), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # 顶部 1 px 水平渐变高光：从透明 → accent → 透明，作为「微光」收口
        if self.width() > 40:
            glow = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
            glow.setColorAt(0.0, qcolor(theme.ACCENT, 0.0))
            glow.setColorAt(0.5, qcolor(theme.ACCENT, 0.55))
            glow.setColorAt(1.0, qcolor(theme.ACCENT, 0.0))
            painter.setPen(Qt.NoPen)
            painter.fillRect(
                QRectF(rect.left() + 12.0, rect.top(), rect.width() - 24.0, 1.0), glow
            )
        painter.end()

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
        """更新状态（``IDLE`` / ``LOADING`` / ``PLAYING`` / ``PAUSED`` / ``ERROR``）。

        播放键的 ``objectName`` **始终**是 ``playerPlay``：外观完全由
        ``IconButton.paintEvent`` 决定，只换图标、加亮描边，绝不切换样式表 ——
        这就是「播放之后键变成一坨蓝色」的根因修复。
        """
        value = str(getattr(state, "value", state) or "IDLE").upper()
        if value not in ("IDLE", "LOADING", "PLAYING", "PAUSED", "ERROR"):
            value = "IDLE"
        self._state = value
        playing = value in ("PLAYING", "LOADING")
        self.btn_play.set_icon("pause" if playing else "play")
        self.btn_play.setText("❚❚" if playing else "▶")
        self.btn_play.setToolTip(
            i18n.tr_text("暂停") if playing else i18n.tr_text("播放")
        )
        # 播放中只让描边更亮（微光），填充仍是半透明
        self.btn_play.set_emphasized(playing)
        has_session = value != "IDLE"
        self.btn_stop.setEnabled(has_session and self._enabled)
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
        """设置播放模式（未知值回落顺序播放）：图标与文字同步。"""
        value = str(getattr(mode, "value", mode) or "sequential")
        if value not in MODE_ORDER:
            value = "sequential"
        self._mode = value
        self.btn_mode.set_icon(MODE_ICON.get(value, "mode_seq"))
        self.btn_mode.setText(MODE_GLYPH.get(value, "→"))
        self.btn_mode.setToolTip(self._mode_text(value))

    def set_playback_enabled(self, enabled: bool) -> None:
        """批处理/功能开关：禁用起播控件（不是隐藏）。"""
        self._enabled = bool(enabled)
        self._refresh_enabled()

    def set_nav_enabled(self, enabled: bool) -> None:
        """上一首/下一首可用性：列表没有歌曲时置灰（用户反馈 2）。"""
        self._nav_enabled = bool(enabled)
        self._refresh_enabled()

    def set_view_mode_hint(self, text: str) -> None:
        """切换按钮的 tooltip：主界面写「切到迷你」，迷你条写「回主界面」。"""
        self.btn_view_mode.setToolTip(text)

    def set_signal_panel_visible(self, visible: bool) -> None:
        """同步「信号路径」开关按钮的按下态（不触发信号）。"""
        self._signal_visible = bool(visible)
        blocked = self.btn_signal.blockSignals(True)
        self.btn_signal.setChecked(self._signal_visible)
        self.btn_signal.blockSignals(blocked)
        self.btn_signal.set_emphasized(self._signal_visible)

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

    def _on_progress_jump(self, value: int) -> None:
        """点击进度条：立即 seek 到点击处（拖动仍走 press→release）。"""
        self.time_label.setText(
            f"{format_mmss(value)} / {format_mmss(self._total_ms)}"
        )
        self.seekRequested.emit(value)

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
        self.btn_signal.set_emphasized(self._signal_visible)
        self.signalPanelToggled.emit(self._signal_visible)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed, emit=True)

    def set_collapsed(self, collapsed: bool, *, emit: bool = False) -> None:
        """折叠/展开（**程序化**，界面上已无折叠按钮）。

        折叠态只留播放暂停与曲名，高度 32 px。保留这个能力是因为自适应布局与
        旧配置文件仍会调用它；没有了按钮，用户不会再被卡在 32 px 无法展开。
        """
        self._collapsed = bool(collapsed)
        self.cover.setVisible(not self._collapsed)
        self.artist_label.setVisible(not self._collapsed)
        self.btn_prev.setVisible(not self._collapsed)
        self.btn_next.setVisible(not self._collapsed)
        self.progress_widget.setVisible(not self._collapsed)
        self.volume_widget.setVisible(not self._collapsed)
        self.btn_mode.setVisible(not self._collapsed)
        self.btn_signal.setVisible(not self._collapsed)
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
        self.btn_play.setEnabled(playable)
        for btn in (self.btn_prev, self.btn_next):
            btn.setEnabled(playable and self._nav_enabled)
        self.btn_stop.setEnabled(playable and self._state != "IDLE")
        self.btn_mode.setEnabled(self._enabled)
        # EQ 面板与播放总开关无关：批处理时也允许看曲线
        self.btn_eq.setEnabled(True)
        self.progress.setEnabled(playable and has_track)
        # 音量滑块**旁路模式也可用**：拖动 = 顺手启用软件音量（main_window 落地），
        # 双击 = 归零回旁路。以前旁路时直接禁用，用户会以为根本没有音量功能。
        self.volume_slider.setEnabled(self._enabled)
        if self._volume_mode == "software":
            self.volume_slider.setToolTip(i18n.tr_text("音量"))
        else:
            self.volume_slider.setToolTip(
                i18n.tr_text("软件音量已旁路：拖动滑块即启用软件音量")
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


__all__ = [
    "PlayerBar",
    "MODE_ORDER",
    "MODE_GLYPH",
    "MODE_ICON",
    "format_mmss",
    "BAR_HEIGHT",
    "BAR_COLLAPSED_HEIGHT",
    "CARD_RADIUS",
]
