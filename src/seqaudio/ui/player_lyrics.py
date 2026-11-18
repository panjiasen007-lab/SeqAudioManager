"""歌词页签：LRC 解析、逐行高亮、偏移微调、联网兜底。

已知限制（范围文档明确不做）
----------------------------
逐字增强型 LRC（``[00:12.00]<00:12.00>字<00:12.35>幕``）**降级为逐行**：把
``<>`` 里的逐字时间戳剥掉，只保留行首时间。这样整首歌词还能跟着唱，不会
因为「不支持逐字」而整篇不显示（TC-GUI-14 的验收点）。

为什么自己解析 LRC 而不复用 ``core.lyrics``
-------------------------------------------
``core.lyrics`` 面向「清洗与写回」（去广告、统一换行、生成 LRC 头），它的
``TIMESTAMP`` 只做匹配不做求值；播放器需要的是「毫秒时间轴 + 逐行文本」，
还要处理一行多个时间戳（副歌复用）。两者目标不同，硬改 core 会波及写盘
链路，所以这里只复用它的 ``META``/清洗能力，解析独立实现。
"""

from __future__ import annotations

import bisect
import logging
import re
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ..core import lyrics as lyrics_mod
from . import i18n, theme
from .widgets import SectionHeader

log = logging.getLogger(__name__)

#: 行首时间戳：``[mm:ss]``、``[mm:ss.xx]``、``[mm:ss:xx]``，允许一行多个
LINE_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
#: 逐字时间戳（``<00:12.35>``），解析时剥掉
WORD_TIME = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
#: 元信息行 ``[ti:..] [ar:..] [offset:..]``
META_LINE = re.compile(r"^\[(ti|ar|al|by|offset|re|ve|length|kana):(.*)\]$", re.IGNORECASE)

#: 读取 .lrc 时依次尝试的编码（GBK 用户最多，BIG5 兜底港台老歌）
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16")

#: 偏移微调的步长（毫秒）
OFFSET_STEPS: tuple[int, ...] = (100, 500)


def parse_lrc(text: str) -> tuple[list[tuple[int, str]], dict[str, str]]:
    """解析 LRC 文本。

    Args:
        text: 歌词原文（可含元信息行、逐字时间戳、一行多时间戳）。

    Returns:
        ``(lines, meta)``：``lines`` 是 ``(毫秒, 文本)`` 且按时间升序；
        ``meta`` 是元信息字典（键小写）。空输入返回 ``([], {})``。
    """
    if not text:
        return [], {}
    meta: dict[str, str] = {}
    collected: list[tuple[int, str]] = []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        meta_match = META_LINE.match(line)
        if meta_match and not LINE_TIME.search(line):
            meta[meta_match.group(1).lower()] = meta_match.group(2).strip()
            continue
        stamps = list(LINE_TIME.finditer(line))
        if not stamps:
            # 无时间轴的纯文本行：不参与高亮，直接跳过（由调用方决定是否按纯文本显示）
            continue
        payload = WORD_TIME.sub("", line[stamps[-1].end():]).strip()
        for stamp in stamps:
            collected.append((_stamp_to_ms(stamp), payload))
    collected.sort(key=lambda pair: pair[0])
    return collected, meta


def _stamp_to_ms(match: "re.Match[str]") -> int:
    """把 ``[mm:ss.xx]`` 的匹配结果折成毫秒。"""
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2) or 0)
    fraction = match.group(3) or ""
    if fraction:
        # 「.xx」两位是百分秒，「.xxx」三位是毫秒
        if len(fraction) == 1:
            millis = int(fraction) * 100
        elif len(fraction) == 2:
            millis = int(fraction) * 10
        else:
            millis = int(fraction)
    else:
        millis = 0
    return max(0, minutes * 60_000 + seconds * 1000 + millis)


