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
    Signal,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
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
from .player_bar import VOLUME_SCALE, format_mmss

MINI_WIDTH = 440
MINI_HEIGHT = 260
CARD_MARGIN = 16
CARD_RADIUS = 14
LYRIC_LINES = 3
OPACITY_MIN = 10
OPACITY_MAX = 100
OPACITY_DEFAULT = 80

MODES = ("sequential", "shuffle", "repeat_one", "repeat_all")
_MODE_ICON = {
    "sequential": "mode_seq",
    "shuffle": "mode_shuffle",
    "repeat_one": "mode_one",
    "repeat_all": "mode_all",
}


# ---------------------------------------------------------------- 矢量图标

def _draw_glyph(painter: QPainter, key: str, rect: QRectF, color: QColor) -> None:
    """在 ``rect`` 里画一个矢量图标（不依赖任何字体）。"""
    painter.save()
    painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    def tri(points: list[tuple[float, float]]) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(x + px * w, y + py * h) for px, py in points]))
        painter.setBrush(Qt.NoBrush)

    if key == "play":
        tri([(0.18, 0.04), (0.18, 0.96), (0.94, 0.5)])
    elif key == "pause":
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(x + 0.16 * w, y + 0.06 * h, 0.24 * w, 0.88 * h), 1.4, 1.4)
        painter.drawRoundedRect(QRectF(x + 0.60 * w, y + 0.06 * h, 0.24 * w, 0.88 * h), 1.4, 1.4)
        painter.setBrush(Qt.NoBrush)
    elif key == "stop":
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(x + 0.16 * w, y + 0.16 * h, 0.68 * w, 0.68 * h), 2.2, 2.2)
        painter.setBrush(Qt.NoBrush)
    elif key == "prev":
        tri([(0.62, 0.06), (0.62, 0.94), (0.12, 0.5)])
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x + 0.80 * w, y + 0.08 * h), QPointF(x + 0.80 * w, y + 0.92 * h))
    elif key == "next":
        tri([(0.38, 0.06), (0.38, 0.94), (0.88, 0.5)])
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x + 0.20 * w, y + 0.08 * h), QPointF(x + 0.20 * w, y + 0.92 * h))
    elif key == "view":
        # 展开到主界面：两条对角箭头
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x + 0.18 * w, y + 0.82 * h), QPointF(x + 0.82 * w, y + 0.18 * h))
        tri([(0.58, 0.10), (0.92, 0.10), (0.92, 0.44)])
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x + 0.82 * w, y + 0.18 * h), QPointF(x + 0.18 * w, y + 0.82 * h))
        tri([(0.42, 0.90), (0.08, 0.90), (0.08, 0.56)])
    elif key == "mode_seq":
        painter.drawLine(QPointF(x + 0.08 * w, y + 0.5 * h), QPointF(x + 0.86 * w, y + 0.5 * h))
        tri([(0.62, 0.22), (0.94, 0.5), (0.62, 0.78)])
    elif key == "mode_shuffle":
        painter.drawLine(QPointF(x + 0.08 * w, y + 0.18 * h), QPointF(x + 0.86 * w, y + 0.82 * h))
        painter.drawLine(QPointF(x + 0.08 * w, y + 0.82 * h), QPointF(x + 0.86 * w, y + 0.18 * h))
        tri([(0.62, 0.10), (0.94, 0.10), (0.94, 0.42)])
        tri([(0.94, 0.58), (0.94, 0.90), (0.62, 0.90)])
    elif key in ("mode_one", "mode_all"):
        painter.drawArc(QRectF(x + 0.12 * w, y + 0.12 * h, 0.76 * w, 0.76 * h), 40 * 16, 285 * 16)
        tri([(0.62, 0.02), (0.98, 0.12), (0.72, 0.42)])
        if key == "mode_one":
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x + 0.5 * w, y + 0.5 * h), w * 0.09, h * 0.09)
            painter.setBrush(Qt.NoBrush)
    elif key in ("volume", "mute"):
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(x + 0.06 * w, y + 0.34 * h),
                    QPointF(x + 0.28 * w, y + 0.34 * h),
                    QPointF(x + 0.52 * w, y + 0.12 * h),
                    QPointF(x + 0.52 * w, y + 0.88 * h),
                    QPointF(x + 0.28 * w, y + 0.66 * h),
                    QPointF(x + 0.06 * w, y + 0.66 * h),
                ]
            )
        )
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap))
        if key == "volume":
            painter.drawArc(QRectF(x + 0.40 * w, y + 0.24 * h, 0.34 * w, 0.52 * h), -60 * 16, 120 * 16)
            painter.drawArc(QRectF(x + 0.34 * w, y + 0.12 * h, 0.56 * w, 0.76 * h), -60 * 16, 120 * 16)
        else:
            painter.drawLine(QPointF(x + 0.62 * w, y + 0.34 * h), QPointF(x + 0.90 * w, y + 0.66 * h))
            painter.drawLine(QPointF(x + 0.90 * w, y + 0.34 * h), QPointF(x + 0.62 * w, y + 0.66 * h))
    elif key == "min":
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x + 0.12 * w, y + 0.5 * h), QPointF(x + 0.88 * w, y + 0.5 * h))
    elif key == "max":
        painter.drawRoundedRect(QRectF(x + 0.14 * w, y + 0.14 * h, 0.72 * w, 0.72 * h), 1.0, 1.0)
    elif key == "close":
        painter.drawLine(QPointF(x + 0.16 * w, y + 0.16 * h), QPointF(x + 0.84 * w, y + 0.84 * h))
        painter.drawLine(QPointF(x + 0.84 * w, y + 0.16 * h), QPointF(x + 0.16 * w, y + 0.84 * h))
    painter.restore()


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
        self.btn_view.clicked.connect(self.viewModeToggled.emit)
        self.btn_prev.clicked.connect(self.prevRequested.emit)
        self.btn_play.clicked.connect(self._on_play_clicked)
        self.btn_stop.clicked.connect(self.stopRequested.emit)
        self.btn_next.clicked.connect(self.nextRequested.emit)
        self.btn_mode.clicked.connect(self.cycle_mode)
        for button in (self.btn_view, self.btn_prev, self.btn_play,
                       self.btn_stop, self.btn_next, self.btn_mode):
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
        playing = self._state == "PLAYING"
        self.btn_play.icon_key = "pause" if playing else "play"
        self.btn_play.setToolTip(i18n.tr("ui_mini_pause" if playing else "ui_mini_play"))
        self.btn_play.update()
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
        else:
            self.title_label.setText(i18n.tr_text("未在播放"))
            self.title_label.setToolTip("")
            self._total_ms = 0
            self._position_ms = 0
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

    def hideEvent(self, event) -> None:  # noqa: N802
        self._glow.stop()
        self._shadow.setBlurRadius(18.0)
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
]
