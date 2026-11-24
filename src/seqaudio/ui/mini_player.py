"""迷你播放条：无边框、圆角、呼吸发光的独立小窗。

设计约束
--------
* **绝不新建引擎**：本窗口不 import ``seqaudio.player``，只发信号给主窗口，
  由主窗口复用同一个 ``PlayerBridge``。
* **不复用 PlayerBar**：主条是横向宽布局，塞进 440 px 会被压缩裁切（上一版
  的"按钮显示不全 / 播放键变整块蓝"就是这么来的）。这里全部控件自绘，尺寸
  固定，图标用 ``QPainter`` 画，不依赖字体。
* 三行歌词直接读 ``PlayerLyricsPanel._lines / _current`` —— 同一个对象，
  LRC、偏移、联网结果天然一致。
* 配色全部来自 ``theme``；切换主题时 ``refresh_theme()`` 重刷。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .glyphs import draw_glyph as _draw_glyph
from .player_bar import VOLUME_SCALE, format_mmss

MINI_WIDTH = 440
MINI_HEIGHT = 260
CARD_MARGIN = 16
CARD_RADIUS = 14
LYRIC_LINES = 3
OPACITY_MIN = 10
OPACITY_MAX = 100
OPACITY_DEFAULT = 80

# ---- 左上角旋转封面（v1.8.2）----
#: 转盘直径（px）。34~38 px 是标题行在不挤压按钮/标题截断前提下放得下的尺寸。
SPIN_COVER_SIZE = 36
#: 匀速旋转角速度（度/秒）。约 4 s 一圈：能看出在转，又不晃眼。
SPIN_SPEED_DPS = 90.0
#: 旋转定时器周期（ms，≈30 fps）。只重绘一个 36 px 的小控件，开销可忽略。
SPIN_TICK_MS = 33
#: 封面图占盘面的直径比例（黑胶盘露出一圈纹理环）。
SPIN_COVER_RATIO = 0.66

MODES = ("sequential", "shuffle", "repeat_one", "repeat_all")
_MODE_ICON = {
    "sequential": "mode_seq",
    "shuffle": "mode_shuffle",
    "repeat_one": "mode_one",
    "repeat_all": "mode_all",
}


class _IconButton(QAbstractButton):
    """自绘圆形按钮：固定尺寸 + hover/pressed 反馈，图标不依赖字体。"""

    def __init__(
        self,
        icon_key: str,
        size: int = 30,
        *,
        accent: bool = False,
        tip: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.icon_key = icon_key
        self._accent = bool(accent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)
        if tip:
            self.setToolTip(tip)

    def sizeHint(self):  # noqa: N802 - Qt 命名
        return self.size()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        hovered = self.underMouse()
        pressed = self.isDown()
        if self._accent:
            fill = QColor(theme.ACCENT_HOVER if hovered else theme.ACCENT)
            if pressed:
                fill = fill.darker(115)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(rect)
            color = QColor(theme.BG_BASE)
        else:
            if pressed:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.SURFACE_ACTIVE))
                painter.drawEllipse(rect)
            elif hovered:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.SURFACE_HOVER))
                painter.drawEllipse(rect)
            color = QColor(theme.FG if hovered else theme.FG_DIM)
        _draw_glyph(painter, self.icon_key, rect.adjusted(7.0, 7.0, -7.0, -7.0), color)


class _SeekSlider(QSlider):
    """进度条：点击任意位置直接跳到该处（QSlider 默认只翻页）。"""

    jumpRequested = Signal(int)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            span = max(1, self.width())
            ratio = min(1.0, max(0.0, event.position().x() / span))
            value = self.minimum() + round(ratio * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.jumpRequested.emit(int(value))
            event.accept()
            return
        super().mousePressEvent(event)


class _Card(QFrame):
    """圆角卡片：QSS 的 border-radius 不会裁剪顶层窗口，只能自绘。"""

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(theme.SURFACE_CARD))
        gradient.setColorAt(1.0, QColor(theme.BG_PANEL))
        painter.fillPath(path, gradient)
        painter.setPen(QPen(QColor(theme.LINE), 1.0))
        painter.drawPath(path)


class _SpinCover(QWidget):
    """黑胶唱片样式的旋转封面（迷你条标题行最左侧，v1.8.2）。

    行为
    ----
    * 深色圆盘 + 主题色细环纹 + 中心孔；盘面中央叠当前曲目封面图。
    * 播放中匀速旋转；暂停/停止/无曲目时**缓停**（角速度逐拍衰减到 0，
      不瞬间冻结）；拿到新曲目重新起转时同样渐入。
    * 拿不到封面（无曲目/无内嵌图）时画默认唱片纹理——环纹是对称的，另加
      一枚随盘旋转的高亮标记点，让"在转"即使无封面也看得见。
    * 窗口隐藏时停表（``suspend``），重新显示且在播放时恢复（``resume``），
      避免不可见时白白重绘。

    性能：QTimer 驱动 ``update()`` 重绘一个 36 px 控件，≈30 fps，开销可忽略。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(SPIN_COVER_SIZE, SPIN_COVER_SIZE)
        self._angle = 0.0            # 当前旋转角（度）
        self._speed = 0.0            # 当前角速度（度/秒），缓停/渐起都靠它
        self._target_speed = 0.0     # 目标角速度：播放中 = SPIN_SPEED_DPS，否则 0
        self._pixmap: Optional[QPixmap] = None
        self._timer = QTimer(self)
        self._timer.setInterval(SPIN_TICK_MS)
        self._timer.setTimerType(Qt.CoarseTimer)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ 数据

    def set_cover(self, data: Optional[bytes | bytearray]) -> None:
        """用当前曲目的封面字节（内嵌图）更新盘面；拿不到就画默认纹理。"""
        pixmap = QPixmap()
        if data and isinstance(data, (bytes, bytearray)) and pixmap.loadFromData(bytes(data)):
            self._pixmap = pixmap
        else:
            self._pixmap = None
        self.update()

    def set_spinning(self, spinning: bool) -> None:
        """播放态切换：``True`` 渐入匀速旋转，``False`` 缓停。"""
        self._target_speed = SPIN_SPEED_DPS if spinning else 0.0
        if spinning:
            self.resume()
        else:
            self._maybe_stop_timer()

    def suspend(self) -> None:
        """窗口隐藏时停表（不清角度，恢复时从原角度继续）。"""
        self._timer.stop()

    def resume(self) -> None:
        """需要转动时恢复定时器（正在旋转或刚切到播放态）。"""
        if self._target_speed > 0.0 and not self._timer.isActive():
            self._timer.start()

    # ------------------------------------------------------------ 动画

    def _tick(self) -> None:
        # 指数趋近目标速度：起转渐入、停止渐出（"缓停"，不瞬间冻结）
        self._speed += (self._target_speed - self._speed) * 0.22
        if self._target_speed <= 0.0 and abs(self._speed) < 1.0:
            self._speed = 0.0
            self._timer.stop()
            if abs(self._angle - round(self._angle)) < 360.0:
                self.update()
            return
        self._angle = (self._angle + self._speed * (SPIN_TICK_MS / 1000.0)) % 360.0
        self.update()

    def _maybe_stop_timer(self) -> None:
        """目标速度为 0 且已基本停转时停表；仍在滑行则让 ``_tick`` 收尾。"""
        if self._target_speed <= 0.0 and abs(self._speed) < 1.0:
            self._speed = 0.0
            self._timer.stop()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = float(min(self.width(), self.height()))
        radius = side / 2.0 - 0.5
        center = QPointF(self.width() / 2.0, self.height() / 2.0)

        # ---- 盘体：深色圆盘 + 边缘描线 ----
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_BASE))
        painter.drawEllipse(center, radius, radius)
        painter.setPen(QPen(QColor(theme.LINE), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, radius - 0.5, radius - 0.5)

        painter.save()
        painter.translate(center)
        painter.rotate(self._angle)

        # ---- 环纹：两圈主题色细环（黑胶沟槽的示意）----
        groove_color = QColor(theme.ACCENT)
        groove_color.setAlpha(70)
        groove = QPen(groove_color)
        groove.setWidthF(1.0)
        painter.setPen(groove)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0.0, 0.0), radius * 0.80, radius * 0.80)
        painter.drawEllipse(QPointF(0.0, 0.0), radius * 0.60, radius * 0.60)

        # ---- 盘面中央的封面（随盘旋转）----
        cover_radius = radius * SPIN_COVER_RATIO
        if self._pixmap is not None and not self._pixmap.isNull():
            clip = QPainterPath()
            clip.addEllipse(QPointF(0.0, 0.0), cover_radius, cover_radius)
            painter.setClipping(True)
            painter.setClipPath(clip)
            source = QRectF(self._pixmap.rect())
            target = QRectF(-cover_radius, -cover_radius, cover_radius * 2.0, cover_radius * 2.0)
            painter.drawPixmap(target, self._pixmap, source)
            painter.setClipping(False)
            # 封面外缘压一圈细环，和盘面区分
            painter.setPen(QPen(QColor(theme.LINE), 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), cover_radius, cover_radius)
        else:
            # 默认唱片纹理：中心标签区 + 一枚随盘旋转的高亮标记点
            painter.setPen(Qt.NoPen)
            label_color = QColor(theme.SURFACE_ACTIVE)
            painter.setBrush(label_color)
            painter.drawEllipse(QPointF(0.0, 0.0), cover_radius, cover_radius)
            marker = QColor(theme.ACCENT)
            marker.setAlpha(200)
            painter.setBrush(marker)
            painter.drawEllipse(
                QPointF(radius * 0.72, 0.0), max(1.2, radius * 0.09), max(1.2, radius * 0.09)
            )
        painter.restore()

        # ---- 中心孔 ----
        hole = QColor(theme.BG_BASE)
        painter.setPen(QPen(QColor(theme.LINE), 1.0))
        painter.setBrush(hole)
        hole_r = max(2.0, radius * 0.14)
        painter.drawEllipse(center, hole_r, hole_r)


