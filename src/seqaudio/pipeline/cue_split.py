"""CUE 整轨分轨导出：把 WAV/APE/FLAC + CUE 按索引切成独立文件（需求 3.1.2 模式 2）。

歌词/标签匹配策略
------------------
整轨文件本身没有分轨标签，CUE 才是每首歌元数据的唯一来源。切分时：

1. **标签** 直接取自 CUE —— 歌名/歌手/专辑/轨号/年份/流派都是抓轨者手写，
   比任何联网匹配都准；
2. **歌词** 用「歌名 + 歌手 + 专辑 + 分轨时长」联网匹配 —— 其中**分轨时长
   是 CUE 提供的精确值**，LRCLIB 会用它做时长校验，能把同名歌的错配歌词
   挡在门外，这是比「只用歌名」准得多的关键；
3. **封面** 复用整轨内嵌封面，没有则按专辑联网补一张。

切分用 ffmpeg 的 ``-c copy``（无损、秒级），标签用 ``write_tags`` 写进新文件，
歌词内嵌到标签的 lyrics 字段（可另行导出 .lrc）。
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.model import Tags, Track
from ..core.tags import write_tags
from ..net import aggregator
from ..net.http import HttpClient

log = logging.getLogger(__name__)


@dataclass
class SplitSummary:
    total: int = 0        # 待切分的轨数
    created: int = 0      # 成功写出的文件数
    with_lyrics: int = 0  # 匹配到歌词的轨数
    failed: int = 0       # 切分/写入失败数
    elapsed_sec: float = 0.0

    def text(self) -> str:
        return (
            f"分轨导出：成功 {self.created}/{self.total}，"
            f"带歌词 {self.with_lyrics}，失败 {self.failed}"
        )


def _find_ffmpeg() -> Optional[str]:
    try:
        from ..core.authenticity import find_ffmpeg

        return find_ffmpeg()
    except Exception:  # noqa: BLE001
        return None


def split_one(
    ffmpeg: str, src: Path, dst: Path, start_sec: float, duration_sec: float
) -> bool:
    """用 ffmpeg 切出 [start, start+duration) 一段，输出 FLAC。"""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{max(0.1, duration_sec):.3f}",
        "-i", str(src),
        "-map", "0:a:0",
        "-c:a", "flac",
        "-y", str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("切分失败 %s：%s", src.name, exc)
        return False
    return proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def _safe_filename(number: int, title: str) -> str:
    title = (title or "").strip() or f"Track {number:02d}"
    for ch in '\\/:*?"<>|':
        title = title.replace(ch, "_")
    return f"{number:02d} - {title}.flac"


def split_cue_sheet(
    track: Track,
    output_dir: Path,
    client: HttpClient,
    *,
    use_lrclib: bool = True,
    use_netmusic: bool = True,
    use_qqmusic: bool = True,
    fetch_lyrics: bool = True,
    on_progress=None,
    cancel=None,
) -> SplitSummary:
    """把整轨 ``track`` 按 ``track.cue`` 切分成独立 FLAC，写标签 + 匹配歌词。

    ``output_dir`` 是输出目录；每个分轨文件名 ``NN - 标题.flac``。
    """
    sheet = track.cue
    if sheet is None or len(sheet.tracks) <= 1:
        return SplitSummary(total=0, elapsed_sec=0.0)

    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        return SplitSummary(total=len(sheet.tracks), failed=len(sheet.tracks))

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(sheet.tracks)
    summary = SplitSummary(total=total)
    started = time.perf_counter()

    # 整轨内嵌封面（切分后复用到每个分轨）
    embedded_cover = track.working.cover or track.original.cover
    embedded_mime = track.working.cover_mime or track.original.cover_mime

    for index, entry in enumerate(sheet.tracks, start=1):
        if cancel is not None and cancel.is_set():
            break

        title = entry.title or f"Track {entry.number:02d}"
        artist = entry.performer or sheet.performer or ""
        album = sheet.album or sheet.title or ""

        dst = output_dir / _safe_filename(entry.number, title)
        if dst.exists():
            # 已存在，跳过切分（幂等），但补一次标签/歌词
            pass
        else:
            ok = split_one(
                ffmpeg, track.path, dst,
                entry.start_ms / 1000.0, entry.duration,
            )
            if not ok:
                summary.failed += 1
                if on_progress is not None:
                    on_progress(index, total)
                continue

        tags = Tags(
            title=title,
            artist=artist,
            album=album,
            albumartist=sheet.performer or "",
            date=sheet.date or "",
            genre=sheet.genre or "",
            track=f"{entry.number}/{total}",
        )
        if embedded_cover:
            tags.cover = embedded_cover
            tags.cover_mime = embedded_mime

        # 歌词：歌名 + 歌手 + 专辑 + 分轨时长（CUE 给的精确时长）
        if fetch_lyrics:
            lyrics = aggregator.fetch_lyrics(
                client, title, artist, album, entry.duration,
                use_lrclib=use_lrclib, use_netmusic=use_netmusic, use_qqmusic=use_qqmusic,
            )
            if lyrics is not None and lyrics.text:
                tags.lyrics = lyrics.text
                summary.with_lyrics += 1

        result = write_tags(dst, tags)
        if result.ok:
            summary.created += 1
        else:
            summary.failed += 1

        if on_progress is not None:
            on_progress(index, total)

    summary.elapsed_sec = time.perf_counter() - started
    return summary
