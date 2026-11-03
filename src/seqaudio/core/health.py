"""曲库体检：重复文件、缺失项、规格与标签一致性汇总。

查重先用文件大小分组，只对同尺寸的候选文件读首/中/尾各 1MB 做快速哈希，
因此 1000 首曲库也不会把每个文件完整读一遍；标签层面的「疑似同一首歌」
再按 标题 + 歌手 + 时长 聚合，只作人工复核线索，不自动删除。
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Optional

from .cover import folder_has_cover
from .lyrics import sidecar_path
from .model import Track, TrackStatus

#: 快速哈希读取的首/尾字节数
HASH_BYTES = 1024 * 1024


@dataclass
class HealthIssue:
    category: str
    track: Track
    detail: str


@dataclass
class DuplicateGroup:
    kind: str                      # 「完全相同」/「疑似同一首歌」
    tracks: list[Track]
    detail: str
    wasted_bytes: int = 0


@dataclass
class HealthReport:
    tracks: list[Track] = field(default_factory=list)
    issues: list[HealthIssue] = field(default_factory=list)
    duplicates: list[DuplicateGroup] = field(default_factory=list)
    total_bytes: int = 0

    def count(self, category: str) -> int:
        return sum(1 for issue in self.issues if issue.category == category)

    @property
    def fake_count(self) -> int:
        return self.count("假无损") + self.count("低规格（假无损）")

    @property
    def low_spec_count(self) -> int:
        return self.count("低规格（假无损）")

    @property
    def unanalysed_count(self) -> int:
        return self.count("尚未鉴定") + self.count("无法判定")

    @property
    def duplicate_files(self) -> int:
        """参与重复组的唯一文件数（同一文件可能同时出现在两组里）。"""
        return len({id(track) for group in self.duplicates for track in group.tracks})

    @property
    def redundant_bytes(self) -> int:
        return sum(group.wasted_bytes for group in self.duplicates)


def _quick_digest(path: Path, size: int) -> str:
    """文件大小 + 首/中/尾各 1MB 的 SHA1，用于快速判定完全相同的副本。"""
    digest = hashlib.sha1(str(size).encode("ascii"))
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(min(HASH_BYTES, size)))
            if size > HASH_BYTES * 2:
                handle.seek(size // 2)
                digest.update(handle.read(HASH_BYTES))
            if size > HASH_BYTES:
                handle.seek(max(0, size - HASH_BYTES))
                digest.update(handle.read(HASH_BYTES))
    except OSError:
        return ""
    return digest.hexdigest()


def _normal(text: str) -> str:
    return re.sub(r"[\s._\-\[\]()（）·,，。:：/]+", "", (text or "").lower())


def _filename_mismatch(track: Track) -> bool:
    title = track.working.title or track.original.title
    if not title:
        return False
    stem = re.sub(r"^[\s._\-]*\d{1,3}[\s._\-]+", "", track.path.stem)
    a, b = _normal(title), _normal(stem)
    if not a or not b:
        return False
    if a in b or b in a:
        return False
    # 文件名里常带歌手/专辑前缀，相似度仍应足够高
    return SequenceMatcher(None, a, b).ratio() < 0.55


def _group_albumartist_issues(tracks: list[Track], report: HealthReport) -> None:
    groups: dict[tuple[str, str], list[Track]] = defaultdict(list)
    for track in tracks:
        album = (track.working.album or track.original.album or "").strip()
        if album:
            groups[(str(track.path.parent).lower(), album.lower())].append(track)
    for items in groups.values():
        if len(items) < 2:
            continue
        names = {
            (t.working.albumartist or t.original.albumartist or "").strip()
            for t in items
        }
        names.discard("")
        if len(names) > 1:
            for track in items:
                report.issues.append(
                    HealthIssue(
                        "专辑艺术家不一致",
                        track,
                        f"同专辑出现多种专辑艺术家：{'、'.join(sorted(names))}",
                    )
                )


def find_duplicates(tracks: list[Track]) -> list[DuplicateGroup]:
    """返回完全相同的文件副本 + 疑似同一首歌的标签聚合。"""
    plain = [t for t in tracks if t.cue_track is None and t.exists]
    groups: list[DuplicateGroup] = []

    # ---- 1. 完全相同的文件：先按大小分组，只对候选做快速哈希 ----
    by_size: dict[int, list[Track]] = defaultdict(list)
    for track in plain:
        try:
            size = track.path.stat().st_size
        except OSError:
            continue
        if size > 0:
            by_size[size].append(track)

    exact_keys: set[int] = set()
    for size, items in by_size.items():
        if len(items) < 2:
            continue
        by_hash: dict[str, list[Track]] = defaultdict(list)
        for track in items:
            digest = _quick_digest(track.path, size)
            if digest:
                by_hash[digest].append(track)
        for digest, same in by_hash.items():
            if len(same) < 2:
                continue
            wasted = size * (len(same) - 1)
            groups.append(
                DuplicateGroup(
                    "完全相同",
                    same,
                    f"文件大小 {size / 1024 / 1024:.1f} MB，首/中/尾哈希一致",
                    wasted,
                )
            )
            exact_keys.update(id(t) for t in same)

    # ---- 2. 疑似同一首歌：标题 + 歌手 + 时长（1 秒容差） ----
    by_signature: dict[tuple[str, str, int], list[Track]] = defaultdict(list)
    for track in plain:
        title = (track.working.title or track.original.title or "").strip()
        artist = (track.working.artist or track.original.artist or "").strip()
        duration = track.info.duration or 0
        if title and duration:
            by_signature[(_normal(title), _normal(artist), round(duration))].append(track)

    for (title, artist, duration), items in by_signature.items():
        if len(items) < 2 or not title:
            continue
        # 同目录同文件的 CUE 分轨不参与（已经被 cue_track 过滤）
        if all(id(t) in exact_keys for t in items):
            continue
        # 同一路径的重复条目不算重复歌曲
        paths = {str(t.path).lower() for t in items}
        if len(paths) < 2:
            continue
        groups.append(
            DuplicateGroup(
                "疑似同一首歌",
                items,
                f"标题/歌手一致，时长约 {duration} 秒",
                0,
            )
        )

    groups.sort(key=lambda g: (len(g.tracks), g.wasted_bytes), reverse=True)
    return groups


def analyze_library(
    tracks: Iterable[Track],
    *,
    check_duplicates: bool = True,
    cancel: Optional[Callable[[], bool]] = None,
) -> HealthReport:
    """生成曲库体检报告。``cancel`` 为可选的线程取消检查。"""
    def cancelled() -> bool:
        return bool(cancel and cancel())

    items = list(tracks)
    report = HealthReport(tracks=items)
    sizes = {
        str(track.path).lower(): track.info.file_size
        for track in items
    }
    report.total_bytes = sum(sizes.values())

    folders_with_cover: dict[str, bool] = {}
    for track in items:
        folder = str(track.path.parent).lower()
        if folder not in folders_with_cover:
            folders_with_cover[folder] = folder_has_cover(track.path.parent)
        if not folders_with_cover[folder] and not track.working.cover:
            report.issues.append(HealthIssue("缺少封面", track, "文件与所在文件夹都没有封面"))

        if not track.working.lyrics and not sidecar_path(track.path).exists():
            report.issues.append(HealthIssue("缺少歌词", track, "内嵌歌词与同名 .lrc 都没有"))

        auth = track.authenticity
        if auth is None:
            report.issues.append(HealthIssue("尚未鉴定", track, "还没有跑真假无损鉴定"))
        elif auth.verdict == "假无损":
            category = "低规格（假无损）" if auth.spec_low else "假无损"
            detail = auth.detail_text or auth.verdict
            report.issues.append(HealthIssue(category, track, detail))
        elif auth.verdict == "无法判定":
            report.issues.append(HealthIssue("无法判定", track, auth.detail_text or auth.error))

        if track.status is TrackStatus.FAILED:
            report.issues.append(HealthIssue("读取/写入失败", track, " | ".join(track.notes)))
        elif track.status is TrackStatus.UNSUPPORTED:
            report.issues.append(HealthIssue("格式不支持", track, " | ".join(track.notes)))

        if _filename_mismatch(track):
            report.issues.append(
                HealthIssue(
                    "文件名与歌名不一致",
                    track,
                    f"歌名「{track.working.title or track.original.title}」与文件名「{track.path.name}」差异较大",
                )
            )

        if cancelled():
            break

    if not cancelled():
        _group_albumartist_issues(items, report)
        if check_duplicates:
            report.duplicates = find_duplicates(items)
    return report


def write_health_report(report: HealthReport, target: Path | str) -> Path:
    """导出健康报告 CSV（UTF-8-SIG，Excel 直接打开不乱码）。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["类型", "歌名", "歌手", "专辑", "文件路径", "说明"])
        for issue in report.issues:
            track = issue.track
            writer.writerow(
                [
                    issue.category,
                    track.working.title or track.original.title or track.path.stem,
                    track.working.artist or track.original.artist,
                    track.working.album or track.original.album,
                    str(track.path),
                    issue.detail,
                ]
            )
        for group in report.duplicates:
            for index, track in enumerate(group.tracks):
                writer.writerow(
                    [
                        group.kind,
                        track.working.title or track.original.title or track.path.stem,
                        track.working.artist or track.original.artist,
                        track.working.album or track.original.album,
                        str(track.path),
                        group.detail + ("（建议保留）" if index == 0 else "（重复副本）"),
                    ]
                )
    return target
