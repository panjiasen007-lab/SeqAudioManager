"""SACD ISO 光盘镜像解析。

SACD ISO 是 SACD 光盘的原始镜像，ffmpeg / mutagen 都读不了：

* 曲目表、声道、时长在 Master TOC（第 510 扇区，偏移 1,044,480）；
* 专辑/艺人文本在紧随其后的 ``SACDText`` 区块；
* 音频是 DSD64（2.8224MHz / 1bit），不少盘还用 DST 压缩。

因此读取与导出都调用随包分发的 ``sacd_extract``（sacd-ripper 的独立命令行
程序）。``-P`` 的文本标题会经过一次有损转码变成乱码，但专辑/艺人可以直接
从 ISO 的 ``SACDText`` 原始字节还原（本样本是 GBK）；逐轨标题读同目录的
``专辑简介.txt`` / ``*.nfo`` 的「专辑曲目」段，读不到才退回 ``Track NN``。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .model import AudioInfo, CueSheet, CueTrack, Tags, Track, TrackStatus
from . import paths

log = logging.getLogger(__name__)

#: SACD 扇区大小与 Master TOC 位置（与 sacd-ripper 的常量一致）
SACD_SECTOR_SIZE = 2048
SACD_TEXT_SCAN_BYTES = 4 * 1024 * 1024

#: 调 ``-P`` 的超时（2GB 镜像也只需要读头部）
PRINT_TIMEOUT = 120.0

#: DSD64 固定参数
SACD_SAMPLE_RATE = 2_822_400
SACD_BIT_DEPTH = 1
SACD_DSD_RATE = "DSD64"

#: 同目录文本候选的优先级关键字
_TEXT_PRIORITY = ("曲目", "简介", "专辑", "info", "track", "readme")
_TEXT_EXTS = (".txt", ".nfo")

_TIME_RE = re.compile(r"(\d{1,3})\s*:\s*(\d{1,2})\s*:\s*(\d{1,3})")
_AREA_RE = re.compile(r"Area\s+Information\s*\[\d+\]", re.IGNORECASE)
_TRACK_LINE_RE = re.compile(
    r"^\s*[\(\[（【]?\s*(\d{1,3})\s*[\)\]）】]?\s*"
    r"(?:[.、．)）:：\-–—]|\s)+(.+?)\s*$"
)
_SECTION_RE = re.compile(
    r"^(?:专辑曲目|专辑音轨|曲目列表|曲目|音轨列表|音轨|Track\s*List|Tracklist|Tracks)\s*[：:]?\s*$",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(r"^\s*([^：:]{1,24})\s*[：:]\s*(.+?)\s*$")
_DATE_RE = re.compile(r"(\d{4})(?:\s*[年./\-]\s*(\d{1,2}))?")


@dataclass
class SacdArea:
    """``-P`` 输出里的一个声道区（2 Channel / 5.1 Channel）。"""

    description: str = ""
    track_count: int = 0
    total_ms: int = 0
    speaker: str = ""
    starts: list[int] = field(default_factory=list)
    durations: list[int] = field(default_factory=list)

    @property
    def channels(self) -> int:
        text = self.speaker.lower()
        if "stereo" in text or "2 channel" in text:
            return 2
        multi = re.search(r"(\d+)\.(\d+)", text)
        if multi:
            return int(multi.group(1)) * 2 + int(multi.group(2))
        single = re.search(r"(\d+)\s*channel", text)
        if single:
            return int(single.group(1))
        return 2

    @property
    def track_numbers(self) -> list[int]:
        return list(range(1, len(self.starts) + 1))


@dataclass
class SidecarInfo:
    """同目录说明文本里解析出的元数据与逐轨标题。"""

    source: str = ""
    album: str = ""
    artist: str = ""
    date: str = ""
    titles: dict[int, str] = field(default_factory=dict)


# ---------------------------------------------------------------- 工具定位


def _tool_names() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return ("sacd_extract.exe", "sacd_extract")
    return ("sacd_extract", "sacd_extract.exe")


def tool_cwd() -> str:
    """给 ``sacd_extract`` 一个纯 ASCII 的工作目录。

    它启动时会用 ANSI 接口读当前目录，路径含中文时会把中文字符丢掉
    （实测把 ``音序管家-v1.5.1`` 变成 ``-v1.5.1``），随后报「目录不存在」
    并退出。用户在中文名文件夹里双击便携版正好会触发这个问题。
    """
    candidates = [
        Path(tempfile.gettempdir()),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp",
        Path("C:/"),
    ]
    for candidate in candidates:
        try:
            if candidate.is_dir() and all(ord(ch) < 128 for ch in str(candidate)):
                return str(candidate)
        except OSError:
            continue
    return str(Path(tempfile.gettempdir()))


def find_sacd_extract() -> Optional[str]:
    """定位随包的 sacd_extract，其次找系统 PATH。"""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "tools")
    tool_dir = paths.asset_path("tools")
    if tool_dir is not None:
        candidates.append(tool_dir)
    root = paths.app_dir()
    candidates += [
        root / "assets" / "tools",
        root / "tools",
        root / "data" / "tools",
    ]
    seen: set[str] = set()
    for folder in candidates:
        key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        for name in _tool_names():
            candidate = folder / name
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return str(candidate)
            except OSError:
                continue
    return shutil.which("sacd_extract")


# ---------------------------------------------------------------- -P 输出


def parse_timecode(text: str) -> int:
    """``MM:SS:FF``（CD 帧，75 帧/秒）转毫秒。"""
    match = _TIME_RE.search(text or "")
    if not match:
        return 0
    minutes, seconds, frames = (int(x) for x in match.groups())
    return minutes * 60_000 + seconds * 1_000 + int(frames * 1000 / 75)


def parse_print_output(text: str) -> list[SacdArea]:
    """解析 ``sacd_extract -P`` 的曲目区信息。

    一个镜像可能同时有 Stereo 与 Multi-Channel 两个区，每个区都有独立的
    Track Count / 时长 / 音轨列表，因此按 ``Area Information [n]`` 分段。
    """
    areas: list[SacdArea] = []
    current: Optional[SacdArea] = None
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if _AREA_RE.search(line):
            current = SacdArea()
            areas.append(current)
            continue
        if current is None:
            continue
        low = line.lower()
        if low.startswith("area description"):
            current.description = line.split(":", 1)[-1].strip()
        elif low.startswith("track count"):
            value = line.split(":", 1)[-1].strip()
            current.track_count = int(value) if value.isdigit() else 0
        elif low.startswith("total play time"):
            current.total_ms = parse_timecode(line)
        elif low.startswith("speaker config"):
            current.speaker = line.split(":", 1)[-1].strip()
        elif low.startswith("track_start_time_code"):
            current.starts.append(parse_timecode(line))
        elif low.startswith("duration"):
            current.durations.append(parse_timecode(line))
    return areas


def select_stereo_area(areas: list[SacdArea]) -> Optional[SacdArea]:
    """优先 2 Channel 区，没有立体声区时退回第一个区。"""
    if not areas:
        return None
    for area in areas:
        if area.channels == 2 and area.starts:
            return area
    return next((area for area in areas if area.starts), areas[0])


# ---------------------------------------------------------------- SACDText


def _decode_sacd_text(raw: bytes) -> str:
    raw = (raw or b"").split(b"\x00", 1)[0].strip()
    if not raw:
        return ""
    for encoding in ("utf-8", "gb18030", "big5", "cp1252"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text:
            return text.strip()
    return raw.decode("cp1252", "replace").strip()


def _text_at(block: bytes, offset: int) -> str:
    if offset <= 8 or offset >= len(block):
        return ""
    return _decode_sacd_text(block[offset : offset + 256])


def read_sacd_text(iso_path: Path | str) -> tuple[str, str]:
    """从 ISO 的 ``SACDText`` 区块读取 (专辑名, 专辑歌手)。

    该区块在 Master TOC 之后（扇区 511），头部四个大端 u16 偏移分别指向
    专辑名 / 专辑歌手 / 碟名 / 碟歌手，均为 NUL 结尾字符串。
    """
    path = Path(iso_path)
    try:
        with path.open("rb") as handle:
            data = handle.read(SACD_TEXT_SCAN_BYTES)
    except OSError:
        return "", ""
    index = data.find(b"SACDText")
    if index < 0:
        return "", ""
    block = data[index : index + 2048]
    if len(block) < 0x24:
        return "", ""

    def offset(at: int) -> int:
        return int.from_bytes(block[at : at + 2], "big")

    album = _text_at(block, offset(0x10))
    artist = _text_at(block, offset(0x12))
    disc = _text_at(block, offset(0x20))
    disc_artist = _text_at(block, offset(0x22))
    return album or disc, artist or disc_artist


# ---------------------------------------------------------------- 同目录文本


def _decode_text_file(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", "replace")


def _sidecar_files(folder: Path) -> list[Path]:
    try:
        entries = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        return []
    candidates = [p for p in entries if p.suffix.lower() in _TEXT_EXTS]

    def score(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        rank = next(
            (i for i, keyword in enumerate(_TEXT_PRIORITY) if keyword in name),
            len(_TEXT_PRIORITY),
        )
        return rank, name

    return sorted(candidates, key=score)


def _parse_metadata_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _FIELD_RE.match(line)
        if not match:
            continue
        key = re.sub(r"\s+", "", match.group(1)).lower()
        value = match.group(2).strip()
        if not value:
            continue
        if key in ("专辑名称", "专辑名", "album", "albumtitle", "albumname"):
            fields.setdefault("album", value)
        elif key in ("专辑歌手", "歌手", "artist", "performer", "albumartist"):
            fields.setdefault("artist", value)
        elif key in ("发行时间", "发行日期", "发行年份", "releasedate", "date", "year"):
            fields.setdefault("date", value)
    return fields


def _normalise_date(value: str) -> str:
    match = _DATE_RE.search(value or "")
    if not match:
        return ""
    year, month = match.group(1), match.group(2)
    if month:
        return f"{year}-{int(month):02d}"
    return year


def _parse_track_titles(text: str, expected_count: int) -> dict[int, str]:
    """只解析「专辑曲目」小节；轨号必须在 1..expected_count。"""
    lines = text.splitlines()
    start = None
    for index, raw_line in enumerate(lines):
        line = unicodedata.normalize("NFKC", raw_line).strip()
        if _SECTION_RE.match(line):
            start = index + 1
            break
    if start is None:
        return {}

    titles: dict[int, str] = {}
    blanks = 0
    for raw_line in lines[start:]:
        line = unicodedata.normalize("NFKC", raw_line).strip()
        if not line:
            blanks += 1
            continue
        # 连续空行之后出现的「出版公司：」之类小节标题，说明曲目段结束了
        if blanks >= 2 and _FIELD_RE.match(line):
            break
        blanks = 0
        match = _TRACK_LINE_RE.match(line)
        if not match:
            if titles:
                break
            continue
        number = int(match.group(1))
        title = match.group(2).strip()
        if not title or not (1 <= number <= expected_count):
            continue
        titles.setdefault(number, title)
    return dict(sorted(titles.items()))


def read_folder_sidecar(iso_path: Path | str, expected_count: int) -> SidecarInfo:
    """扫描 ISO 同目录的说明文本，返回元数据与逐轨标题。"""
    folder = Path(iso_path).parent
    for candidate in _sidecar_files(folder):
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        text = _decode_text_file(data)
        if not text:
            continue
        titles = _parse_track_titles(text, expected_count)
        fields = _parse_metadata_fields(text)
        if not titles and not fields:
            continue
        return SidecarInfo(
            source=candidate.name,
            album=fields.get("album", ""),
            artist=fields.get("artist", ""),
            date=_normalise_date(fields.get("date", "")),
            titles=titles,
        )
    return SidecarInfo()


def read_folder_track_titles(
    iso_path: Path | str, expected_count: int
) -> dict[int, str]:
    """从同目录文本的「专辑曲目」段读取逐轨标题。"""
    return read_folder_sidecar(iso_path, expected_count).titles


def read_folder_release_date(iso_path: Path | str) -> str:
    """从同目录文本读取发行时间（YYYY-MM / YYYY）。"""
    return read_folder_sidecar(iso_path, 999).date


# ---------------------------------------------------------------- 探测入口


def _run_print(tool: str, iso_path: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [tool, "-P", "-i", str(iso_path)],
            capture_output=True,
            timeout=PRINT_TIMEOUT,
            cwd=tool_cwd(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return -1, f"{exc}"
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    # 多数构建把曲目表写到 stdout，但个别环境/构建会写 stderr；
    # 两边都找，避免「明明有输出却解析不到」导致导入失败。
    if "Area Information" in out or not err:
        return proc.returncode, out
    if "Area Information" in err:
        return proc.returncode, err
    return proc.returncode, out + ("\n" + err if err else "")


def probe_sacd_iso(path: Path | str) -> Track:
    """读取 SACD ISO，返回列表里的一整条「整轨」Track（cue.sacd=True）。

    多轨会由 :func:`probe.expand_cue_tracks` 展开成每轨一行，和 WAV+CUE
    的处理路径完全一致。
    """
    path = Path(path)
    track = Track(path=path)
    tool = find_sacd_extract()
    if tool is None:
        track.status = TrackStatus.FAILED
        track.note("未找到 sacd_extract，无法读取 SACD ISO（请确认 assets/tools 随包存在）")
        return track

    returncode, text = _run_print(tool, path)
    areas = parse_print_output(text)
    area = select_stereo_area(areas)
    if returncode != 0 or area is None or not area.starts:
        detail = text.strip().splitlines()[-1] if text.strip() else f"退出码 {returncode}"
        track.status = TrackStatus.FAILED
        track.note(f"SACD ISO 解析失败（退出码 {returncode}）：{detail[:200]}")
        track.note(f"解析工具：{tool}")
        track.note(f"镜像：{path}")
        return track

    count = area.track_count or len(area.starts)
    album, artist = read_sacd_text(path)
    sidecar = read_folder_sidecar(path, count)
    album = album or sidecar.album or path.stem
    artist = artist or sidecar.artist

    created = ""
    match = re.search(r"Creation date:\s*([0-9][0-9\-./]*)", text)
    if match:
        created = match.group(1).strip()
    date = sidecar.date or created

    starts = list(area.starts)
    durations = list(area.durations)
    entries: list[CueTrack] = []
    for index in range(count):
        start_ms = starts[index] if index < len(starts) else 0
        if index < len(durations):
            duration_ms = durations[index]
        elif index + 1 < len(starts):
            duration_ms = max(0, starts[index + 1] - start_ms)
        else:
            duration_ms = 0
        title = sidecar.titles.get(index + 1) or f"Track {index + 1:02d}"
        entries.append(
            CueTrack(
                number=index + 1,
                title=title,
                performer=artist,
                start_ms=start_ms,
                end_ms=start_ms + duration_ms,
            )
        )

    total_ms = area.total_ms or (entries[-1].end_ms if entries else 0)
    total_sec = total_ms / 1000.0
    channels = area.channels
    info = AudioInfo(
        fmt="SACD ISO",
        lossless=True,
        bitrate=SACD_SAMPLE_RATE * SACD_BIT_DEPTH * channels,
        sample_rate=SACD_SAMPLE_RATE,
        bit_depth=SACD_BIT_DEPTH,
        channels=channels,
        duration=total_sec,
        dsd_rate=SACD_DSD_RATE,
        file_size=path.stat().st_size if path.exists() else 0,
    )
    sheet = CueSheet(
        cue_path=path,
        audio_file=path,
        title=album,
        album=album,
        performer=artist,
        date=date,
        tracks=entries,
        encoding_used="SACD",
        sacd=True,
        area=area.description,
        channels=channels,
    )
    tags = Tags(album=album, artist=artist, albumartist=artist, date=date)
    track.info = info
    track.cue = sheet
    track.original = tags
    track.working = tags.copy()

    minutes, seconds = int(total_sec) // 60, int(total_sec) % 60
    track.note(
        f"SACD ISO：{count} 轨 · {area.speaker or f'{channels} Channel'} · "
        f"{minutes}:{seconds:02d}"
    )
    if len(areas) > 1:
        track.note(f"镜像含 {len(areas)} 个声道区，已按 2 Channel 区显示")
    if sidecar.titles:
        suffix = "全部" if len(sidecar.titles) >= count else "部分"
        track.note(f"曲目名来自 {sidecar.source}（{suffix} {len(sidecar.titles)}/{count}）")
    else:
        track.note("SACD TOC 无逐轨标题，已用 Track NN 占位")
    if album and artist:
        track.note("专辑/歌手来自 SACDText")

    track.status = TrackStatus.PENDING
    return track
