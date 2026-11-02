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
    """用 ffmpeg 切出 [start, start+duration) 一段，输出 FLAC。

    先写临时文件，校验时长无误后再原子替换到目标路径：
    * 中断/失败不会留下半截 FLAC；
    * 目标已存在时也能安全覆盖（修复历史坏分轨）。
    """
    from ..core.probe import probe

    duration_sec = max(0.1, duration_sec)
    tmp = dst.with_name(dst.stem + ".seqaudio.tmp" + dst.suffix)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration_sec:.3f}",
        "-i", str(src),
        "-map", "0:a:0",
        "-map_metadata", "-1",
        "-vn", "-sn", "-dn",
        "-c:a", "flac",
        "-y", str(tmp),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("切分失败 %s：%s", src.name, exc)
        tmp.unlink(missing_ok=True)
        return False
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    # 校验时长：CUE 的毫秒索引与编码器输出的时间基可能有细微差，
    # 允许 0.35 秒误差；差得多说明切分位置/时长算错了，不能落盘。
    try:
        actual = probe(tmp, with_cue=False).info.duration or 0.0
    except Exception:  # noqa: BLE001
        actual = 0.0
    if actual <= 0 or abs(actual - duration_sec) > 0.35:
        log.warning(
            "分轨时长异常 %s：期望 %.2fs，实际 %.2fs", dst.name, duration_sec, actual
        )
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dst)
    return True


def _safe_filename(number: int, title: str) -> str:
    title = (title or "").strip() or f"Track {number:02d}"
    for ch in '\\/:*?"<>|':
        title = title.replace(ch, "_")
    return f"{number:02d} - {title}.flac"


def _entry_bounds(
    sheet, index: int, total_ms: int, trim_pregap: bool
) -> tuple[int, int]:
    """返回第 ``index`` 轨的 ``(start_ms, duration_ms)``。

    默认沿用 EAC/CUE 习惯：轨尾到下一轨 INDEX 01（预间隙归入前一轨）。
    ``trim_pregap=True`` 时改到下一轨 INDEX 00，即去掉轨尾预间隙。
    """
    entry = sheet.tracks[index]
    end_ms = entry.end_ms
    if index + 1 < len(sheet.tracks):
        nxt = sheet.tracks[index + 1]
        if trim_pregap:
            boundary = nxt.pregap_ms if nxt.pregap_ms > entry.start_ms else nxt.start_ms
            if boundary > entry.start_ms:
                end_ms = boundary
    elif total_ms > 0:
        end_ms = total_ms
    return entry.start_ms, max(0, end_ms - entry.start_ms)


def _pregap_has_audio(
    ffmpeg: str, src: Path, start_sec: float, end_sec: float
) -> bool:
    """预间隙 [INDEX 00, INDEX 01) 里是否藏有真实音频。

    有些 CD 会把隐藏曲/加长尾奏放在预间隙里（实测本样本第 13→14 轨之间
    就有约 2 分钟 -10dB 的内容）。这种间隙绝不能按静音裁掉，否则丢音频。
    在间隙的 10% / 50% / 90% 各取 ≤1 秒做 RMS 检测，超过 -75dBFS 即视为有内容。
    """
    duration = end_sec - start_sec
    if duration <= 0.3 or end_sec <= start_sec:
        return False
    import numpy as np

    for frac in (0.1, 0.5, 0.9):
        ss = start_sec + duration * frac
        take = min(1.0, max(0.2, end_sec - ss))
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", f"{ss:.3f}", "-t", f"{take:.3f}",
            "-i", str(src), "-map", "0:a:0", "-ac", "1",
            "-f", "s16le", "-acodec", "pcm_s16le", "-",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.TimeoutExpired, OSError):
            return True  # 检测失败时保守处理：不裁剪
        if proc.returncode != 0 or not proc.stdout:
            continue
        data = np.frombuffer(proc.stdout, dtype="<i2")
        if data.size == 0:
            continue
        rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
        dbfs = 20.0 * np.log10(max(rms, 1.0) / 32768.0)
        if dbfs > -75.0:
            return True
    return False


