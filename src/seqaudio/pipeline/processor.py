"""批处理编排：单首流程 + 批量调度 + 失败隔离（需求 3.6.9 / 4.6）。

单首处理顺序
------------
1. 过滤器（时长/格式）→ 2. CUE 补充 → 3. 本地歌词优先 → 4. 联网匹配
→ 5. 冲突合并 → 6. 标签水印清理 → 7. 安全写入（备份+校验）
→ 8. 歌词落盘（内嵌/同名 lrc）→ 9. 重命名

任一环节抛异常只把当前曲目标记为失败，**绝不中断整批任务**。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..core import cue as cue_mod
from ..core import lyrics as lyrics_mod
from ..core import rename as rename_mod
from ..core import watermark, writer
from ..core.config import Settings
from ..core.model import ConflictMode, LyricsMode, Track, TrackStatus
from ..core.report import RunSummary
from ..net.http import DiskCache, HttpClient
from .matcher import Matcher, MatchResult

log = logging.getLogger(__name__)

EventCallback = Callable[[str, object], None]


@dataclass
class BatchOptions:
    match_online: bool = True
    clean_watermark: bool = True
    write_tags: bool = True
    fetch_lyrics: bool = True
    do_rename: bool = False
    force_write: bool = False
    threshold: float = 0.62


@dataclass
class BatchStats:
    total: int = 0
    index: int = 0
    done: int = 0
    failed: int = 0
    review: int = 0
    skipped: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at


class Processor:
    """批量处理器。纯 Python，可被 GUI 线程或命令行复用。"""

    def __init__(
        self,
        settings: Settings,
        *,
        on_event: Optional[EventCallback] = None,
        client: Optional[HttpClient] = None,
    ):
        self.settings = settings
        self.on_event = on_event or (lambda kind, payload: None)
        self.cancel_event = threading.Event()
        self._client = client
        self.stats = BatchStats()

    # ------------------------------------------------------------ 基础设施

    @property
    def client(self) -> HttpClient:
        if self._client is None:
            self._client = HttpClient(
                proxy=self.settings.http_proxy,
                timeout=self.settings.request_timeout,
                concurrency=self.settings.concurrency,
                cache=DiskCache(ttl_days=self.settings.cache_ttl_days),
                offline=self.settings.offline_mode,
            )
        return self._client

    def matcher(self) -> Matcher:
        s = self.settings
        return Matcher(
            self.client,
            threshold=max(0.3, min(0.95, float(s.match_threshold or 0.62))),
            use_musicbrainz=s.use_musicbrainz,
            use_coverart=s.use_coverart,
            use_lrclib=s.use_lrclib,
        )

    def emit(self, kind: str, payload: object) -> None:
        try:
            self.on_event(kind, payload)
        except Exception:  # pragma: no cover - UI 回调异常不应影响处理
            log.exception("事件回调异常")

    def cancel(self) -> None:
        self.cancel_event.set()

    # ------------------------------------------------------------ 批量

    def run(self, tracks: list[Track], options: Optional[BatchOptions] = None) -> RunSummary:
        opts = options or BatchOptions(
            match_online=self.settings.online_enabled and not self.settings.offline_mode,
            clean_watermark=self.settings.watermark_enabled,
            do_rename=self.settings.rename_enabled,
        )
        self.cancel_event.clear()
        self.stats = BatchStats(total=len(tracks))
        self.emit("start", len(tracks))

        matcher = self.matcher() if opts.match_online else None

        for index, track in enumerate(tracks, start=1):
            if self.cancel_event.is_set():
                track.note("用户取消")
                if track.status not in (TrackStatus.DONE, TrackStatus.FAILED):
                    track.status = TrackStatus.SKIPPED
                self.emit("track", track)
                continue

            self.stats.index = index
            try:
                self.process_one(track, opts, matcher)
            except Exception as exc:  # noqa: BLE001
                log.exception("处理失败：%s", track.path)
                track.status = TrackStatus.FAILED
                track.note(f"未预期的错误：{exc}")

            self.emit("track", track)
            self.emit("progress", (index, len(tracks)))
            time.sleep(0)  # 让出 GIL，保证 UI 线程有机会刷新

        # 批量重命名（统一执行，避免单曲改名后影响后续写入）
        if opts.do_rename and not self.cancel_event.is_set():
            self._batch_rename(tracks)

        summary = RunSummary.of(tracks, elapsed=self.stats.elapsed)
        self.emit("finish", summary)
        log.info("批处理结束：%s", summary.text())
        return summary

    # ------------------------------------------------------------ 单首

    def process_one(
        self,
        track: Track,
        opts: BatchOptions,
        matcher: Optional[Matcher] = None,
    ) -> None:
        started = time.perf_counter()
        s = self.settings

        if not track.exists:
            track.status = TrackStatus.FAILED
            track.note("文件不存在")
            return
        if track.status is TrackStatus.UNSUPPORTED:
            return

        # ---- 1. 过滤器 ----
        if s.min_duration_sec > 0 and track.info.duration:
            if track.info.duration < s.min_duration_sec:
                track.status = TrackStatus.SKIPPED
                track.note(f"时长 {track.info.duration:.0f}s 低于阈值 {s.min_duration_sec:.0f}s")
                return

        track.status = TrackStatus.READING
        track.reset_working()
        track.changed_fields.clear()
        track.notes.clear()

        # ---- 2. CUE 整轨信息补充 ----
        if track.cue and s.fill_album_from_cue:
            cue_tags = cue_mod.to_tags_for_album(track.cue)
            changed = track.working.merge(cue_tags, ConflictMode.FILL_EMPTY_ONLY)
            if changed:
                track.note(f"CUE 补充字段：{', '.join(changed)}")

        # ---- 3. 本地歌词优先 ----
        local_lyrics = ""
        if s.use_local_lyrics:
            local_lyrics = (
                track.original.lyrics.strip()
                or (lyrics_mod.read_sidecar(track.path) or "").strip()
            )
            if local_lyrics:
                track.lyrics_src = "本地"

        # ---- 4. 联网匹配 ----
        accepted = False
        result: Optional[MatchResult] = None
        if matcher is not None:
            track.status = TrackStatus.READING
            result = matcher.match(track, need_lyrics=opts.fetch_lyrics)
            track.confidence = result.confidence
            if result.accepted and result.candidate:
                cand = result.candidate
                track.source = cand.source
                incoming = cand.to_tags()
                changed = track.working.merge(incoming, s.conflict())
                track.changed_fields.extend(c for c in changed if c not in track.changed_fields)
                track.status = TrackStatus.MATCHED
                accepted = True
                track.note(f"匹配：{cand.label}（{cand.confidence:.0%}，{cand.source}）")
                if cand.cover:
                    track.note("已获取封面")
            else:
                track.status = TrackStatus.NEEDS_REVIEW
                if result.candidate:
                    track.note(
                        f"待确认：最高分 {result.confidence:.0%} - {result.candidate.label}"
                    )
                elif result.reason:
                    track.note(result.reason)
                # 待确认时仍用文件名兜底补全空字段
                fallback = self._fallback_tags(track)
                changed = track.working.merge(fallback, ConflictMode.FILL_EMPTY_ONLY)
                track.changed_fields.extend(c for c in changed if c not in track.changed_fields)
        else:
            fallback = self._fallback_tags(track)
            changed = track.working.merge(fallback, ConflictMode.FILL_EMPTY_ONLY)
            track.changed_fields.extend(c for c in changed if c not in track.changed_fields)

        # ---- 5. 歌词决策 ----
        if opts.fetch_lyrics:
            self._decide_lyrics(track, result, local_lyrics)

        # ---- 6. 标签水印清理 ----
        if opts.clean_watermark:
            report = watermark.clean_tags(
                track.working,
                strong_patterns=s.strong_patterns,
                segment_patterns=s.segment_patterns,
                custom_words=s.custom_words,
                clean_title=s.clean_title_watermark,
                clean_albumartist=s.clean_albumartist_watermark,
            )
            for detail in report.details:
                track.note(f"水印清理 {detail}")
            track.changed_fields.extend(
                f for f in report.changed_fields if f not in track.changed_fields
            )

        # ---- 7. 写标签 ----
        if opts.write_tags:
            self._write(track, opts)

        track.elapsed_ms = int((time.perf_counter() - started) * 1000)

    # ------------------------------------------------------------ 子步骤

    def _fallback_tags(self, track: Track):
        from ..core.probe import guess_from_filename

        guessed = guess_from_filename(track.path)
        if track.cue and len(track.cue.tracks) == 1:
            only = track.cue.tracks[0]
            if only.title:
                guessed.title = only.title
            if only.performer:
                guessed.artist = only.performer
        return guessed

    def _decide_lyrics(
        self,
        track: Track,
        result: Optional[MatchResult],
        local_lyrics: str,
    ) -> None:
        s = self.settings
        mode = s.lyrics_mode_enum()
        text = ""
        source = ""

        if local_lyrics and s.use_local_lyrics:
            text, source = local_lyrics, "本地"
        elif result and result.lyrics and result.lyrics.text:
            text, source = result.lyrics.text, result.lyrics.source
            if s.prefer_synced and result.lyrics.synced:
                text = result.lyrics.synced
        elif track.original.lyrics:
            text, source = track.original.lyrics, "原标签"

        if not text:
            return

        if s.clean_lyrics:
            cleaned = lyrics_mod.clean_lyrics(text)
            if cleaned:
                text = cleaned

        track.lyrics_src = source
        if mode in (LyricsMode.EMBED, LyricsMode.BOTH):
            track.working.lyrics = text
            if "lyrics" not in track.changed_fields:
                track.changed_fields.append("lyrics")
        if mode in (LyricsMode.SIDECAR, LyricsMode.BOTH):
            try:
                lyrics_mod.write_sidecar(track.path, text)
                track.note(f"歌词已生成：{lyrics_mod.sidecar_path(track.path).name}（{source}）")
            except OSError as exc:
                track.note(f"歌词文件写入失败：{exc}")

    def _write(self, track: Track, opts: BatchOptions) -> None:
        s = self.settings
        changed = [
            f for f in track.changed_fields
            if track.working.get(f) != track.original.get(f)
        ]
        cover_changed = track.working.cover is not None and track.working.cover != track.original.cover

        if not changed and not cover_changed and not opts.force_write:
            if track.status is TrackStatus.MATCHED:
                track.status = TrackStatus.DONE
                track.note("标签无变化，跳过写入")
            elif track.status is not TrackStatus.NEEDS_REVIEW:
                track.status = TrackStatus.SKIPPED
                track.note("无可用变更")
            return

        track.status = TrackStatus.WRITING
        outcome = writer.safe_write(
            track,
            backup=s.backup_enabled,
            backup_root=s.backup_dir() if s.backup_enabled else None,
            mirror_backup=s.backup_mirror,
            id3_version=s.id3_version,
            compat_mode=s.compat_mode,
            clear_cover=s.clear_cover_when_no_match,
        )
        track.backup_path = outcome.backup_path
        if outcome.ok:
            track.original = track.working.copy()
            track.status = TrackStatus.DONE
            track.note(f"写入成功（{len(outcome.written)} 个字段，{outcome.elapsed_ms}ms）")
        else:
            track.status = TrackStatus.FAILED
            track.note(outcome.message)

    def _batch_rename(self, tracks: list[Track]) -> None:
        s = self.settings
        candidates = [
            t for t in tracks
            if t.status in (TrackStatus.DONE, TrackStatus.MATCHED, TrackStatus.NEEDS_REVIEW)
        ]
        if not candidates:
            return
        plan = rename_mod.build_plan(candidates, s.rename_template)
        success, failed = rename_mod.execute_plan(plan)
        for item in plan.items:
            if item.changed and item.ok:
                item.track.note(f"已重命名 -> {item.new_path.name}")
            elif not item.ok:
                item.track.note(f"未重命名：{item.reason}")
        self.emit("log", ("INFO", f"重命名完成：成功 {success}，失败 {failed}"))
        for item in plan.items:
            self.emit("track", item.track)
