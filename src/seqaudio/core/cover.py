"""封面批量导出（需求 3.6.4 封面管理的一部分）。

把列表里已读到的封面（联网匹配到的，或原文件内嵌的）批量导出为图片文件，
命名 ``歌手 - 歌名``，按 mime 选择正确扩展名。只导出内存里已有的封面，
不去逐个重新解析磁盘 —— 一千首逐首读标签太慢，导出封面属于低频整理动作。
"""

from __future__ import annotations

import re
from pathlib import Path

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