def read_lyrics_file(path: Path) -> str:
    """按 UTF-8 → GB18030 → BIG5 顺序读歌词文件，全失败时 ``errors='replace'`` 兜底。"""
    target = Path(path)
    for encoding in ENCODINGS:
        try:
            return target.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError as exc:
            log.debug("读取歌词失败：%s（%s）", target, exc)
            return ""
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - 文件在读取瞬间被删
        return ""


class _LyricsFetchWorker(QThread):
    """联网匹配歌词的后台线程（不阻塞界面）。"""

    fetched = Signal(str, str)   # (歌词文本, 来源)
    failed = Signal(str)

    def __init__(self, settings, title: str, artist: str, album: str, duration: float, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.title = title
        self.artist = artist
        self.album = album
        self.duration = duration

    def run(self) -> None:
        try:
            from ..net.aggregator import fetch_lyrics
            from ..net.http import DiskCache, HttpClient

            settings = self.settings
            client = HttpClient(
                proxy=settings.http_proxy,
                timeout=settings.request_timeout,
                concurrency=settings.concurrency,
                cache=DiskCache(ttl_days=settings.cache_ttl_days),
                offline=settings.offline_mode,
            )
            result = fetch_lyrics(
                client,
                self.title,
                self.artist,
                self.album,
                self.duration,
                use_lrclib=bool(settings.use_lrclib),
                use_netmusic=bool(settings.use_netmusic),
                use_qqmusic=bool(settings.use_qqmusic),
            )
            if result is None or not result.text:
                self.failed.emit("未找到歌词")
                return
            self.fetched.emit(result.text, str(result.source or ""))
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一转成提示
            log.exception("联网匹配歌词失败")
            self.failed.emit(str(exc))


class PlayerLyricsPanel(QWidget):
    """「歌词」页签。

    Signals:
        offsetChanged(int): 偏移调整后的毫秒值（主窗口可据此刷新提示）。
    """

    offsetChanged = Signal(int)

    def __init__(self, settings=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self._lines: list[tuple[int, str]] = []
        self._times: list[int] = []
        self._meta: dict[str, str] = {}
        self._offset_ms = int(getattr(settings, "playback_lyrics_offset_ms", 0) or 0)
        self._current = -1
        self._track: Optional[dict[str, Any]] = None
        self._worker: Optional[_LyricsFetchWorker] = None
        self._raw_plain = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(
            SectionHeader(i18n.tr_text("歌词"), i18n.tr_text("右键可微调偏移或联网匹配"))
        )

        self.view = QListWidget()
        self.view.setObjectName("playerLyrics")
        self.view.setSelectionMode(QAbstractItemView.NoSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.setWordWrap(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self.view, 1)

        self.hint_label = QLabel(i18n.tr_text("未找到歌词"))
        self.hint_label.setObjectName("hint")
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        self.offset_label = QLabel("")
        self.offset_label.setObjectName("hint")
        root.addWidget(self.offset_label)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._refresh_offset_label()
        self.clear()

    # ------------------------------------------------------------ 接口

    def set_track(self, track: Any) -> None:
        """换曲目：找同名 ``.lrc`` → 内嵌标签歌词 → 空态。"""
        data = self._as_dict(track)
        self._track = data
        self.clear()
        if not data:
            return
        path_text = str(data.get("path") or "")
        text = ""
        if path_text:
            sidecar = Path(path_text).with_suffix(".lrc")
            if sidecar.exists():
                text = read_lyrics_file(sidecar)
                if text.strip():
                    log.debug("歌词来自同名 .lrc：%s", sidecar)
        if not text.strip():
            text = str(data.get("lyrics") or "")
        self.set_lyrics(text)

    def set_lyrics(self, text: str) -> None:
        """直接喂一段歌词文本（本地/联网结果都走这里）。"""
        self._raw_plain = ""
        self._lines, self._meta = parse_lrc(text or "")
        if self._lines:
            own_offset = 0
            try:
                own_offset = int(self._meta.get("offset", "0") or 0)
            except ValueError:
                own_offset = 0
            if own_offset:
                # LRC 的 offset 是「歌词提前/延后」的毫秒，与界面偏移叠加
                self._lines = [(max(0, ms - own_offset), payload) for ms, payload in self._lines]
                self._lines.sort(key=lambda pair: pair[0])
        self._times = [ms for ms, _ in self._lines]
        self._render()
        self._current = -1

    def set_position(self, ms: int) -> None:
        """按播放位置切高亮行（未变化时不做任何事，避免无谓重绘）。"""
        if not self._lines:
            return
        position = max(0, int(ms or 0)) - self._offset_ms
        index = bisect.bisect_right(self._times, position) - 1
        if index < 0:
            index = -1 if position < self._times[0] else 0
        if index == self._current:
            return
        self._current = index
        self._apply_highlight()

    def clear(self) -> None:
        """清空显示（换曲目或停止时调用）。"""
        self._lines = []
        self._times = []
        self._meta = {}
        self._raw_plain = ""
        self._current = -1
        self.view.clear()
        self.hint_label.setText(i18n.tr_text("未找到歌词"))
        self.hint_label.setVisible(True)

    def current_line_index(self) -> int:
        return self._current

    def line_count(self) -> int:
        return len(self._lines)

    def offset_ms(self) -> int:
        return self._offset_ms

    def set_offset_ms(self, value: int, *, persist: bool = True) -> None:
        """设置偏移（毫秒）：高亮位置随之平移，重启后保留。"""
        self._offset_ms = int(value)
        if persist and self.settings is not None:
            try:
                self.settings.playback_lyrics_offset_ms = self._offset_ms
                self.settings.save()
            except OSError:
                log.debug("歌词偏移未能落盘")
        self._refresh_offset_label()
        self._current = -1
        self.offsetChanged.emit(self._offset_ms)

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _as_dict(track: Any) -> Optional[dict[str, Any]]:
        """把 ``Track`` / ``QueueItem`` / 字典统一成字典视图。"""
        if track is None:
            return None
        if isinstance(track, dict):
            return dict(track)
        path = getattr(track, "path", None)
        if path is None:
            return None
        working = getattr(track, "working", None)
        original = getattr(track, "original", None)
        lyrics = ""
        for tags in (working, original):
            if tags is not None and getattr(tags, "lyrics", ""):
                lyrics = str(getattr(tags, "lyrics") or "")
                break
        info = getattr(track, "info", None)
        return {
            "path": str(path),
            "title": getattr(track, "title", "") or Path(str(path)).stem,
            "artist": getattr(track, "artist", "") or "",
            "album": getattr(track, "album", "") or "",
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
            "lyrics": lyrics,
        }

    def _render(self) -> None:
        self.view.clear()
        if not self._lines:
            self.hint_label.setText(i18n.tr_text("未找到歌词"))
            self.hint_label.setVisible(True)
            return
        self.hint_label.setVisible(False)
        for _, payload in self._lines:
            item = QListWidgetItem(payload or "♪")
            item.setTextAlignment(Qt.AlignHCenter)
            self.view.addItem(item)
        self._apply_highlight()

    def _apply_highlight(self) -> None:
        """当前行加粗 + 主色，前后两行半透明，其余更淡。"""
        colors = {
            -2: QColor(theme.FG_MUTE),
            -1: QColor(theme.FG_DIM),
            0: QColor(theme.ACCENT),
            1: QColor(theme.FG_DIM),
            2: QColor(theme.FG_MUTE),
        }
        fallback = QColor(theme.FG_MUTE)
        fallback.setAlpha(150)
        for row in range(self.view.count()):
            item = self.view.item(row)
            if item is None:
                continue
            distance = row - self._current
            font = QFont(self.font())
            if distance == 0:
                font.setBold(True)
                font.setPixelSize(max(13, font.pixelSize() + 1) if font.pixelSize() > 0 else 14)
            item.setFont(font)
            item.setForeground(colors.get(distance, fallback))
        if 0 <= self._current < self.view.count():
            self.view.scrollToItem(
                self.view.item(self._current), QAbstractItemView.PositionAtCenter
            )

    def _refresh_offset_label(self) -> None:
        if self._offset_ms == 0:
            self.offset_label.setText(i18n.tr_format("歌词偏移：{value}", value="0.0 s"))
        else:
            self.offset_label.setText(
                i18n.tr_format("歌词偏移：{value}", value=f"{self._offset_ms / 1000.0:+.1f} s")
            )

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction(
            i18n.tr_text("联网匹配歌词…"),
            self.fetch_online,
        )
        menu.addSeparator()
        for step in OFFSET_STEPS:
            menu.addAction(
                i18n.tr_format("偏移 +{value} s", value=f"{step / 1000.0:.1f}"),
                lambda s=step: self.set_offset_ms(self._offset_ms + s),
            )
        for step in OFFSET_STEPS:
            menu.addAction(
                i18n.tr_format("偏移 -{value} s", value=f"{step / 1000.0:.1f}"),
                lambda s=step: self.set_offset_ms(self._offset_ms - s),
            )
        menu.addAction(
            i18n.tr_text("重置偏移"), lambda: self.set_offset_ms(0)
        )
        menu.exec(self.view.mapToGlobal(pos))

    # ------------------------------------------------------------ 联网

    def fetch_online(self) -> None:
        """右键「联网匹配歌词」：异步抓取，失败只提示不弹窗。"""
        if not self._track:
            self.hint_label.setText(i18n.tr_text("未找到歌词"))
            return
        if self.settings is None:
            return
        if bool(getattr(self.settings, "offline_mode", False)):
            self.hint_label.setVisible(True)
            self.hint_label.setText(i18n.tr_text("当前离线模式，无法联网匹配歌词"))
            return
        if self._worker is not None and self._worker.isRunning():
            return
        title = str(self._track.get("title") or "").strip()
        if not title:
            return
        self.hint_label.setVisible(True)
        self.hint_label.setText(i18n.tr_text("正在联网匹配歌词…"))
        worker = _LyricsFetchWorker(
            self.settings,
            title,
            str(self._track.get("artist") or ""),
            str(self._track.get("album") or ""),
            float(self._track.get("duration") or 0.0),
            parent=self,
        )
        worker.fetched.connect(self._on_fetched)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker = worker
        worker.start()

    def _on_fetched(self, text: str, source: str) -> None:
        self.set_lyrics(text)
        if not self._lines:
            # 拿到的是纯文本（无时间轴）：按行铺开，保证「可读」而不是空白
            self._render_plain(text)
        # 有行（带时间轴或纯文本）就藏掉提示，避免「未找到歌词」盖在歌词上
        self.hint_label.setVisible(self.view.count() == 0)
        if source:
            log.info("歌词已联网匹配：%s", source)

    def _on_fetch_failed(self, message: str) -> None:
        self.hint_label.setVisible(True)
        self.hint_label.setText(i18n.tr_format("未找到歌词（{message}）", message=message))

    def _render_plain(self, text: str) -> None:
        """无时间轴的纯文本歌词：逐行显示，不做高亮。"""
        self._raw_plain = lyrics_mod.clean_lyrics(text or "") or str(text or "")
        lines = [line for line in self._raw_plain.split("\n") if line.strip()]
        if not lines:
            return
        self.view.clear()
        for line in lines:
            item = QListWidgetItem(line)
            item.setTextAlignment(Qt.AlignHCenter)
            self.view.addItem(item)
        self.hint_label.setVisible(False)


__all__ = ["PlayerLyricsPanel", "parse_lrc", "read_lyrics_file", "OFFSET_STEPS"]
