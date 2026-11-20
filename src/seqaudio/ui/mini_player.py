"""迷你播放条：无边框可拖动的小窗，上方精简播放条 + 下方三行歌词。

设计约束
--------
* **绝不新建引擎**：本窗口不 import ``seqaudio.player``，只复用主窗口传进来的
  ``PlayerBridge`` 信号（由 ``main_window`` 接线），与 main 状态天然同步。
* 三行歌词直接读 ``PlayerLyricsPanel._lines / _current`` —— 这是同一个面板对象，
  不是拷贝，因此 LRC、偏移、联网结果都自动一致。
* 主题色全部来自 ``theme``；切换主题时 ``refresh_theme()`` 重刷样式。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QComboBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .player_bar import PlayerBar

MINI_WIDTH = 360
MINI_HEIGHT = 150
LYRIC_LINES = 3


class MiniPlayerWindow(QWidget):
    """迷你形态：32px 播放条 + 3 行歌词，可拖动，按钮回主界面。"""

    def __init__(self, settings, lyrics_panel, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self.lyrics_panel = lyrics_panel
        self._drag_origin = QPoint()
        self._dragging = False

        self.setObjectName("miniPlayer")
        self.setWindowTitle(i18n.tr("ui_mini_title"))
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(MINI_WIDTH, MINI_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # 精简播放条：复用同一套控件，折叠后只留切换/播放/停止/曲名
        self.bar = PlayerBar()
        self.bar.set_collapsed(True)
        self.bar.btn_collapse.hide()
        self.bar.set_view_mode_hint(i18n.tr("ui_view_main"))
        root.addWidget(self.bar)

        lyrics = QVBoxLayout()
        lyrics.setContentsMargins(8, 2, 8, 4)
        lyrics.setSpacing(2)
        self.lyric_labels: list[QLabel] = []
        for _ in range(LYRIC_LINES):
            label = QLabel("")
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            label.setMinimumHeight(20)
            self.lyric_labels.append(label)
            lyrics.addWidget(label)
        root.addLayout(lyrics, 1)

        self.refresh_theme()
        self.update_lyrics()

    # ------------------------------------------------------------ 歌词

    def update_lyrics(self) -> None:
        """从歌词面板取「当前行 + 前后各一行」，当前行主色加粗。"""
        lines = list(getattr(self.lyrics_panel, "_lines", []) or [])
        current = int(getattr(self.lyrics_panel, "_current", -1) or -1)
        if not lines:
            self.lyric_labels[0].setText(i18n.tr("lyr_mini_empty"))
            self.lyric_labels[0].setStyleSheet(f"color:{theme.FG_MUTE};")
            font = QFont(self.lyric_labels[0].font())
            font.setBold(False)
            self.lyric_labels[0].setFont(font)
            for label in self.lyric_labels[1:]:
                label.setText("")
            return

        for index, label in enumerate(self.lyric_labels):
            row = current - 1 + index
            if 0 <= row < len(lines):
                text = str(lines[row][1] or "♪")
            else:
                text = ""
            label.setText(text)
            font = QFont(label.font())
            if row == current and row >= 0:
                font.setBold(True)
                label.setStyleSheet(f"color:{theme.ACCENT};")
            else:
                font.setBold(False)
                label.setStyleSheet(f"color:{theme.FG_DIM};")
            label.setFont(font)

    # ------------------------------------------------------------ 主题 / 语言

    def refresh_theme(self) -> None:
        """主题切换后重刷背景、边框、播放条与歌词配色。"""
        self.setStyleSheet(
            f"QWidget#miniPlayer {{ background:{theme.SURFACE_CARD};"
            f" border:1px solid {theme.LINE}; border-radius:10px; }}"
        )
        self.bar.apply_theme()
        self.update_lyrics()

    def refresh_texts(self) -> None:
        """语言切换后更新按钮提示与空歌词提示。"""
        self.bar.set_view_mode_hint(i18n.tr("ui_view_main"))
        self.setWindowTitle(i18n.tr("ui_mini_title"))
        self.update_lyrics()

    # ------------------------------------------------------------ 拖动

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            # 子控件（按钮/滑杆）自身处理点击，不让整窗跟着拖
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


__all__ = ["MiniPlayerWindow", "MINI_WIDTH", "MINI_HEIGHT"]
