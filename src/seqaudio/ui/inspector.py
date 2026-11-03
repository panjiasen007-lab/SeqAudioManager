"""右侧详情检查器：封面、标签、规格与鉴定结论一屏看完。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.authenticity import VERDICT_COLORS
from ..core.model import Track
from ..core.quarantine import human_size
from . import theme


class InspectorPanel(QWidget):
    editRequested = Signal()
    spectrumRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._track: Optional[Track] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        head = QLabel("曲目详情")
        head.setObjectName("sectionTitle")
        root.addWidget(head)

        self.empty = QLabel("选中一首曲目，查看封面、标签与鉴定详情")
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(120)
        root.addWidget(self.empty)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVisible(False)
        root.addWidget(self.scroll, 1)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        self.cover = QLabel("无封面")
        self.cover.setFixedSize(168, 168)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet(
            f"border:1px dashed {theme.LINE}; border-radius:10px;"
            f"color:{theme.FG_MUTE}; background:{theme.BG_CARD};"
        )
        cover_row = QHBoxLayout()
        cover_row.addStretch(1)
        cover_row.addWidget(self.cover)
        cover_row.addStretch(1)
        body_layout.addLayout(cover_row)

        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color:{theme.FG}; font-size:15px; font-weight:700;")
        self.title.setAlignment(Qt.AlignCenter)
        body_layout.addWidget(self.title)

        self.artist = QLabel("")
        self.artist.setWordWrap(True)
        self.artist.setStyleSheet(f"color:{theme.FG_DIM};")
        self.artist.setAlignment(Qt.AlignCenter)
        body_layout.addWidget(self.artist)

        self.album = QLabel("")
        self.album.setWordWrap(True)
        self.album.setStyleSheet(f"color:{theme.FG_MUTE}; font-size:11px;")
        self.album.setAlignment(Qt.AlignCenter)
        body_layout.addWidget(self.album)

        self._line = QFrame()
        self._line.setFixedHeight(1)
        body_layout.addWidget(self._line)

        self.info = QFormLayout()
        self.info.setContentsMargins(0, 0, 0, 0)
        self.info.setHorizontalSpacing(10)
        self.info.setVerticalSpacing(6)
        self.info.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._info_labels: dict[str, QLabel] = {}
        for key, label in (
            ("format", "格式"),
            ("spec", "规格"),
            ("duration", "时长"),
            ("bitrate", "码率"),
            ("size", "大小"),
            ("path", "路径"),
        ):
            value = QLabel("—")
            value.setWordWrap(key == "path")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setStyleSheet(f"color:{theme.FG_DIM};")
            self._info_labels[key] = value
            self.info.addRow(label, value)
        body_layout.addLayout(self.info)

        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.verdict.setStyleSheet(
            f"padding:8px 10px; border:1px solid {theme.LINE_SOFT};"
            f"border-radius:8px; background:{theme.BG_CARD};"
        )
        body_layout.addWidget(self.verdict)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.edit_btn = QPushButton("编辑标签")
        self.edit_btn.setObjectName("primary")
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self.editRequested)
        self.spectrum_btn = QPushButton("查看频谱")
        self.spectrum_btn.setObjectName("ghost")
        self.spectrum_btn.setCursor(Qt.PointingHandCursor)
        self.spectrum_btn.clicked.connect(self.spectrumRequested)
        button_row.addWidget(self.edit_btn, 1)
        button_row.addWidget(self.spectrum_btn, 1)
        body_layout.addLayout(button_row)
        body_layout.addStretch(1)

        self.scroll.setWidget(body)

    def _apply_theme_styles(self) -> None:
        self.title.setStyleSheet(f"color:{theme.FG}; font-size:15px; font-weight:700;")
        self.artist.setStyleSheet(f"color:{theme.FG_DIM};")
        self.album.setStyleSheet(f"color:{theme.FG_MUTE}; font-size:11px;")
        self._line.setStyleSheet(f"background:{theme.LINE_SOFT};")
        for label in self._info_labels.values():
            label.setStyleSheet(f"color:{theme.FG_DIM};")

    def set_track(self, track: Optional[Track]) -> None:
        self._track = track
        self.empty.setVisible(track is None)
        self.scroll.setVisible(track is not None)
        self._apply_theme_styles()
        if track is None:
            return

        self.title.setText(track.working.title or track.original.title or track.path.stem)
        self.artist.setText(track.working.artist or track.original.artist or "—")
        album = track.working.album or track.original.album
        self.album.setText(album or "")

        info = track.info
        self._info_labels["format"].setText(info.fmt or "—")
        self._info_labels["spec"].setText(info.spec_text)
        self._info_labels["duration"].setText(track.duration_text)
        self._info_labels["bitrate"].setText(info.bitrate_text)
        self._info_labels["size"].setText(human_size(info.file_size) if info.file_size else "—")
        self._info_labels["path"].setText(str(track.path))

        self._render_cover(track)

        auth = track.authenticity
        if auth is None:
            self.verdict.setText("尚未进行真假无损鉴定")
            self.verdict.setStyleSheet(
                f"padding:8px 10px; border:1px solid {theme.LINE_SOFT};"
                f"border-radius:8px; color:{theme.FG_DIM}; background:{theme.BG_CARD};"
            )
            return
        color = VERDICT_COLORS.get(auth.verdict, theme.FG_DIM)
        text = f"<b style='color:{color}'>{auth.verdict}</b>"
        detail = auth.detail_text
        if detail:
            text += f"<br><span style='color:{theme.FG_DIM}'>{detail}</span>"
        self.verdict.setText(text)
        self.verdict.setStyleSheet(
            f"padding:8px 10px; border:1px solid {theme.LINE_SOFT};"
            f"border-radius:8px; background:{theme.BG_CARD};"
        )

    def refresh(self) -> None:
        self.set_track(self._track)

    def _render_cover(self, track: Track) -> None:
        data = track.working.cover
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                self.cover.setText("")
                self.cover.setStyleSheet(
                    f"border:1px solid {theme.LINE}; border-radius:10px;"
                )
                self.cover.setPixmap(
                    pix.scaled(166, 166, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                return
        self.cover.setPixmap(QPixmap())
        self.cover.setText("无封面")
        self.cover.setStyleSheet(
            f"border:1px dashed {theme.LINE}; border-radius:10px;"
            f"color:{theme.FG_MUTE}; background:{theme.BG_CARD};"
        )
