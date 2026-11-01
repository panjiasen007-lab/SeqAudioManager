"""每文件夹至少一张 cover 的编排逻辑（需求：强制每个文件夹有一张专辑封面）。

三级策略，逐级兜底，直到写出一张 cover：

1. **内存封面** —— 曲目匹配时已拿到（MusicBrainz/iTunes 等）或原文件内嵌；
2. **专辑封面** —— 用「专辑 + 歌手」联网再搜一张；
3. **歌手照片** —— Deezer / 网易云歌手头像兜底。

放在 pipeline 层（而非 core）：它需要 HttpClient 联网，core 层保持零依赖。
纯本地的读写（``folder_has_cover`` / ``write_folder_cover``）在 ``core.cover``。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..core import cover as cover_mod
from ..core.model import Track, TrackStatus
from ..net import aggregator
from ..net.http import HttpClient

log = logging.getLogger(__name__)


@dataclass
class CoverSummary:
    folders: int = 0       # 参与分组的文件夹数
    created: int = 0       # 新写入的 cover 数
    existing: int = 0      # 已有封面，跳过
    from_memory: int = 0   # 用曲目已匹配的封面
    album_cover: int = 0   # 用专辑封面（联网）
    artist_photo: int = 0  # 用歌手照片（联网兜底）
    failed: int = 0        # 三路全空，未生成

    def text(self) -> str:
        return (
            f"文件夹封面：共 {self.folders} 个文件夹，生成 {self.created}，"
            f"已有 {self.existing}，失败 {self.failed}"
            f"（内存 {self.from_memory} / 专辑 {self.album_cover} / 歌手 {self.artist_photo}）"
        )


def _group_by_folder(tracks: list[Track]) -> dict[Path, list[Track]]:
    groups: dict[Path, list[Track]] = defaultdict(list)
    for track in tracks:
        if track.status in (TrackStatus.UNSUPPORTED, TrackStatus.SKIPPED, TrackStatus.FAILED):
            continue
        try:
            groups[track.path.parent].append(track)
        except OSError:
            continue
    return groups


def _first_nonempty(tracks: list[Track], field: str) -> str:
    for track in tracks:
        value = track.working.get(field) or track.original.get(field)
        if value:
            return value
    return ""


#: 无效/占位歌手名：这些名字搜歌手照片必然命中错误的"合辑/群星"头像，
#: 应该跳过歌手照片兜底，让专辑封面（或歌名封面）顶上。
_INVALID_ARTISTS = {
    "群星", "群星合唱", "各种艺人", "合辑", "杂锦", "合集", "佚名", "未知", "未知歌手",
    "various artists", "various artist", "various", "va", "unknown", "unknown artist",
    "compilation", "soundtrack",
}


def _valid_artist(artist: str) -> bool:
    """歌手名是否值得用来搜歌手照片（排除群星/Various 等占位名）。"""
    name = (artist or "").strip().lower()
    if not name:
        return False
    if name in _INVALID_ARTISTS:
        return False
    return True


def ensure_folder_covers(
    tracks: list[Track],
    client: HttpClient,
    *,
    use_itunes: bool = True,
    use_netmusic: bool = True,
    use_qqmusic: bool = True,
    use_deezer: bool = True,
    on_progress: Optional[Callable[[int, int, Path], None]] = None,
    cancel=None,
) -> CoverSummary:
    """为每个文件夹确保一张 cover，返回统计。"""
    groups = _group_by_folder(tracks)
    summary = CoverSummary(folders=len(groups))
    if not groups:
        return summary

    total = len(groups)
    for index, (folder, group) in enumerate(groups.items(), start=1):
        if cancel is not None and cancel.is_set():
            break

        if cover_mod.folder_has_cover(folder):
            summary.existing += 1
        else:
            made = _make_one(folder, group, client, summary,
                             use_itunes=use_itunes, use_netmusic=use_netmusic,
                             use_qqmusic=use_qqmusic, use_deezer=use_deezer)
            if made:
                for track in group:
                    track.note("已生成文件夹封面")
            else:
                summary.failed += 1

        if on_progress is not None:
            on_progress(index, total, folder)

    log.info("文件夹封面：%s", summary.text())
    return summary


def _make_one(
    folder: Path,
    group: list[Track],
    client: HttpClient,
    summary: CoverSummary,
    *,
    use_itunes: bool,
    use_netmusic: bool,
    use_qqmusic: bool,
    use_deezer: bool,
) -> bool:
    # 1) 内存里已匹配/内嵌的封面
    img, mime = cover_mod.first_cover(group)
    if img:
        try:
            cover_mod.write_folder_cover(folder, img, mime)
        except OSError as exc:
            log.warning("写文件夹封面失败 %s：%s", folder, exc)
            return False
        summary.from_memory += 1
        summary.created += 1
        return True

    title = _first_nonempty(group, "title")
    artist = _first_nonempty(group, "artist")
    album = _first_nonempty(group, "album")

    # 2) 专辑封面（联网）。album 或 title 都能搜：fetch_album_cover 内部
    #    用「专辑名优先、搜不到退回歌名」的策略，所以不再因 album 为空跳过 ——
    #    否则一堆只有歌名的文件夹会直接落到歌手照片（往往还匹配错歌手）。
    img: Optional[bytes] = None
    mime = ""
    if album or title:
        img, mime, _source = aggregator.fetch_album_cover(
            client, title, artist, album,
            use_itunes=use_itunes, use_netmusic=use_netmusic, use_qqmusic=use_qqmusic,
        )
        if img:
            summary.album_cover += 1

    # 3) 歌手照片（联网兜底）。只在歌手名有效时搜 —— 群星/Various Artists
    #    这类占位名搜照片必然命中错误的合辑头像，跳过让位给专辑封面。
    if img is None and _valid_artist(artist):
        img, mime, _source = aggregator.fetch_artist_photo(
            client, artist, use_deezer=use_deezer, use_netmusic=use_netmusic
        )
        if img:
            summary.artist_photo += 1
        else:
            return False
    elif img is None:
        return False

    try:
        cover_mod.write_folder_cover(folder, img, mime)
    except OSError as exc:
        log.warning("写文件夹封面失败 %s：%s", folder, exc)
        return False
    summary.created += 1
    return True
