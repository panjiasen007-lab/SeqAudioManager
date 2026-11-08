"""匹配质量评测：用真实曲库衡量「元数据 / 歌词 / 封面」的成功率与准确率。

评测集取自已经分轨好的成品文件（标签来自原盘 CUE，可信）。对每首歌：

* **元数据**：用 tag 里的 title+artist+duration 去搜，看第一条是否「歌名对、
  歌手对、时长接近」。三个都中才算准确（strict），只看歌名算宽松。
* **歌词**：是否拿到带时间轴的歌词，且命中的歌名/歌手与期望一致。
* **封面**：是否拿到图，以及体积是否合理（< 1MB）。

用法::

    python tools/eval_match.py --limit 20          # 快速基线
    python tools/eval_match.py --limit 40 --json out.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from seqaudio.core.text import artist_match, similarity_strict  # noqa: E402
from seqaudio.net import aggregator  # noqa: E402
from seqaudio.net.http import HttpClient  # noqa: E402

LIBRARY = Path("D:/真无损已经修改")
EXTS = {".flac", ".wav", ".ape"}
PLACEHOLDER = ("track", "unknown", "未知名", "未知", "audio")


@dataclass
class Sample:
    path: Path
    title: str
    artist: str
    album: str
    duration: float


@dataclass
class Stat:
    total: int = 0
    meta_ok: int = 0          # 严格：歌名 + 歌手 + 时长
    meta_title_ok: int = 0    # 宽松：只要歌名对
    meta_none: int = 0        # 完全没结果
    lyrics_ok: int = 0
    lyrics_wrong: int = 0     # 有歌词但歌名对不上（错配）
    lyrics_none: int = 0
    cover_ok: int = 0
    cover_none: int = 0
    cover_huge: int = 0
    meta_ms: list = field(default_factory=list)
    lyrics_ms: list = field(default_factory=list)
    cover_ms: list = field(default_factory=list)
    failures: list = field(default_factory=list)


def collect(limit: int, seed: int = 7) -> list[Sample]:
    samples: list[Sample] = []
    if not LIBRARY.exists():
        return samples
    for path in LIBRARY.rglob("*"):
        if path.suffix.lower() not in EXTS or not path.is_file():
            continue
        try:
            from seqaudio.core.probe import probe

            info = probe(path, with_cue=False)
        except Exception:  # noqa: BLE001
            continue
        title = (info.original.title or "").strip()
        artist = (info.original.artist or "").strip()
        if not title or len(title) < 2:
            continue
        if any(p in title.lower() for p in PLACEHOLDER):
            continue
        if not info.info.duration or info.info.duration < 30:
            continue
        samples.append(
            Sample(path, title, artist, (info.original.album or "").strip(), info.info.duration)
        )
    # 同一首歌可能有多份拷贝，按 (title, artist) 去重
    seen: set[tuple[str, str]] = set()
    unique: list[Sample] = []
    for s in samples:
        key = (s.title, s.artist)
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    random.Random(seed).shuffle(unique)
    return unique[:limit]


def check_meta(sample: Sample, cands) -> tuple[bool, bool, str]:
    """返回 (严格命中, 歌名命中, 说明)。"""
    if not cands:
        return False, False, "无结果"
    first = cands[0]
    title_sim = similarity_strict(sample.title, first.title)
    artist_sim = artist_match(sample.artist, first.artist) if sample.artist else 1.0
    dur_delta = abs(first.duration - sample.duration) if first.duration else 999.0
    strict = title_sim >= 0.9 and artist_sim >= 0.5 and dur_delta <= 5.0
    loose = title_sim >= 0.9
    why = []
    if title_sim < 0.9:
        why.append(f"歌名{title_sim:.2f}")
    if artist_sim < 0.5:
        why.append(f"歌手“{first.artist}”≠“{sample.artist}”")
    if dur_delta > 5.0:
        why.append(f"时长差{dur_delta:.0f}s")
    note = (
        f"{first.source} «{first.title}»/{first.artist} {first.duration:.0f}s"
        + (f"  ✗ {'/'.join(why)}" if why else "")
    )
    return strict, loose, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--proxy", default="http://127.0.0.1:10808")
    ap.add_argument("--json", default="", help="把明细写到这个文件")
    args = ap.parse_args()

    samples = collect(args.limit)
    if not samples:
        print(f"评测集为空：{LIBRARY} 下没有可用样本")
        return 1
    print(f"评测集：{len(samples)} 首（来自 {len({s.album for s in samples})} 张专辑）\n")

    client = HttpClient(proxy=args.proxy, timeout=20.0, concurrency=2, offline=False)
    stat = Stat(total=len(samples))
    details = []

    for i, s in enumerate(samples, 1):
        row = {"title": s.title, "artist": s.artist, "duration": round(s.duration, 1)}

        t0 = time.time()
        try:
            cands = aggregator.search_metadata(
                client, s.title, s.artist, s.album, duration=s.duration
            )
        except Exception as exc:  # noqa: BLE001
            cands = []
            row["meta_error"] = f"{type(exc).__name__}: {exc}"
        stat.meta_ms.append((time.time() - t0) * 1000)
        strict, loose, note = check_meta(s, cands)
        row["meta"] = note
        row["meta_strict"] = strict
        row["meta_loose"] = loose
        if strict:
            stat.meta_ok += 1
        if loose:
            stat.meta_title_ok += 1
        if not cands:
            stat.meta_none += 1
            stat.failures.append(f"[元数据无结果] {s.title}/{s.artist}")

        t0 = time.time()
        try:
            lyr = aggregator.fetch_lyrics(
                client, s.title, s.artist, s.album, s.duration
            )
        except Exception as exc:  # noqa: BLE001
            lyr = None
            row["lyrics_error"] = f"{type(exc).__name__}: {exc}"
        stat.lyrics_ms.append((time.time() - t0) * 1000)
        if lyr and lyr.text:
            ok = (
                similarity_strict(s.title, lyr.matched_title) >= 0.9
                if lyr.matched_title
                else True
            )
            row["lyrics"] = f"{lyr.source} «{lyr.matched_title}» {len(lyr.text)}字"
            row["lyrics_ok"] = ok
            if ok:
                stat.lyrics_ok += 1
            else:
                stat.lyrics_wrong += 1
                stat.failures.append(
                    f"[歌词错配] 期望 {s.title}，实得 {lyr.matched_title}/{lyr.matched_artist}"
                )
        else:
            stat.lyrics_none += 1
            row["lyrics"] = "无"
            stat.failures.append(f"[歌词无] {s.title}/{s.artist}")

        t0 = time.time()
        try:
            img, mime, src = aggregator.fetch_album_cover(
                client, s.title, s.artist, s.album or s.title
            )
        except Exception as exc:  # noqa: BLE001
            img, mime, src = None, "", ""
            row["cover_error"] = f"{type(exc).__name__}: {exc}"
        stat.cover_ms.append((time.time() - t0) * 1000)
        if img:
            kb = len(img) // 1024
            row["cover"] = f"{src} {kb}KB {mime}"
            if kb > 1024:
                stat.cover_huge += 1
                stat.failures.append(f"[封面过大] {s.title} {kb}KB")
            else:
                stat.cover_ok += 1
        else:
            stat.cover_none += 1
            row["cover"] = "无"
            stat.failures.append(f"[封面无] {s.title}/{s.artist}")

        details.append(row)
        lyr_flag = "✓" if row.get("lyrics_ok") else ("✗" if row.get("lyrics") == "无" else "~")
        print(
            f"[{i:>2}/{len(samples)}] {s.title[:20]:22s} "
            f"元数据{'✓' if strict else ('~' if loose else '✗')}  "
            f"歌词{lyr_flag}  封面{'✓' if row['cover'] != '无' else '✗'}"
        )
        if not strict:
            print(f"        {note}")

    def pct(n: int) -> str:
        return f"{n}/{stat.total} ({n / stat.total:.0%})"

    def avg(ms: list) -> str:
        return f"{sum(ms) / len(ms) / 1000:.1f}s" if ms else "-"

    print("\n" + "=" * 60)
    print(f"元数据  严格准确 {pct(stat.meta_ok)}   歌名命中 {pct(stat.meta_title_ok)}   "
          f"无结果 {stat.meta_none}   平均 {avg(stat.meta_ms)}")
    print(f"歌词    成功 {pct(stat.lyrics_ok)}   错配 {stat.lyrics_wrong}   无 {stat.lyrics_none}   "
          f"平均 {avg(stat.lyrics_ms)}")
    print(f"封面    成功 {pct(stat.cover_ok)}   无 {stat.cover_none}   过大 {stat.cover_huge}   "
          f"平均 {avg(stat.cover_ms)}")
    print("=" * 60)
    if stat.failures:
        print(f"\n问题清单（{len(stat.failures)} 条）:")
        for f in stat.failures[:40]:
            print("  " + f)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": stat.__dict__ | {"meta_ms": None, "lyrics_ms": None,
                                                   "cover_ms": None, "failures": stat.failures},
                        "details": details}, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        print(f"\n明细已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
