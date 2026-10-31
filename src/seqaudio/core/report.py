"""处理报表导出：CSV / Excel（需求 3.6.8）。

Excel 中文乱码的根因是缺少 BOM，这里统一写 ``utf-8-sig``，双击即可正常打开。
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .model import Track, TrackStatus

COLUMNS = [
    ("文件名", "name"),
    ("状态", "status"),
    ("歌手", "artist"),
    ("歌名", "title"),
    ("专辑", "album"),
    ("年份", "date"),
    ("流派", "genre"),
    ("格式", "format"),
    ("规格", "spec"),
    ("无损", "lossless"),
    ("采样率", "sample_rate"),
    ("位深", "bit_depth"),
    ("码率", "bitrate"),
    ("时长", "duration"),
    ("匹配置信度", "confidence"),
    ("匹配来源", "source"),
    ("变更字段", "changed"),
    ("备份文件", "backup"),
    ("耗时(ms)", "elapsed"),
    ("说明", "notes"),
    ("完整路径", "path"),
]


def _row(track: Track) -> dict[str, str]:
    info = track.info
    return {
        "name": track.path.name,
        "status": track.status.value,
        "artist": track.working.artist,
        "title": track.working.title,
        "album": track.working.album,
        "date": track.working.date,
        "genre": track.working.genre,
        "format": info.fmt,
        "spec": info.spec_text,
        "lossless": "是" if info.lossless else "否",
        "sample_rate": str(info.sample_rate or ""),
        "bit_depth": str(info.bit_depth or ""),
        "bitrate": str(info.bitrate or ""),
        "duration": info.duration_text,
        "confidence": f"{track.confidence:.0%}" if track.confidence else "",
        "source": track.source,
        "changed": ",".join(track.changed_fields),
        "backup": str(track.backup_path or ""),
        "elapsed": str(track.elapsed_ms),
        "notes": " | ".join(track.notes),
        "path": str(track.path),
    }


def export_csv(
    tracks: Iterable[Track],
    target: Path | str,
    *,
    only: Optional[set[TrackStatus]] = None,
) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [t for t in tracks if only is None or t.status in only]
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([c[0] for c in COLUMNS])
        for track in rows:
            data = _row(track)
            writer.writerow([data[key] for _, key in COLUMNS])
    return target


def export_failures(tracks: Iterable[Track], target: Path | str) -> Path:
    return export_csv(tracks, target, only={TrackStatus.FAILED, TrackStatus.UNSUPPORTED})


def default_report_name(prefix: str = "处理报表") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"


def summarize(tracks: Iterable[Track]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for track in tracks:
        key = track.status.value
        stats[key] = stats.get(key, 0) + 1
    return stats


@dataclass
class RunSummary:
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    unsupported: int = 0
    review: int = 0
    elapsed_sec: float = 0.0

    @classmethod
    def of(cls, tracks: list[Track], elapsed: float = 0.0) -> "RunSummary":
        s = cls(total=len(tracks), elapsed_sec=elapsed)
        for t in tracks:
            if t.status is TrackStatus.DONE:
                s.done += 1
            elif t.status is TrackStatus.FAILED:
                s.failed += 1
            elif t.status is TrackStatus.SKIPPED:
                s.skipped += 1
            elif t.status is TrackStatus.UNSUPPORTED:
                s.unsupported += 1
            elif t.status is TrackStatus.NEEDS_REVIEW:
                s.review += 1
        return s

    def text(self) -> str:
        return (
            f"共 {self.total} 首：完成 {self.done}，失败 {self.failed}，"
            f"待确认 {self.review}，跳过 {self.skipped}，不支持 {self.unsupported}"
        )
