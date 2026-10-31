"""音频探测：格式识别、技术参数提取、标签读取、CUE 关联。

全部走流式读取 —— 只解析头部与标签区，不把音频数据载入内存，
因此 4GB 的 DSD 整轨文件也能秒开（需求 3.1.2 / 4.3）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import mutagen

from .model import (
    AudioInfo,
    CUE_EXT,
    ENCRYPTED_EXTS,
    LOSSLESS_EXTS,
    SUPPORTED_EXTS,
    Tags,
    Track,
    TrackStatus,
)
from .tags import _family, open_audio, read_tags
from . import cue as cue_mod

#: DSD 采样率 -> 速率名称
DSD_RATES = {
    2_822_400: "DSD64",
    5_644_800: "DSD128",
    11_289_600: "DSD256",
    22_579_200: "DSD512",
}


class ProbeError(Exception):
    pass


def check_extension(path: Path) -> tuple[bool, str]:
    """返回 (是否可处理, 原因)。"""
    ext = path.suffix.lower()
    if ext in ENCRYPTED_EXTS:
        return False, f"{ext} 为加密格式，本软件不解密，已跳过"
    if ext == CUE_EXT:
        return False, "CUE 为索引文件，随整轨音频一起处理"
    if ext not in SUPPORTED_EXTS:
        return False, f"不支持的扩展名 {ext or '(无)'}"
    return True, ""


def describe_audio(obj: mutagen.FileType, path: Path) -> AudioInfo:
    """从 mutagen 对象提取技术参数。"""
    info = AudioInfo(file_size=_safe_size(path))
    mod = type(obj).__module__.rsplit(".", 1)[-1]
    ext = path.suffix.lower()

    info.fmt = _format_name(obj, ext)
    info.lossless = _is_lossless(obj, ext)

    raw_info = getattr(obj, "info", None)
    if raw_info is not None:
        info.sample_rate = getattr(raw_info, "sample_rate", None)
        info.channels = getattr(raw_info, "channels", None)
        info.bit_depth = getattr(raw_info, "bits_per_sample", None)
        info.duration = _safe_float(getattr(raw_info, "length", None))
        info.bitrate = _safe_int(getattr(raw_info, "bitrate", None))

    # ALAC / AAC 的 m4a 需靠 codec 判定
    if mod == "mp4":
        codec = str(getattr(raw_info, "codec", "") or "").lower()
        if "alac" in codec:
            info.lossless = True
            info.fmt = "ALAC"
        elif "aac" in codec:
            info.lossless = False
            info.fmt = "AAC"

    # DSD 速率标注
    if info.sample_rate and info.sample_rate in DSD_RATES:
        info.dsd_rate = DSD_RATES[info.sample_rate]
        info.bit_depth = None

    # Opus 固定 48kHz 输出，标注真实输入采样率不可得
    if info.sample_rate and info.sample_rate > 3_000_000 and not info.dsd_rate:
        info.dsd_rate = f"{info.sample_rate / 44100:g}xDSD"

    return info


def _format_name(obj: mutagen.FileType, ext: str) -> str:
    names = {
        "flac": "FLAC",
        "mp3": "MP3",
        "mp4": "M4A",
        "wave": "WAV",
        "aiff": "AIFF",
        "monkeysaudio": "APE",
        "wavpack": "WV",
        "trueaudio": "TTA",
        "dsf": "DSF",
        "dsdiff": "DFF",
        "oggvorbis": "OGG",
        "oggopus": "OPUS",
        "aac": "AAC",
        "musepack": "MPC",
        "asf": "WMA",
    }
    mod = type(obj).__module__.rsplit(".", 1)[-1]
    return names.get(mod, ext.lstrip(".").upper() or "?")


def _is_lossless(obj: mutagen.FileType, ext: str) -> bool:
    if ext in (".flac", ".ape", ".wav", ".wave", ".wv", ".tta", ".dsf", ".dff", ".aif", ".aiff"):
        return True
    if ext == ".m4a":
        mod = type(obj).__module__.rsplit(".", 1)[-1]
        if mod == "mp4":
            codec = str(getattr(getattr(obj, "info", None), "codec", "") or "").lower()
            return "alac" in codec
        return True
    return False


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def find_cue_for(audio_path: Path) -> Optional[Path]:
    """在同目录查找指向该音频的 CUE 文件。"""
    folder = audio_path.parent
    try:
        cues = [p for p in folder.iterdir() if p.suffix.lower() == CUE_EXT]
    except OSError:
        return None
    if not cues:
        return None
    stem = audio_path.stem.lower()
    # 同名优先
    for c in cues:
        if c.stem.lower() == stem:
            return c
    # 其次按 CUE 内 FILE 声明匹配
    for c in cues:
        try:
            sheet = cue_mod.parse_cue(c)
        except Exception:
            continue
        if sheet.audio_file and sheet.audio_file.name.lower() == audio_path.name.lower():
            return c
    return cues[0] if len(cues) == 1 else None


def probe(path: Path | str, *, with_cue: bool = True) -> Track:
    """读取单个音频文件，返回可直接进列表的 Track。"""
    path = Path(path)
    ok, reason = check_extension(path)
    track = Track(path=path)
    if not path.exists():
        track.status = TrackStatus.FAILED
        track.note("文件不存在")
        return track
    if not ok:
        track.status = TrackStatus.UNSUPPORTED
        track.note(reason)
        return track

    obj = open_audio(path)
    if obj is None:
        track.status = TrackStatus.FAILED
        track.note("无法解析：格式不支持或文件损坏")
        return track

    track.info = describe_audio(obj, path)
    try:
        tags = read_tags(path, obj)
    except Exception as exc:  # noqa: BLE001
        tags = Tags()
        track.note(f"标签读取异常：{exc}")

    track.original = tags
    track.working = tags.copy()

    if with_cue:
        cue_path = find_cue_for(path)
        if cue_path is not None:
            try:
                sheet = cue_mod.parse_cue(cue_path)
                track.cue = sheet
                if sheet.title:
                    track.note(f"已关联 CUE（{sheet.encoding_used}）：{sheet.title}")
                track.note(f"CUE 含 {len(sheet.tracks)} 个曲目")
            except Exception as exc:  # noqa: BLE001
                track.note(f"CUE 解析失败：{exc}")

    track.status = TrackStatus.PENDING
    return track


def scan_folder(folder: Path | str, *, recursive: bool = True) -> list[Track]:
    """扫描目录下全部受支持音频（跳过加密格式并记录提示）。"""
    folder = Path(folder)
    tracks: list[Track] = []
    if not folder.is_dir():
        return tracks
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    for entry in iterator:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        ext = entry.suffix.lower()
        if ext in SUPPORTED_EXTS:
            tracks.append(probe(entry))
        elif ext in ENCRYPTED_EXTS:
            t = Track(path=entry)
            t.status = TrackStatus.UNSUPPORTED
            t.note(f"{ext} 加密格式，不支持解析与解密，已跳过")
            tracks.append(t)
    tracks.sort(key=lambda t: str(t.path).lower())
    return tracks


def iter_fast_meta(paths: list[Path]) -> list[Track]:
    """批量读取（供后台线程调用，逐条产出避免长阻塞）。"""
    return [probe(p) for p in paths]


def guess_from_filename(path: Path) -> Tags:
    """从文件名粗猜标签：常见 ``歌手 - 歌名``、``01. 歌名`` 形态。"""
    stem = path.stem
    cleaned = stem.strip()
    # 处理 "01 - 歌手 - 歌名" / "01. 歌名" 两种常见形态
    body = cleaned
    tokens = body.split(" - ")
    if len(tokens) >= 3 and tokens[0].strip().isdigit():
        return Tags(title=tokens[-1].strip(), artist=tokens[1].strip(), track=tokens[0].strip())
    if len(tokens) == 2:
        left, right = tokens[0].strip(), tokens[1].strip()
        num = left.split(".")[0].strip()
        if left.split(".")[0].strip().isdigit() and "." in left:
            return Tags(title=right, track=num, artist="")
        return Tags(artist=left, title=right)
    # "01. 歌名"
    for dot in (". ", "、", "."):
        if dot in body:
            head, _, tail = body.partition(dot)
            if head.strip().isdigit() and tail.strip():
                return Tags(track=head.strip(), title=tail.strip())
            break
    return Tags(title=cleaned)
