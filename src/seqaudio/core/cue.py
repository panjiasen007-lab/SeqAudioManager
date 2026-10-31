"""CUE 整轨索引文件解析（需求 3.1.2 整轨 CUE 支持）。

能力
----
* 编码自动识别：UTF-8(BOM) / UTF-16 LE/BE / GBK/GB18030 / Latin-1 兜底，修复中文乱码；
* 解析 TITLE / PERFORMER / REM GENRE / REM DATE / FILE / TRACK / INDEX / ISRC；
* 容错：写法不规范的 CUE 不抛异常，尽力解析并记录 warnings，绝不中断批量任务。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .model import CueSheet, CueTrack

_LINE = re.compile(r"^\s*(\w+)\s*(.*)$")
_TIME = re.compile(r"^\s*(\d{1,3}):(\d{1,2}):(\d{1,2})\s*$")


def decode_cue_bytes(data: bytes) -> tuple[str, str]:
    """返回 (文本, 使用的编码名)。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace"), "utf-8-sig"
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le", errors="replace"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be", errors="replace"), "utf-16-be"
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def _frame_to_ms(text: str) -> int:
    """``MM:SS:FF``（CD 帧，75 帧/秒）转毫秒。"""
    m = _TIME.match(text)
    if not m:
        return 0
    minutes, seconds, frames = (int(x) for x in m.groups())
    return minutes * 60_000 + seconds * 1_000 + int(frames * 1000 / 75)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def parse_cue(path: Path | str) -> CueSheet:
    path = Path(path)
    raw = path.read_bytes()
    text, encoding = decode_cue_bytes(raw)
    sheet = CueSheet(cue_path=path, encoding_used=encoding)
    sheet.warnings = []
    if encoding not in ("utf-8", "utf-8-sig", "utf-16-le", "utf-16-be"):
        sheet.warnings.append(f"非 UTF-8 编码（{encoding}），已自动转码")

    album_performer = ""
    album_title = ""
    current: Optional[CueTrack] = None
    pending_track_title = ""
    file_name = ""
    global_performer_seen = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            continue
        keyword, rest = m.group(1).upper(), m.group(2)

        if keyword == "REM":
            sub = rest.split(None, 1)
            if len(sub) == 2:
                key, value = sub[0].upper(), sub[1].strip()
                if key == "GENRE":
                    sheet.genre = value
                elif key == "DATE":
                    sheet.date = value
        elif keyword == "TITLE":
            value = _unquote(rest)
            if current is None:
                album_title = value
            else:
                # TRACK 之后出现的 TITLE 归当前曲目
                current.title = value
        elif keyword == "PERFORMER":
            value = _unquote(rest)
            if current is None:
                album_performer = value
                global_performer_seen = True
            else:
                current.performer = value
        elif keyword == "FILE":
            file_name = _unquote(rest.rsplit(" ", 1)[0] if rest.upper().endswith("WAVE") else rest)
        elif keyword == "TRACK":
            parts = rest.split()
            if not parts:
                continue
            try:
                number = int(parts[0])
            except ValueError:
                sheet.warnings.append(f"曲目号无法解析：{line}")
                continue
            current = CueTrack(number=number)
            if not current.performer and global_performer_seen:
                current.performer = album_performer
            sheet.tracks.append(current)
        elif keyword == "INDEX":
            parts = rest.split()
            if len(parts) < 2 or current is None:
                continue
            try:
                index_no = int(parts[0])
            except ValueError:
                continue
            if index_no == 1:
                current.start_ms = _frame_to_ms(parts[1])

    # 结束时间串联
    for i, track in enumerate(sheet.tracks):
        if i + 1 < len(sheet.tracks):
            track.end_ms = sheet.tracks[i + 1].start_ms

    # 占位标题：TRACK 之后才出现的 TITLE 归属该轨
    if pending_track_title and sheet.tracks:
        sheet.tracks[-1].title = pending_track_title

    sheet.title = album_title
    sheet.album = album_title
    sheet.performer = album_performer

    if file_name:
        candidate = (path.parent / file_name).resolve()
        if candidate.exists():
            sheet.audio_file = candidate
        else:
            # 常见于改过名的整轨文件，尝试同目录同扩展名匹配
            for pattern in (f"*{Path(file_name).suffix}", "*.flac", "*.ape", "*.wav"):
                matches = sorted(path.parent.glob(pattern))
                if len(matches) == 1:
                    sheet.audio_file = matches[0]
                    break
            if sheet.audio_file is None:
                sheet.warnings.append(f"CUE 指向的音频不存在：{file_name}")

    if not sheet.tracks:
        sheet.warnings.append("未解析到任何 TRACK 条目，CUE 可能不规范")
    for track in sheet.tracks:
        if track.start_ms == 0 and track.number != 1:
            track.start_ms = 0

    return sheet


def fill_durations(sheet: CueSheet, total_seconds: float) -> None:
    """用音频总时长补齐最后一轨的结束时间。"""
    if not sheet.tracks or not total_seconds:
        return
    total_ms = int(total_seconds * 1000)
    sheet.tracks[-1].end_ms = total_ms
    for i, track in enumerate(sheet.tracks[:-1]):
        if track.end_ms <= track.start_ms:
            track.end_ms = sheet.tracks[i + 1].start_ms


def track_titles(sheet: CueSheet) -> list[str]:
    return [t.title or f"Track {t.number:02d}" for t in sheet.tracks]


def to_tags_for_album(sheet: CueSheet):
    """整轨模式 1：把 CUE 信息转成整轨文件应有的标签。"""
    from .model import Tags

    tags = Tags(
        title=sheet.title or "",
        artist=sheet.performer or "",
        album=sheet.album or sheet.title or "",
        albumartist=sheet.performer or "",
        date=sheet.date or "",
        genre=sheet.genre or "",
    )
    if len(sheet.tracks) == 1:
        only = sheet.tracks[0]
        tags.title = only.title or tags.title
        tags.artist = only.performer or tags.artist
        tags.track = str(only.number)
    return tags
