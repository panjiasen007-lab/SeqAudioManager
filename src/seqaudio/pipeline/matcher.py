"""多源聚合与置信度评分（需求 3.2.4 / 3.2.5）。

评分思想
--------
置信度 = 源相关度 × w1 + 标题相似度 × w2 + 歌手相似度 × w3
        + 专辑相似度 × w4 + 时长接近度 × w5

低于阈值（默认 0.62）不自动写入，转入「待人工确认」，避免把冷门翻唱/现场版
错误覆盖成原版信息。同名不同版本（live / remix / acoustic）会额外扣分。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Optional

from ..core.model import MatchCandidate, Track
from ..core.script import to_simplified
from ..net import aggregator, lrclib
from ..net.http import HttpClient

_PUNCT = re.compile(r"[\s\-_·、,，.。!！?？'\"“”‘’()（）\[\]【】~～]+")
_VERSION_HINTS = (
    "live", "remix", "acoustic", "instrumental", "伴奏", "现场", "翻唱", "dj", "demo",
    "演唱会", "演奏会", "音乐会", "karaoke", "ktv", "remastered", "重制",
)


def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = to_simplified(text).lower()
    return _PUNCT.sub("", text)


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    ratio = SequenceMatcher(None, na, nb).ratio()
    # 简繁混用（陈奕迅 / 陳奕迅）时逐字比对会偏低，但同长度中文串几乎总是同一实体，
    # 这里给 0.85 的下限，避免繁体库数据被误判为不匹配
    if len(na) == len(nb) and ratio < 0.85:
        ratio = 0.85
    return ratio


def duration_score(expected: Optional[float], actual: Optional[float]) -> float:
    if not expected or not actual:
        return 0.5  # 未知时给中性分，不奖不罚
    delta = abs(expected - actual)
    if delta <= 2:
        return 1.0
    if delta <= 5:
        return 0.85
    if delta <= 12:
        return 0.6
    if delta <= 30:
        return 0.3
    return 0.0


def version_penalty(title: str, candidate_title: str, candidate_album: str = "") -> float:
    """曲目本身没有版本标记、候选却是现场/翻唱/演唱会版本时扣分。

    专辑名同样参与判断 —— MusicBrainz 里「某某演唱会」「DUO 2010」这类现场专辑
    的曲目名往往与原版完全一致，只看标题会把现场版错判成原版。
    """
    base = normalize(title)
    for hint in _VERSION_HINTS:
        in_title = hint in normalize(candidate_title)
        in_album = hint in normalize(candidate_album)
        if (in_title or in_album) and hint not in base:
            return 0.18
    return 0.0


@dataclass
class ScoreWeights:
    source: float = 0.34
    title: float = 0.28
    artist: float = 0.20
    album: float = 0.10
    duration: float = 0.08


@dataclass
class MatchResult:
    candidate: Optional[MatchCandidate] = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    confidence: float = 0.0
    accepted: bool = False
    lyrics: Optional[lrclib.LyricsResult] = None
    reason: str = ""


class Matcher:
    """把一首 Track 解析成「最佳候选 + 置信度 + 歌词」。"""

    def __init__(
        self,
        client: HttpClient,
        *,
        threshold: float = 0.62,
        use_musicbrainz: bool = True,
        use_coverart: bool = True,
        use_lrclib: bool = True,
        use_itunes: bool = True,
        use_netmusic: bool = True,
        weights: Optional[ScoreWeights] = None,
    ):
        self.client = client
        self.threshold = threshold
        self.use_mb = use_musicbrainz
        self.use_cover = use_coverart
        self.use_lrclib = use_lrclib
        self.use_itunes = use_itunes
        self.use_netmusic = use_netmusic
        self.weights = weights or ScoreWeights()

    # ------------------------------------------------------------ 检索

    def query(self, title: str, artist: str, album: str, duration: Optional[float]) -> list[MatchCandidate]:
        # 多源接力：MusicBrainz → iTunes → 网易云，任何一个命中即停。
        # 每个源失败都静默切换，不会因为单个源失效而让整批曲目都匹配不上。
        return aggregator.search_metadata(
            self.client,
            title,
            artist,
            album,
            use_mb=self.use_mb,
            use_itunes=self.use_itunes,
            use_netmusic=self.use_netmusic,
        )

    def score(self, track: Track, candidate: MatchCandidate, album_hint: str = "") -> float:
        w = self.weights
        probe_title = track.working.title or track.original.title or track.path.stem
        probe_artist = track.working.artist or track.original.artist
        probe_album = track.working.album or track.original.album or album_hint

        s_title = similarity(probe_title, candidate.title)
        s_artist = similarity(probe_artist, candidate.artist) if probe_artist else 0.6
        s_album = similarity(probe_album, candidate.album) if probe_album else 0.6
        s_dur = duration_score(track.info.duration, candidate.duration)
        total = (
            w.source * max(candidate.raw_score, 0.5)
            + w.title * s_title
            + w.artist * s_artist
            + w.album * s_album
            + w.duration * s_dur
        )

        # 目录名推断出的专辑是极强的先验：命中就大幅加分，明确冲突就减分。
        # 权重只有 0.10 的 album 项不足以压过 live/合集的干扰，
        # 这里单独给一个加减项，让「整批属于同一张专辑」这个事实起作用。
        if album_hint:
            hint_sim = similarity(album_hint, candidate.album) if candidate.album else 0.0
            if hint_sim >= 0.80:
                total += 0.14
            elif hint_sim >= 0.55:
                total += 0.05
            elif candidate.album and hint_sim < 0.30:
                total -= 0.06

        total -= version_penalty(probe_title, candidate.title, candidate.album)
        return max(0.0, min(1.0, total))

    def rank(
        self,
        track: Track,
        candidates: Iterable[MatchCandidate],
        album_hint: str = "",
    ) -> list[MatchCandidate]:
        scored = list(candidates)
        for candidate in scored:
            candidate.confidence = self.score(track, candidate, album_hint)
        scored.sort(key=lambda c: c.confidence, reverse=True)
        return scored

    # ------------------------------------------------------------ 主流程

    def match(self, track: Track, *, need_lyrics: bool = True, album_hint: str = "") -> MatchResult:
        title = track.working.title or track.original.title or track.path.stem
        artist = track.working.artist or track.original.artist
        # 检索时只在「文件自带专辑标签」的情况下加 release 限定；
        # 目录推断的专辑只用于打分 —— 用它做检索条件会把其他发行版直接滤掉，
        # 反而可能一首候选都搜不到
        album_for_query = track.working.album or track.original.album

        candidates = self.query(title, artist, album_for_query, track.info.duration)
        candidates = self.rank(track, candidates, album_hint)
        result = MatchResult(candidates=candidates)

        if not candidates:
            result.reason = "联网未找到候选"
        else:
            best = candidates[0]
            result.candidate = best
            result.confidence = best.confidence
            result.accepted = best.confidence >= self.threshold
            if not result.accepted:
                result.reason = f"最高置信度 {best.confidence:.0%} 低于阈值 {self.threshold:.0%}"

        if result.accepted and result.candidate and self.use_cover:
            cand = result.candidate
            if not cand.cover:
                data, mime = aggregator.fetch_cover(
                    self.client, cand, use_coverart=self.use_cover, use_remote=True
                )
                if data:
                    cand.cover, cand.cover_mime = data, mime
                else:
                    # 候选没有可用的封面来源（无 release_id、无 cover_url）时，
                    # 用「专辑名 + 歌手」独立再搜一张兜底，避免单曲封面留空。
                    data, mime, _src = aggregator.fetch_album_cover(
                        self.client,
                        title,
                        artist,
                        album_for_query,
                        use_itunes=self.use_itunes,
                        use_netmusic=self.use_netmusic,
                    )
                    if data:
                        cand.cover, cand.cover_mime = data, mime

        if need_lyrics and self.use_lrclib:
            target = result.candidate if result.accepted else None
            lyr_title = (target.title if target else title)
            lyr_artist = (target.artist if target else artist)
            lyr_album = (target.album if target else album_for_query)
            result.lyrics = aggregator.fetch_lyrics(
                self.client,
                lyr_title,
                lyr_artist,
                lyr_album,
                track.info.duration,
                use_lrclib=self.use_lrclib,
                use_netmusic=self.use_netmusic,
            )
            if result.lyrics is None and artist:
                result.lyrics = lrclib.search_lyrics(self.client, title, "", track.info.duration)
        return result


def candidates_for_display(candidates: list[MatchCandidate], limit: int = 8) -> list[tuple[str, str, float]]:
    """``(来源, 描述, 置信度)``，供 UI 候选列表展示。"""
    return [(c.source, c.label, c.confidence) for c in candidates[:limit]]