def split_cue_sheet(
    track: Track,
    output_dir: Path,
    client: HttpClient,
    *,
    use_lrclib: bool = True,
    use_netmusic: bool = True,
    use_qqmusic: bool = True,
    fetch_lyrics: bool = True,
    trim_pregap: bool = False,
    on_progress=None,
    cancel=None,
) -> SplitSummary:
    """把整轨 ``track`` 按 ``track.cue`` 切分成独立 FLAC，写标签 + 匹配歌词。

    ``output_dir`` 是输出目录；每个分轨文件名 ``NN - 标题.flac``。
    ``trim_pregap=True`` 时每轨在下一轨 INDEX 00 截止（去掉轨尾静音预间隙）；
    若预间隙里检测到真实音频（隐藏曲/尾奏），自动保留，不裁内容。
    """
    sheet = track.cue
    if sheet is None or len(sheet.tracks) <= 1:
        return SplitSummary(total=0, elapsed_sec=0.0)

    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        return SplitSummary(total=len(sheet.tracks), failed=len(sheet.tracks))

    # 末轨的 end_ms 依赖整轨总时长；CUE 自身只给出下一轨的起点。
    total_sec = track.info.duration or 0.0
    if total_sec <= 0:
        from ..core.probe import probe as _probe

        try:
            total_sec = _probe(track.path, with_cue=False).info.duration or 0.0
        except Exception:  # noqa: BLE001
            total_sec = 0.0
    if total_sec <= 0:
        # 某些容器（APE/DFF 等）mutagen 读不到时长，退回 ffmpeg
        from ..core.authenticity import _probe_duration

        try:
            total_sec = _probe_duration(ffmpeg, track.path)
        except Exception:  # noqa: BLE001
            total_sec = 0.0
    from ..core.cue import fill_durations

    fill_durations(sheet, total_sec)

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(sheet.tracks)
    total_ms = int(total_sec * 1000)
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
        start_ms, duration_ms = _entry_bounds(sheet, index - 1, total_ms, trim_pregap)
        # 安全保护：预间隙里有音频（隐藏曲/尾奏）时不裁，避免丢内容
        if trim_pregap and index < total:
            nxt = sheet.tracks[index]
            candidate_end = nxt.pregap_ms
            default_end = nxt.start_ms
            if (
                entry.start_ms < candidate_end < default_end
                and _pregap_has_audio(
                    ffmpeg, track.path,
                    candidate_end / 1000.0, default_end / 1000.0,
                )
            ):
                log.warning(
                    "轨 %s 的预间隙含音频（%.1fs），已保留而不裁剪",
                    entry.number, (default_end - candidate_end) / 1000.0,
                )
                duration_ms = default_end - entry.start_ms
        expected = duration_ms / 1000.0

        dst = output_dir / _safe_filename(entry.number, title)
        if expected <= 0.05:
            log.warning("轨 %s 时长为 %.2fs，跳过：CUE 索引可能不完整", entry.number, expected)
            summary.failed += 1
            if on_progress is not None:
                on_progress(index, total)
            continue

        reuse = False
        if dst.exists():
            # 已有输出先验时长：历史坏分轨（例如末轨 0.1s）要重切，不能盲目复用
            from ..core.probe import probe as _probe

            try:
                actual = _probe(dst, with_cue=False).info.duration or 0.0
            except Exception:  # noqa: BLE001
                actual = 0.0
            reuse = abs(actual - expected) <= 0.35
            if not reuse:
                log.info("重切异常分轨 %s：已有 %.2fs，应为 %.2fs", dst.name, actual, expected)

        if not reuse:
            ok = split_one(
                ffmpeg, track.path, dst,
                start_ms / 1000.0, expected,
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
                client, title, artist, album, expected,
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
