"""封面批量导出（需求 3.6.4 封面管理的一部分）。

把列表里已读到的封面（联网匹配到的，或原文件内嵌的）批量导出为图片文件，
命名 ``歌手 - 歌名``，按 mime 选择正确扩展名。只导出内存里已有的封面，
不去逐个重新解析磁盘 —— 一千首逐首读标签太慢，导出封面属于低频整理动作。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .model import Track

_SAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

#: 文件夹封面可被识别的既有命名（含大小写），避免重复生成
COVER_NAMES = ("cover", "folder", "front", "albumart", "album")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _ext_for_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").split(";")[0].strip(), ".jpg")


def folder_has_cover(folder: Path) -> bool:
    """判断文件夹里是否已有封面图（cover.* / folder.* 等常见命名）。"""
    try:
        entries = {p.name.lower() for p in folder.iterdir() if p.is_file()}
    except OSError:
        return False
    for name in COVER_NAMES:
        for ext in COVER_EXTS:
            if f"{name}{ext}" in entries:
                return True
    return False


def first_cover(tracks: list[Track]) -> tuple[Optional[bytes], str]:
    """从一组曲目里取第一张内存封面（已匹配或内嵌的）。"""
    for track in tracks:
        data = track.working.cover or track.original.cover
        if data and len(data) > 64:
            mime = track.working.cover_mime or track.original.cover_mime or "image/jpeg"
            return data, mime
    return None, ""


def write_folder_cover(folder: Path, data: bytes, mime: str) -> Path:
    """把封面写为文件夹下的 cover.<ext>，返回写入路径。"""
    folder.mkdir(parents=True, exist_ok=True)
    ext = _ext_for_mime(mime)
    # 若已有 cover 且扩展名不同，只保留新的（旧的 cover 由调用方在写入前判断过）
    target = folder / f"cover{ext}"
    target.write_bytes(data)
    return target


def export_covers(tracks: list[Track], out_dir: Path) -> tuple[int, int]:
    """导出封面，返回 (成功数, 无封面数)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    exported = 0
    skipped = 0
    seen: set[str] = set()

    for track in tracks:
        data = track.working.cover or track.original.cover
        if not data or len(data) <= 64:
            skipped += 1
            continue

        mime = track.working.cover_mime or track.original.cover_mime or "image/jpeg"
        ext = _MIME_EXT.get(mime.split(";")[0].strip(), ".jpg")

        artist = track.working.artist or track.original.artist or ""
        title = track.working.title or track.original.title or track.path.stem
        base = _SAFE.sub("_", f"{artist} - {title}".strip(" -_")).strip()
        if not base:
            base = track.path.stem

        target = out_dir / f"{base}{ext}"
        index = 1
        while target.name.lower() in seen or target.exists():
            target = out_dir / f"{base} ({index}){ext}"
            index += 1
        target.write_bytes(data)
        seen.add(target.name.lower())
        exported += 1

    return exported, skipped
