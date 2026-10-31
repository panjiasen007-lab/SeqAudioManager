"""歌词处理：LRC 清洗、校验、格式归一（需求 3.3）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
META = re.compile(r"^\[(ti|ar|al|by|offset|re|ve|length|kana):(.*)\]$", re.IGNORECASE)

#: 歌词中常见的站点/制作水印特征
_LYRIC_WATERMARK = (
    r"www\.",
    r"https?://",
    r"[\w\-]+\.(com|net|cn|org|cc|top|xyz)",
    r"歌词(制作|提供|下载|编辑)",
    r"歌词.{0,12}(提供|制作|下载|编辑)",
    r"制作[：:]\s*\S{2,20}$",
    r"微信公众号",
    r"公众号[：:]",
    r"更多(歌词|音乐|歌曲)",
    r"LRC歌词",
)
_LYRIC_WM = [re.compile(p, re.IGNORECASE) for p in _LYRIC_WATERMARK]


def normalize_lrc(text: str) -> str:
    """统一换行、去除零宽字符与全角空格。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\u3000", " ")
    return text


def clean_lyrics(text: str, *, keep_meta: bool = True) -> str:
    """剔除歌词内的广告行、站点标记与冗余空行。"""
    text = normalize_lrc(text)
    if not text.strip():
        return ""

    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            lines.append("")
            continue

        meta = META.match(line)
        if meta:
            key = meta.group(1).lower()
            value = meta.group(2)
            if key == "by" and any(p.search(value) for p in _LYRIC_WM):
                continue
            if key in ("ti", "ar", "al", "offset", "length"):
                lines.append(line if keep_meta else "")
            elif key == "re" or key == "ve":
                continue
            else:
                lines.append(line if keep_meta else "")
            continue

        # 纯广告行（不含时间标签且命中水印）
        if not TIMESTAMP.search(line) and any(p.search(line) for p in _LYRIC_WM):
            continue

        # 带时间标签的广告行：删除时间标签后的文本若整段是水印，则丢弃该行
        match = TIMESTAMP.match(line)
        if match:
            payload = line[match.end():].strip()
            if payload and any(p.search(payload) for p in _LYRIC_WM):
                # 只删水印片段，保留其余内容
                cleaned = payload
                for pattern in _LYRIC_WM:
                    cleaned = pattern.sub("", cleaned)
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|/：:")
                # 清完只剩残字（如「本」），说明整行就是广告，丢弃
                if len(cleaned) < max(3, len(payload) * 0.3):
                    continue
                line = line[: match.end()] + cleaned
        lines.append(line)

    # 压缩连续空行
    result: list[str] = []
    blank = 0
    for line in lines:
        if line == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        result.append(line)

    return "\n".join(result).strip()


def is_synced(text: str) -> bool:
    """是否为带时间轴的同步歌词。"""
    return bool(TIMESTAMP.search(text or ""))


def count_timed_lines(text: str) -> int:
    return sum(1 for line in (text or "").split("\n") if TIMESTAMP.search(line))


def parse_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in normalize_lrc(text).split("\n"):
        match = META.match(line.strip())
        if match:
            meta[match.group(1).lower()] = match.group(2).strip()
    return meta


def sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".lrc")


def read_sidecar(audio_path: Path) -> Optional[str]:
    """读取同目录同名 .lrc（需求：本地已有歌词优先采用，不重复联网）。"""
    path = sidecar_path(audio_path)
    if not path.exists():
        return None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return None


def write_sidecar(audio_path: Path, lyrics: str, *, encoding: str = "utf-8") -> Path:
    path = sidecar_path(audio_path)
    text = normalize_lrc(lyrics)
    if encoding == "utf-8":
        path.write_text(text, encoding="utf-8-sig")
    else:
        path.write_text(f"[ti:]\n{text}", encoding=encoding, errors="replace")
    return path


def build_lrc(title: str, artist: str, album: str, lyrics: str) -> str:
    """为纯文本歌词补齐 LRC 头（无时间轴时仍可作为文本歌词使用）。"""
    header = [f"[ti:{title}]" if title else "", f"[ar:{artist}]" if artist else "",
              f"[al:{album}]" if album else ""]
    body = clean_lyrics(lyrics, keep_meta=False)
    return "\n".join([h for h in header if h] + ([""] if body else []) + ([body] if body else []))
