"""音频探测：格式识别、技术参数提取、标签读取、CUE 关联。

全部走流式读取 —— 只解析头部与标签区，不把音频数据载入内存，
因此 4GB 的 DSD 整轨文件也能秒开（需求 3.1.2 / 4.3）。
"""

from __future__ import annotations

import os
import re
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
    info.lossless = _is_lossless(obj, ext, path)
    if info.fmt == "WMA" and info.lossless:
        info.fmt = "WMA Lossless"

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

    # DSD 速率标注。注意不要清掉 bit_depth —— DSD 是 1-bit 采样，
    # 报表里「1 bit / 2822400 Hz」正是它和 PCM 的本质区别，抹掉反而丢信息
    if info.sample_rate and info.sample_rate in DSD_RATES:
        info.dsd_rate = DSD_RATES[info.sample_rate]
        if not info.bit_depth:
            info.bit_depth = 1

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
        "oggflac": "Ogg-FLAC",
        "oggopus": "OPUS",
        "aac": "AAC",
        "musepack": "MPC",
        "asf": "WMA",
    }
    mod = type(obj).__module__.rsplit(".", 1)[-1]
    return names.get(mod, ext.lstrip(".").upper() or "?")


def _aiff_compression(path: Path) -> str:
    """从 AIFF/AIFF-C 头读压缩类型（'NONE' 表示未压缩 PCM）。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or head[:4] != b"FORM":
                return ""
            form = head[8:12]
            if form == b"AIFF":
                return "NONE"
            pos = 12
            while pos < 4096:
                fh.seek(pos)
                chunk = fh.read(8)
                if len(chunk) < 8:
                    break
                cid, size = chunk[:4], int.from_bytes(chunk[4:], "big")
                if cid == b"COMM":
                    data = fh.read(min(size, 64))
                    # AIFC 的 COMM 在采样率(10B)之后还有 4 字节压缩类型
                    if form == b"AIFC" and len(data) >= 22:
                        return data[18:22].decode("latin-1").strip()
                    return "NONE"
                pos += 8 + size + (size & 1)
    except OSError:
        return ""
    return ""


def _wav_format_tag(path: Path) -> int:
    """从 WAV 的 fmt 块读 audioFormat（1=PCM，3=float，0x11=IMA ADPCM…）。"""
    try:
        with path.open("rb") as fh:
            data = fh.read(64)
        idx = data.find(b"fmt ")
        if idx >= 0 and len(data) >= idx + 10:
            return int.from_bytes(data[idx + 8 : idx + 10], "little")
    except OSError:
        return -1
    return -1


def _is_lossless(obj: mutagen.FileType, ext: str, path: Path | None = None) -> bool:
    """按 mutagen 类与编解码器判定是否无损，而不是只看扩展名。

    扩展名会骗人：`.ogg/.oga` 既可能是 Vorbis（有损）也可能是 Ogg-FLAC（无损），
    `.wma` 既可能是 WMA Pro 也可能包含 WMA Lossless，`.aif` 还可能是 IMA ADPCM。
    """
    mod = type(obj).__module__.rsplit(".", 1)[-1]
    raw = getattr(obj, "info", None)
    codec = str(
        getattr(raw, "codec", "") or getattr(raw, "codec_name", "") or ""
    ).lower()

    # 原生无损容器/编码
    if mod in ("flac", "wavpack", "trueaudio", "monkeysaudio", "dsf", "dsdiff", "oggflac"):
        return True
    # MP4 家族：ALAC 无损，AAC 有损
    if mod == "mp4":
        return "alac" in codec
    # ASF/WMA：只有 wmalossless 编解码器是无损
    if mod == "asf":
        return ("wmalossless" in codec) or ("lossless" in codec)
    # WAV：fmt 块里的 audioFormat 1/3 是 PCM/浮点；ADPCM/µ-law/A-law 有损
    if mod == "wave" and path is not None:
        tag = _wav_format_tag(path)
        if tag >= 0:
            return tag in (1, 3, 0xFFFE)
    # AIFF/AIFF-C：compression type 'NONE' 或 PCM 系为无损；'ima4'/MACE 有损
    if mod == "aiff" and path is not None:
        comp = _aiff_compression(path).upper()
        if comp:
            return comp in ("NONE", "SOWT", "TWOS", "FL32", "FL64", "IN24", "IN32")
    # 兜底：按扩展名（保持对未知 mutagen 类的兼容）
    if ext in (".flac", ".ape", ".wv", ".tta", ".dsf", ".dff"):
        return True
    if ext in (".aif", ".aiff", ".wav", ".wave", ".caf"):
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
                # 用整轨时长补齐最后一轨的结束时间；否则最后一轨 duration=0，
                # 分轨导出会只切出 0.1 秒。
                cue_mod.fill_durations(sheet, track.info.duration or 0.0)
                if sheet.title:
                    track.note(f"已关联 CUE（{sheet.encoding_used}）：{sheet.title}")
                track.note(f"CUE 含 {len(sheet.tracks)} 个曲目")
            except Exception as exc:  # noqa: BLE001
                track.note(f"CUE 解析失败：{exc}")

    track.status = TrackStatus.PENDING
    return track


def expand_cue_tracks(tracks: list[Track]) -> list[Track]:
    """多轨 CUE 整轨展开成多个 Track（每轨一个），让列表显示所有歌曲。

    整轨 WAV/APE/FLAC 抓轨通常带一个 CUE 索引，里面是多首歌。展开后每一轨
    成为列表里独立的一行，标题/歌手/专辑/轨道号/时长都取自 CUE；单轨 CUE
    或没有 CUE 的文件保持原样。

    展开出的分轨条目共享同一个磁盘文件（``cue_track`` 记录它是第几轨），
    供鉴定去重、写标签跳过等下游逻辑识别，避免对同一文件重复处理。
    """
    out: list[Track] = []
    for track in tracks:
        cue = track.cue
        if cue is None or len(cue.tracks) <= 1:
            out.append(track)
            continue
        cue_mod.fill_durations(cue, track.info.duration or 0.0)
        total = len(cue.tracks)
        for index, entry in enumerate(cue.tracks):
            title = entry.title or f"Track {entry.number:02d}"
            artist = entry.performer or cue.performer or ""
            tags = Tags(
                title=title,
                artist=artist,
                album=cue.album or cue.title or "",
                albumartist=cue.performer or "",
                date=cue.date or "",
                genre=cue.genre or "",
                track=f"{entry.number}/{total}",
            )
            t = Track(path=track.path, info=track.info)
            t.cue = cue
            t.cue_track = index
            t.original = tags
            t.working = tags.copy()
            t.status = track.status
            t.notes = list(track.notes)
            out.append(t)
    return out


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


#: 曲目号前缀：``01.`` / ``1、`` / ``03)`` / ``07 -``
_TRACK_PREFIX = re.compile(r"^(\d{1,4})\s*[\.、\)\]]\s*(.+)$")

#: 歌手与歌名之间的分隔符，长分隔符优先匹配
_NAME_SEPARATORS = (" - ", " – ", " — ", "－", "—", "–")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _split_once(text: str) -> tuple[str, str, str]:
    """按最长可用分隔符切一次，返回 (左, 分隔符, 右)。"""
    for sep in _NAME_SEPARATORS:
        if sep in text:
            left, _, right = text.partition(sep)
            return left.strip(), sep, right.strip()
    return text.strip(), "", ""


#: 目录名里常见的格式/规格标记，推断专辑名时要剥掉
_FOLDER_NOISE = re.compile(
    r"\b(?:DSD\d*|DFF|DSF|SACD|FLAC|APE|WAV|WV|TTA|ALAC|AAC|MP3|OGG|Opus"
    r"|Hi-?Res|HQ|HIRES|24bit|16bit|192kHz|96kHz|44\.1kHz)\b",
    re.IGNORECASE,
)
_FOLDER_BRACKETS = re.compile(r"[\[【(（][^\]】)）]{0,40}[\]】)）]")
_FOLDER_YEAR_TAIL = re.compile(r"[-–—_]\s*(?:19|20)\d{2}\s*$")


def _looks_like_catalog_id(text: str) -> bool:
    """判断是否为资源编号（2XHDFT1223、WWW-1234），这类尾巴不该当专辑名。"""
    compact = text.replace(" ", "")
    if not compact or not re.fullmatch(r"[A-Za-z0-9\-_]+", compact):
        return False
    # 必须字母+数字混排才算编号；纯数字如 "25" 可能是专辑名（Adele《25》）
    return (
        len(compact) <= 16
        and bool(re.search(r"[A-Za-z]", compact))
        and bool(re.search(r"\d", compact))
    )


def guess_album_from_folder(folder: Path) -> str:
    """从目录名推断专辑名。

    无损资源的目录名几乎都是「歌手 - 专辑 格式」这种写法，例如
    ``周杰伦 -七里香 DSD DFF`` / ``Beyond - 樂與怒 SACD [DSD64] - 2015 DSF``。
    这是批量处理时判断「这一批属于哪张专辑」最强、也最廉价的信号 ——
    比逐首去 MusicBrainz 猜要准得多。
    """
    name = (folder.name if isinstance(folder, Path) else str(folder)).strip()
    if not name:
        return ""

    name = _FOLDER_BRACKETS.sub(" ", name)
    name = _FOLDER_NOISE.sub(" ", name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    name = _FOLDER_YEAR_TAIL.sub("", name).strip()

    # 「歌手 - 专辑」取后半段；注意 "周杰伦 -七里香" 这种连字符前有空格、后无空格
    for sep in (" - ", " – ", " — ", "－"):
        if sep in name:
            head, tail = name.split(sep, 1)[0].strip(), name.split(sep, 1)[1].strip()
            if not tail:
                continue
            # 右侧若只是资源编号（如 "2XHDFT1223"），说明整串名字都是标题
            if _looks_like_catalog_id(tail) and head:
                return head.strip(" -_")
            return tail.strip(" -_")
    if " -" in name:
        tail = name.split(" -", 1)[1].strip()
        if tail:
            return tail.strip(" -_")

    # 「歌手-专辑」纯中文无空格连字符
    if "-" in name:
        left, _, right = name.partition("-")
        if _has_cjk(left) and _has_cjk(right) and right.strip():
            return right.strip(" -_")
    return name.strip(" -_")


def guess_from_filename(path: Path) -> Tags:
    """从文件名粗猜标签。

    覆盖的真实形态（按优先级）::

        周杰伦 - 02.七里香.dff   -> artist=周杰伦  track=2  title=七里香
        01 - 周杰伦 - 七里香.mp3 -> track=1  artist=周杰伦  title=七里香
        陈奕迅 - 富士山下.flac   -> artist=陈奕迅  title=富士山下
        07. 青花瓷.flac          -> track=7  title=青花瓷
        Adele - Hello.mp3        -> artist=Adele  title=Hello

    重点是「歌手 - 02.歌名」这种带轨道号的写法 —— 整轨抓轨、DSD 资源里
    极常见，若把轨道号并进歌名，联网检索的相似度会直接掉下来。
    """
    stem = path.stem.strip()
    if not stem:
        return Tags(title=path.name)

    left, sep, right = _split_once(stem)
    if not sep:
        # 中文歌手常用无空格连字符（李宗盛-山丘），但英文歌名里的 "-" 是专名的一部分
        # （Jay-Z、What's-Going-On），因此只在两侧都含中文时才按连字符拆
        if "-" in stem:
            dash_left, _, dash_right = stem.partition("-")
            if _has_cjk(dash_left) and _has_cjk(dash_right) and dash_left.strip():
                match = _TRACK_PREFIX.match(dash_right.strip())
                if match and match.group(2).strip():
                    return Tags(
                        artist=dash_left.strip(),
                        track=str(int(match.group(1))),
                        title=match.group(2).strip(),
                    )
                return Tags(artist=dash_left.strip(), title=dash_right.strip())
        match = _TRACK_PREFIX.match(stem)
        if match and match.group(2).strip():
            return Tags(track=str(int(match.group(1))), title=match.group(2).strip())
        return Tags(title=stem)

    # ① 首段就是轨道号："01 - 歌手 - 歌名" / "01 - 歌名"
    if left.isdigit():
        inner_left, inner_sep, inner_right = _split_once(right)
        if inner_sep and inner_left and inner_right:
            return Tags(track=str(int(left)), artist=inner_left, title=inner_right)
        match = _TRACK_PREFIX.match(right)
        if match and match.group(2).strip():
            return Tags(track=str(int(left)), title=match.group(2).strip())
        return Tags(track=str(int(left)), title=right)

    # ② 歌名一段带轨道号："歌手 - 02.歌名"
    match = _TRACK_PREFIX.match(right)
    if match and match.group(2).strip():
        return Tags(artist=left, track=str(int(match.group(1))), title=match.group(2).strip())

    # ③ 标准 "歌手 - 歌名"
    if left and right:
        return Tags(artist=left, title=right)
    return Tags(title=stem)
