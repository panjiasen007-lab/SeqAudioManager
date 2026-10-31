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

from ..core import clean as clean_mod
from ..core import cue as cue_mod
from ..core import lyrics as lyrics_mod
from ..core import rename as rename_mod
from ..core import watermark, writer
from ..core.config import Settings
from ..core.model import ConflictMode, LyricsMode, Track, TrackStatus
from ..core.probe import guess_album_from_folder, guess_from_filename
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
            use_itunes=s.use_itunes,
            use_netmusic=s.use_netmusic,
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

        # ---- 阶段一：匹配与整理（只改内存，不落盘）----
        # 必须先全部匹配完再写：同一张专辑的曲目要能互相"投票"统一专辑名与年份，
        # 否则 MusicBrainz 会把它们匹配到演唱会版、精选集、重发版等不同发行版，
        # 一批文件写出十种专辑名。
        for index, track in enumerate(tracks, start=1):
            if self.cancel_event.is_set():
                self._mark_cancelled(track)
                continue
            self.stats.index = index
            try:
                self.prepare_one(track, opts, matcher)
            except Exception as exc:  # noqa: BLE001
                log.exception("处理失败：%s", track.path)
                track.status = TrackStatus.FAILED
                track.note(f"未预期的错误：{exc}")
            self.emit("track", track)
            self.emit("progress", (index, len(tracks)))
            time.sleep(0)

        # ---- 阶段二：专辑一致性收敛 ----
        if not self.cancel_event.is_set():
            self._unify_album_fields(tracks)

        # ---- 阶段三：落盘 ----
        if opts.write_tags and not self.cancel_event.is_set():
            self.emit("phase", "写入标签")
            writable = [t for t in tracks if t.status not in
                        (TrackStatus.UNSUPPORTED, TrackStatus.SKIPPED, TrackStatus.FAILED)]
            for index, track in enumerate(writable, start=1):
                if self.cancel_event.is_set():
                    break
                try:
                    self.finalize_one(track, opts)
                except Exception as exc:  # noqa: BLE001
                    log.exception("写入失败：%s", track.path)
                    track.status = TrackStatus.FAILED
                    track.note(f"写入阶段异常：{exc}")
                self.emit("track", track)
                self.emit("write_progress", (index, len(writable)))
                time.sleep(0)

        # ---- 阶段四：批量重命名 ----
        if opts.do_rename and not self.cancel_event.is_set():
            self._batch_rename(tracks)

        summary = RunSummary.of(tracks, elapsed=self.stats.elapsed)
        self.emit("finish", summary)
        log.info("批处理结束：%s", summary.text())
        return summary

    def _mark_cancelled(self, track: Track) -> None:
        track.note("用户取消")
        if track.status not in (TrackStatus.DONE, TrackStatus.FAILED):
            track.status = TrackStatus.SKIPPED
        self.emit("track", track)

    # ------------------------------------------------------------ 单首：准备

    def prepare_one(
        self,
        track: Track,
        opts: BatchOptions,
        matcher: Optional[Matcher] = None,
    ) -> None:
        """匹配 + 清洗 + 歌词决策，全部只改内存。"""
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

        # ---- 2. 文件名兜底 ----
        # 必须在联网检索之前：DSD/整轨资源常常一个标签都没有，
        # 若不先把「周杰伦 - 02.七里香」拆成歌手+歌名，检索词就变成整个文件名，
        # 相似度会被无谓拉低，甚至什么都搜不到。
        guessed = guess_from_filename(track.path)
        guessed_changed = track.working.merge(guessed, ConflictMode.FILL_EMPTY_ONLY)
        if guessed_changed:
            track.note(f"文件名补全：{', '.join(guessed_changed)}")

        # ---- 3. CUE 整轨信息补充 ----
        if track.cue and s.fill_album_from_cue:
            cue_tags = cue_mod.to_tags_for_album(track.cue)
            changed = track.working.merge(cue_tags, ConflictMode.FILL_EMPTY_ONLY)
            if changed:
                track.note(f"CUE 补充字段：{', '.join(changed)}")

        # ---- 4. 本地歌词优先 ----
        local_lyrics = ""
        if s.use_local_lyrics:
            local_lyrics = (
                track.original.lyrics.strip()
                or (lyrics_mod.read_sidecar(track.path) or "").strip()
            )
            if local_lyrics:
                track.lyrics_src = "本地"

        # ---- 5. 联网匹配 ----
        accepted = False
        result: Optional[MatchResult] = None
        album_hint = self._album_hint(track)
        if matcher is not None:
            track.status = TrackStatus.READING
            result = matcher.match(track, need_lyrics=opts.fetch_lyrics, album_hint=album_hint)
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
                # 待确认时仍用文件名 + 目录名兜底补全空字段
                fallback = self._fallback_tags(track)
                if album_hint and not fallback.album:
                    fallback.album = album_hint
                changed = track.working.merge(fallback, ConflictMode.FILL_EMPTY_ONLY)
                track.changed_fields.extend(c for c in changed if c not in track.changed_fields)
        else:
            fallback = self._fallback_tags(track)
            if album_hint and not fallback.album:
                fallback.album = album_hint
            changed = track.working.merge(fallback, ConflictMode.FILL_EMPTY_ONLY)
            track.changed_fields.extend(c for c in changed if c not in track.changed_fields)

        # ---- 6. 歌词决策 ----
        if opts.fetch_lyrics:
            self._decide_lyrics(track, result, local_lyrics)

        # ---- 7a. 去序号：标题开头的「01. / [01] / 01-」是抓轨软件留下的，
        # 不属于歌名，清掉才能让检索词干净、重命名不带着「02.」这种尾巴
        if s.strip_track_numbers and track.working.title:
            new_title, changed = clean_mod.clean_title(
                track.working.title, strip_number=True
            )
            if changed and new_title != track.working.title:
                track.working.title = new_title
                track.note(f"去序号：{'、'.join(changed)}")
                if "title" not in track.changed_fields:
                    track.changed_fields.append("title")

        # ---- 7b. 标签水印清理 ----
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

        track.elapsed_ms = int((time.perf_counter() - started) * 1000)

    # ------------------------------------------------------------ 单首：落盘

    def finalize_one(self, track: Track, opts: BatchOptions) -> None:
        """写标签 + 歌词 sidecar（在专辑一致性收敛之后执行）。"""
        if not track.exists or track.status is TrackStatus.UNSUPPORTED:
            return
        self._write_lyrics_sidecar(track)
        if opts.write_tags:
            self._write(track, opts)

    @staticmethod
    def _album_hint(track: Track) -> str:
        try:
            return guess_album_from_folder(track.path.parent)
        except Exception:  # noqa: BLE001
            return ""

    def _unify_album_fields(self, tracks: list[Track]) -> None:
        """同一目录下的曲目统一专辑名与年份。

        MusicBrainz 对同一张专辑的不同曲目，可能分别匹配到录音室原版、
        演唱会版、精选集、后续重发版。逐首写入的结果就是「一张专辑十个年份」。
        这里按目录分组做多数投票，把整批收敛到票数最高的组合。
        """
        from collections import Counter, defaultdict

        groups: dict[str, list[Track]] = defaultdict(list)
        for track in tracks:
            if track.status in (TrackStatus.UNSUPPORTED, TrackStatus.SKIPPED, TrackStatus.FAILED):
                continue
            groups[str(track.path.parent).lower()].append(track)

        for group in groups.values():
            if len(group) < 2:
                continue
            votes: Counter = Counter()
            for track in group:
                album = (track.working.album or "").strip()
                if album:
                    votes[(album, (track.working.date or "").strip()[:4])] += 1
            if not votes:
                continue
            (best_album, best_year), hits = votes.most_common(1)[0]
            # 只有形成多数共识才收敛，避免把双 EP 合集硬拧成一个名字
            if hits * 2 < len(group):
                continue

            touched = 0
            for track in group:
                changed = False
                if (track.working.album or "").strip() != best_album:
                    track.working.album = best_album
                    changed = True
                current_year = (track.working.date or "").strip()[:4]
                if best_year and current_year != best_year:
                    track.working.date = best_year
                    changed = True
                if changed:
                    touched += 1
                    if "album" not in track.changed_fields:
                        track.changed_fields.append("album")
                    track.note(f"专辑一致性：统一为《{best_album}》{best_year}")
            if touched:
                log.info("专辑收敛 %s：%d 首统一为《%s》%s", group[0].path.parent.name,
                         touched, best_album, best_year)

    # ------------------------------------------------------------ 子步骤

    def _fallback_tags(self, track: Track):
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
            # 只记录待写内容，真正落盘放到 finalize —— 重命名之后路径才算最终确定
            track.lyrics_sidecar = text

    def _write_lyrics_sidecar(self, track: Track) -> None:
        if not track.lyrics_sidecar:
            return
        mode = self.settings.lyrics_mode_enum()
        if mode not in (LyricsMode.SIDECAR, LyricsMode.BOTH):
            return
        try:
            path = lyrics_mod.write_sidecar(track.path, track.lyrics_sidecar)
            track.note(f"歌词已生成：{path.name}（{track.lyrics_src or '网络'}）")
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
                # 同名 .lrc 必须跟着音频一起改名，否则歌词就“掉队”了
                old_lrc = lyrics_mod.sidecar_path(item.old_path)
                if old_lrc.exists():
                    try:
                        old_lrc.rename(lyrics_mod.sidecar_path(item.new_path))
                    except OSError as exc:
                        item.track.note(f"歌词文件改名失败：{exc}")
                item.track.note(f"已重命名 -> {item.new_path.name}")
            elif not item.ok:
                item.track.note(f"未重命名：{item.reason}")
        self.emit("log", ("INFO", f"重命名完成：成功 {success}，失败 {failed}"))
        for item in plan.items:
            self.emit("track", item.track)