class MiniPlayerWindow(QWidget):
    """迷你形态：标题栏 + 进度 + 全套播放控制 + 三行歌词 + 透明度。"""

    playRequested = Signal()
    pauseRequested = Signal()
    stopRequested = Signal()
    prevRequested = Signal()
    nextRequested = Signal()
    seekRequested = Signal(int)
    volumeChanged = Signal(float)
    modeChanged = Signal(str)
    playlistRequested = Signal()
    viewModeToggled = Signal()
    closeRequested = Signal()

    def __init__(self, settings, lyrics_panel, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self.lyrics_panel = lyrics_panel

        self._state = "IDLE"
        self._mode = "sequential"
        self._track: Optional[dict] = None
        self._total_ms = 0
        self._position_ms = 0
        self._volume_db = 0.0
        self._volume_mode = "bypass"
        self._enabled = True
        self._nav_enabled = False
        self._dragging_slider = False
        self._drag_origin = QPoint()
        self._dragging = False

        self.setObjectName("miniPlayer")
        self.setWindowTitle(i18n.tr("ui_mini_title"))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(MINI_WIDTH, MINI_HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(CARD_MARGIN, CARD_MARGIN, CARD_MARGIN, CARD_MARGIN)
        outer.setSpacing(0)
        self.card = _Card(self)
        self.card.setObjectName("miniCard")
        outer.addWidget(self.card)

        self._shadow = QGraphicsDropShadowEffect(self.card)
        self._shadow.setBlurRadius(18.0)
        self._shadow.setOffset(0.0, 0.0)
        self._shadow.setColor(self._glow_color(120))
        self.card.setGraphicsEffect(self._shadow)

        self._glow = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._glow.setDuration(2400)
        self._glow.setStartValue(12.0)
        self._glow.setEndValue(30.0)
        self._glow.setEasingCurve(QEasingCurve.InOutSine)
        self._glow.setLoopCount(-1)

        inner = QVBoxLayout(self.card)
        inner.setContentsMargins(10, 8, 10, 8)
        inner.setSpacing(4)
        inner.addLayout(self._build_title_row())
        inner.addLayout(self._build_progress_row())
        inner.addLayout(self._build_controls_row())
        inner.addLayout(self._build_lyrics())
        inner.addLayout(self._build_opacity_row())

        self._opacity = OPACITY_DEFAULT
        self.apply_opacity(int(getattr(settings, "playback_mini_opacity", OPACITY_DEFAULT) or OPACITY_DEFAULT))
        self.refresh_theme()
        self.set_track(None)
        self.set_state("IDLE")
        self.update_lyrics()

    # ------------------------------------------------------------ 构建

    def _glow_color(self, alpha: int) -> QColor:
        color = QColor(theme.ACCENT)
        color.setAlpha(max(0, min(255, int(alpha))))
        return color

    def _build_title_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(6)
        # 左上角旋转封面（v1.8.2）：黑胶转盘，播放中匀速旋转
        self.spin_cover = _SpinCover()
        row.addWidget(self.spin_cover, 0, Qt.AlignVCenter)
        self.title_label = QLabel(i18n.tr_text("未在播放"))
        self.title_label.setObjectName("miniTitle")
        font = QFont(self.title_label.font())
        font.setBold(True)
        font.setPixelSize(13)
        self.title_label.setFont(font)
        self.title_label.setMinimumWidth(0)
        row.addWidget(self.title_label, 1)

        self.btn_min = _IconButton("min", 26, tip=i18n.tr("ui_mini_minimize"))
        self.btn_max = _IconButton("max", 26, tip=i18n.tr("ui_mini_maximize"))
        self.btn_close = _IconButton("close", 26, tip=i18n.tr("ui_mini_close"))
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self.viewModeToggled.emit)
        self.btn_close.clicked.connect(self.closeRequested.emit)
        for button in (self.btn_min, self.btn_max, self.btn_close):
            row.addWidget(button)
        return row

    def _build_progress_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(6)
        self.progress = _SeekSlider(Qt.Horizontal)
        self.progress.setObjectName("miniProgress")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setCursor(Qt.PointingHandCursor)
        self.progress.jumpRequested.connect(self._on_seek_jump)
        self.progress.sliderPressed.connect(self._on_slider_pressed)
        self.progress.sliderReleased.connect(self._on_slider_released)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("miniTime")
        time_font = QFont(self.time_label.font())
        time_font.setPixelSize(10)
        self.time_label.setFont(time_font)
        row.addWidget(self.progress, 1)
        row.addWidget(self.time_label)
        return row

    def _build_controls_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)
        self.btn_view = _IconButton("view", 30, tip=i18n.tr("ui_view_main"))
        self.btn_prev = _IconButton("prev", 30, tip=i18n.tr("ui_mini_prev"))
        self.btn_play = _IconButton("play", 38, accent=True, tip=i18n.tr("ui_mini_play"))
        self.btn_stop = _IconButton("stop", 30, tip=i18n.tr("ui_mini_stop"))
        self.btn_next = _IconButton("next", 30, tip=i18n.tr("ui_mini_next"))
        self.btn_mode = _IconButton("mode_seq", 30, tip=i18n.tr("ui_mini_mode"))
        self.btn_playlist = _IconButton("list", 30, tip=i18n.tr("ui_playlist"))
        self.btn_view.clicked.connect(self.viewModeToggled.emit)
        self.btn_prev.clicked.connect(self.prevRequested.emit)
        self.btn_play.clicked.connect(self._on_play_clicked)
        self.btn_stop.clicked.connect(self.stopRequested.emit)
        self.btn_next.clicked.connect(self.nextRequested.emit)
        self.btn_mode.clicked.connect(self.cycle_mode)
        self.btn_playlist.clicked.connect(self.playlistRequested.emit)
        for button in (self.btn_view, self.btn_prev, self.btn_play,
                       self.btn_stop, self.btn_next, self.btn_mode, self.btn_playlist):
            row.addWidget(button)
        row.addStretch(1)

        self.btn_volume = _IconButton("volume", 24, tip=i18n.tr("ui_mini_volume"))
        self.btn_volume.clicked.connect(self._toggle_mute)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setObjectName("miniVolume")
        self.volume_slider.setRange(-60 * VOLUME_SCALE, 0)
        self.volume_slider.setValue(0)
        self.volume_slider.setFixedWidth(92)
        self.volume_slider.setToolTip(i18n.tr("ui_mini_volume"))
        self.volume_slider.valueChanged.connect(self._on_volume_slider)
        row.addWidget(self.btn_volume)
        row.addWidget(self.volume_slider)
        return row

    def _build_lyrics(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setContentsMargins(8, 2, 8, 0)
        box.setSpacing(0)
        self.lyric_labels: list[QLabel] = []
        for _ in range(LYRIC_LINES):
            label = QLabel("")
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            label.setMinimumHeight(24)
            box.addWidget(label)
            self.lyric_labels.append(label)
        return box

    def _build_opacity_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(6)
        self.opacity_label = QLabel(i18n.tr("ui_mini_opacity"))
        self.opacity_label.setObjectName("miniHint")
        font = QFont(self.opacity_label.font())
        font.setPixelSize(10)
        self.opacity_label.setFont(font)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setObjectName("miniOpacity")
        self.opacity_slider.setRange(OPACITY_MIN, OPACITY_MAX)
        self.opacity_slider.setValue(OPACITY_DEFAULT)
        self.opacity_slider.setFixedWidth(110)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.opacity_slider.sliderReleased.connect(self._persist_opacity)
        self.opacity_value = QLabel(f"{OPACITY_DEFAULT}%")
        self.opacity_value.setObjectName("miniHint")
        self.opacity_value.setFont(font)
        row.addWidget(self.opacity_label)
        row.addWidget(self.opacity_slider)
        row.addWidget(self.opacity_value)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------ 播放控制

    def _on_play_clicked(self) -> None:
        if self._state == "PLAYING":
            self.pauseRequested.emit()
        else:
            self.playRequested.emit()

    def _on_seek_jump(self, value: int) -> None:
        self._position_ms = int(value)
        self._refresh_progress()
        self.seekRequested.emit(int(value))

    def _on_slider_pressed(self) -> None:
        self._dragging_slider = True

    def _on_slider_released(self) -> None:
        self._dragging_slider = False
        self.seekRequested.emit(int(self.progress.value()))

    def _on_volume_slider(self, value: int) -> None:
        db = float(value) / float(VOLUME_SCALE)
        self._volume_db = db
        self.volumeChanged.emit(db)

    def _toggle_mute(self) -> None:
        if self._volume_db <= -59.5:
            self.set_volume(0.0)
            self.volumeChanged.emit(0.0)
        else:
            self.set_volume(-60.0)
            self.volumeChanged.emit(-60.0)

    def cycle_mode(self) -> None:
        try:
            index = MODES.index(self._mode)
        except ValueError:
            index = 0
        self.set_mode(MODES[(index + 1) % len(MODES)])
        self.modeChanged.emit(self._mode)

    # ------------------------------------------------------------ 状态同步（主窗口调用）

    def set_state(self, state: str) -> None:
        self._state = str(state or "IDLE")
        playing = self._state in ("PLAYING", "LOADING")
        self.btn_play.icon_key = "pause" if playing else "play"
        self.btn_play.setToolTip(i18n.tr("ui_mini_pause" if playing else "ui_mini_play"))
        self.btn_play.update()
        # 转盘跟随播放态：播放中匀速旋转，暂停/停止/无曲目缓停（v1.8.2）
        self.spin_cover.set_spinning(playing)
        self._refresh_enabled()

    def set_track(self, track: Optional[dict]) -> None:
        self._track = dict(track) if isinstance(track, dict) else None
        if self._track:
            title = str(self._track.get("title") or "").strip()
            if not title:
                path_text = str(self._track.get("path") or "")
                title = path_text.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                title = title.rsplit(".", 1)[0] if "." in title else title
            artist = str(self._track.get("artist") or "").strip()
            album = str(self._track.get("album") or "").strip()
            self.title_label.setText(title or i18n.tr_text("未在播放"))
            self.title_label.setToolTip(" · ".join(p for p in (title, artist, album) if p))
            duration_ms = int(self._track.get("duration_ms") or 0)
            if duration_ms <= 0:
                duration_ms = int(round(float(self._track.get("duration") or 0.0) * 1000.0))
            self._total_ms = max(0, duration_ms)
            # 转盘封面：主窗口在 trackChanged 里已把内嵌封面字节放进 "cover"
            # （与 PlayerBar 同源同拍），拿不到就画默认唱片纹理
            cover = self._track.get("cover")
            self.spin_cover.set_cover(cover if isinstance(cover, (bytes, bytearray)) else None)
        else:
            self.title_label.setText(i18n.tr_text("未在播放"))
            self.title_label.setToolTip("")
            self._total_ms = 0
            self._position_ms = 0
            self.spin_cover.set_cover(None)
        self._refresh_progress()
        self._refresh_enabled()

    def set_position(self, ms: int, total: Optional[int] = None) -> None:
        if total is not None and int(total) > 0:
            self._total_ms = int(total)
        self._position_ms = max(0, int(ms or 0))
        self._refresh_progress()

    def set_volume(self, db: float) -> None:
        self._volume_db = float(db)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(round(self._volume_db * VOLUME_SCALE)))
        self.volume_slider.blockSignals(False)
        self.btn_volume.icon_key = "mute" if self._volume_db <= -59.5 else "volume"
        self.btn_volume.update()

    def set_volume_mode(self, mode: str) -> None:
        self._volume_mode = str(mode or "bypass")

    def set_mode(self, mode: str) -> None:
        value = str(mode or "sequential")
        if value not in MODES:
            value = "sequential"
        self._mode = value
        self.btn_mode.icon_key = _MODE_ICON[value]
        self.btn_mode.setToolTip(i18n.tr(f"ui_mini_mode_{value}"))
        self.btn_mode.update()

    def set_playback_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._refresh_enabled()

    def set_nav_enabled(self, enabled: bool) -> None:
        self._nav_enabled = bool(enabled)
        self._refresh_enabled()

    def current_state(self) -> str:
        return self._state

    def current_mode(self) -> str:
        return self._mode

    def volume_db(self) -> float:
        return self._volume_db

    def _refresh_progress(self) -> None:
        total = max(1, self._total_ms)
        self.progress.setMaximum(total)
        if not self._dragging_slider:
            self.progress.setValue(min(max(0, self._position_ms), total))
        self.time_label.setText(
            f"{format_mmss(self._position_ms)} / {format_mmss(self._total_ms)}"
        )

    def _refresh_enabled(self) -> None:
        playable = self._enabled
        self.btn_play.setEnabled(playable)
        self.btn_stop.setEnabled(playable and self._state not in ("IDLE", ""))
        for button in (self.btn_prev, self.btn_next):
            button.setEnabled(playable and self._nav_enabled)
        self.progress.setEnabled(playable and self._track is not None)
        self.volume_slider.setEnabled(playable)

    # ------------------------------------------------------------ 透明度

    def apply_opacity(self, value: int) -> None:
        value = max(OPACITY_MIN, min(OPACITY_MAX, int(value)))
        self._opacity = value
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(value)
        self.opacity_slider.blockSignals(False)
        self.opacity_value.setText(f"{value}%")
        try:
            self.setWindowOpacity(value / 100.0)
        except Exception:  # noqa: BLE001 - 个别平台不支持，不能因此崩
            pass

    def _on_opacity_changed(self, value: int) -> None:
        self.apply_opacity(int(value))

    def _persist_opacity(self) -> None:
        try:
            self.settings.playback_mini_opacity = int(self._opacity)
            self.settings.save()
        except OSError:
            pass

    # ------------------------------------------------------------ 歌词

    def update_lyrics(self) -> None:
        lines = list(getattr(self.lyrics_panel, "_lines", []) or [])
        current = int(getattr(self.lyrics_panel, "_current", -1) or -1)
        if not lines:
            self.lyric_labels[0].setText(i18n.tr("lyr_mini_empty"))
            self._style_lyric(self.lyric_labels[0], active=False, muted=True)
            for label in self.lyric_labels[1:]:
                label.setText("")
            return
        for index, label in enumerate(self.lyric_labels):
            row = current - 1 + index
            text = str(lines[row][1] or "♪") if 0 <= row < len(lines) else ""
            label.setText(text)
            self._style_lyric(label, active=(row == current and row >= 0))

    def _style_lyric(self, label: QLabel, *, active: bool, muted: bool = False) -> None:
        font = QFont(label.font())
        font.setBold(active)
        font.setPixelSize(13 if active else 11)
        label.setFont(font)
        if muted:
            color = theme.FG_MUTE
        elif active:
            color = theme.ACCENT
        else:
            color = theme.FG_DIM
        label.setStyleSheet(f"color:{color}; background:transparent;")

    # ------------------------------------------------------------ 主题 / 语言

    def refresh_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#miniPlayer {{ background: transparent; }}
            QLabel#miniTitle {{ color: {theme.FG}; }}
            QLabel#miniTime {{ color: {theme.FG_MUTE}; }}
            QLabel#miniHint {{ color: {theme.FG_MUTE}; }}
            QSlider#miniProgress::groove:horizontal {{
                height: 3px; background: {theme.LINE_SOFT}; border-radius: 1px;
            }}
            QSlider#miniProgress::sub-page:horizontal {{
                background: {theme.ACCENT}; border-radius: 1px;
            }}
            QSlider#miniProgress::handle:horizontal {{
                width: 9px; margin: -4px 0; border-radius: 4px; background: {theme.CYAN};
            }}
            QSlider#miniVolume::groove:horizontal {{
                height: 3px; background: {theme.LINE_SOFT}; border-radius: 1px;
            }}
            QSlider#miniVolume::sub-page:horizontal {{
                background: {theme.CYAN}; border-radius: 1px;
            }}
            QSlider#miniVolume::handle:horizontal {{
                width: 8px; margin: -3px 0; border-radius: 4px; background: {theme.FG_DIM};
            }}
            QSlider#miniOpacity::groove:horizontal {{
                height: 3px; background: {theme.LINE_SOFT}; border-radius: 1px;
            }}
            QSlider#miniOpacity::sub-page:horizontal {{
                background: {theme.ACCENT_DIM}; border-radius: 1px;
            }}
            QSlider#miniOpacity::handle:horizontal {{
                width: 8px; margin: -3px 0; border-radius: 4px; background: {theme.FG_DIM};
            }}
            """
        )
        self._shadow.setColor(self._glow_color(120))
        self.card.update()
        for button in (
            self.btn_view, self.btn_prev, self.btn_play, self.btn_stop,
            self.btn_next, self.btn_mode, self.btn_volume,
            self.btn_min, self.btn_max, self.btn_close,
        ):
            button.update()
        self.update_lyrics()

    def refresh_texts(self) -> None:
        self.setWindowTitle(i18n.tr("ui_mini_title"))
        self.btn_view.setToolTip(i18n.tr("ui_view_main"))
        self.btn_prev.setToolTip(i18n.tr("ui_mini_prev"))
        self.btn_next.setToolTip(i18n.tr("ui_mini_next"))
        self.btn_stop.setToolTip(i18n.tr("ui_mini_stop"))
        self.btn_volume.setToolTip(i18n.tr("ui_mini_volume"))
        self.btn_min.setToolTip(i18n.tr("ui_mini_minimize"))
        self.btn_max.setToolTip(i18n.tr("ui_mini_maximize"))
        self.btn_close.setToolTip(i18n.tr("ui_mini_close"))
        self.opacity_label.setText(i18n.tr("ui_mini_opacity"))
        self.set_state(self._state)
        self.set_mode(self._mode)
        self.update_lyrics()

    # ------------------------------------------------------------ 生命周期

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        try:
            self.setWindowOpacity(self._opacity / 100.0)
        except Exception:  # noqa: BLE001
            pass
        if self._glow.state() != QPropertyAnimation.Running:
            self._glow.start()
        # 转盘：窗口重新可见且在播放时恢复旋转（隐藏期间定时器已停）
        self.spin_cover.resume()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._glow.stop()
        self._shadow.setBlurRadius(18.0)
        # 转盘停表：窗口不可见时不必重绘（恢复显示时按播放态续转）
        self.spin_cover.suspend()
        super().hideEvent(event)

    # ------------------------------------------------------------ 拖动

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if isinstance(child, (QAbstractButton, QAbstractSlider, QComboBox)):
                super().mousePressEvent(event)
                return
            self._dragging = True
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, (QAbstractButton, QAbstractSlider, QComboBox)):
                self.viewModeToggled.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


__all__ = [
    "MiniPlayerWindow",
    "MINI_WIDTH",
    "MINI_HEIGHT",
    "CARD_MARGIN",
    "CARD_RADIUS",
    "OPACITY_MIN",
    "OPACITY_MAX",
    "OPACITY_DEFAULT",
    "SPIN_COVER_SIZE",
    "SPIN_SPEED_DPS",
    "SPIN_TICK_MS",
]
