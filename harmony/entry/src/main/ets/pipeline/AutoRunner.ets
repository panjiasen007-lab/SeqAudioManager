// 一键全自动：多源匹配 → 标签写回 → 封面 → 歌词；低于阈值转待人工确认。
import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { TagPatch, TagWriter, WriteResult } from '../tags/TagWriter';
import { CoverCandidate, CoverService, CoverApplyResult } from '../cover/CoverService';
import { LyricsResult, LyricLine } from '../lyrics/LyricsService';
import { MetadataProviders } from '../net/MetadataProviders';
import { MatchCandidate, MatchSettings, PendingMatch } from '../net/MetadataTypes';
import { MetadataMatcher } from './Matcher';
import { RenameDedupService, RenamePreview, RenameReport } from '../library/RenameDedupService';
import { AppLog } from '../utils/AppLog';

export class AutoReport {
  total: number = 0;
  matched: number = 0;
  accepted: number = 0;
  written: number = 0;
  covers: number = 0;
  lyrics: number = 0;
  renamed: number = 0;
  pending: PendingMatch[] = [];
  failed: number = 0;
  cancelled: boolean = false;
}

export class AutoOptions {
  settings: MatchSettings = new MatchSettings();
  writeTags: boolean = true;
  fetchCover: boolean = true;
  fetchLyrics: boolean = true;
}

class ApplyItem {
  track: Track;
  cand: MatchCandidate;

  constructor(track: Track, cand: MatchCandidate) {
    this.track = track;
    this.cand = cand;
  }
}

function lrcText(lines: LyricLine[]): string {
  const out: string[] = [];
  for (const line of lines) {
    if (line.timeMs <= 0 && line.text == '') {
      continue;
    }
    const ms = Math.max(0, line.timeMs);
    const m = Math.floor(ms / 60000);
    const s = Math.floor((ms % 60000) / 1000);
    const cs = Math.floor((ms % 1000) / 10);
    const mm = m < 10 ? `0${m}` : `${m}`;
    const ss = s < 10 ? `0${s}` : `${s}`;
    const cc = cs < 10 ? `0${cs}` : `${cs}`;
    out.push(`[${mm}:${ss}.${cc}]${line.text}`);
  }
  return out.join('\n');
}

function writeText(path: string, text: string): void {
  try {
    const bytes = new util.TextEncoder().encodeInto(text);
    const f = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
    try {
      fs.writeSync(f.fd, bytes.buffer);
    } finally {
      fs.closeSync(f);
    }
  } catch (e) {
    // 歌词侧车写失败不影响主流程
  }
}

// 本地歌词优先：内嵌标签 > 同名 .lrc；命中时不再请求在线歌词（对齐桌面版）
function readLocalLyrics(track: Track): string {
  const cached = track.lyrics.trim();
  if (cached != '') {
    return cached;
  }
  const dot = track.path.lastIndexOf('.');
  if (dot <= 0) {
    return '';
  }
  const lrc = `${track.path.substring(0, dot)}.lrc`;
  try {
    const st = fs.statSync(lrc);
    if (st.size <= 0 || st.size > 2 * 1024 * 1024) {
      return '';
    }
    const f = fs.openSync(lrc, fs.OpenMode.READ_ONLY);
    try {
      const ab = new ArrayBuffer(st.size);
      const n = fs.readSync(f.fd, ab, { offset: 0, length: st.size });
      return new util.TextDecoder().decodeWithStream(new Uint8Array(ab, 0, n));
    } finally {
      fs.closeSync(f);
    }
  } catch (e) {
    return '';
  }
}

export class AutoRunner {
  static patchOf(cand: MatchCandidate): TagPatch {
    const p = new TagPatch();
    p.title = cand.title;
    p.artist = cand.artist;
    p.album = cand.album;
    p.albumartist = cand.albumartist != '' ? cand.albumartist : cand.artist;
    p.genre = cand.genre;
    p.year = cand.date != '' ? cand.date.substring(0, 4) : '';
    const trackNo = parseInt(cand.track);
    p.trackNo = isNaN(trackNo) ? 0 : trackNo;
    const discNo = parseInt(cand.disc);
    p.discNo = isNaN(discNo) ? 0 : discNo;
    return p;
  }

  static async applyCandidate(ctx: common.UIAbilityContext, track: Track, cand: MatchCandidate,
    options: AutoOptions): Promise<boolean> {
    const patch = AutoRunner.patchOf(cand);
    let writeOk = true;
    if (options.writeTags) {
      const wr: WriteResult = await TagWriter.write(track.path, patch);
      writeOk = wr.ok;
      if (!wr.ok) {
        AppLog.warn(`标签写回失败：${track.fileName} · ${wr.message}`);
        return false;
      }
    }
    if (patch.title != '') {
      track.title = patch.title;
    }
    if (patch.artist != '') {
      track.artist = patch.artist;
    }
    if (patch.album != '') {
      track.album = patch.album;
    }
    if (patch.albumartist != '') {
      track.albumartist = patch.albumartist;
    }
    if (patch.genre != '') {
      track.genre = patch.genre;
    }
    if (patch.year != '') {
      track.year = patch.year;
    }
    if (patch.trackNo > 0) {
      track.trackNo = patch.trackNo;
    }
    if (patch.discNo > 0) {
      track.discNo = patch.discNo;
    }
    track.metaSource = cand.source;
    track.metaCheckedAt = Date.now();
    track.matchConfidence = cand.confidence;
    // 文件大小/mtime 已被 TagWriter 改写
    try {
      const st = fs.statSync(track.path);
      track.sizeBytes = st.size;
      track.mtimeMs = Math.round(st.mtime * 1000);
    } catch (e) {
      // 保留旧值
    }
    await Db.updateTrack(track);

    if (options.fetchCover && cand.coverUrl != '') {
      const cc = new CoverCandidate();
      cc.source = cand.source;
      cc.title = cand.album != '' ? cand.album : cand.title;
      cc.artist = cand.artist;
      cc.url = cand.coverUrl;
      const cr: CoverApplyResult = await CoverService.apply(ctx, track, cc);
      if (cr.ok) {
        track.coverBlobPath = cr.cachePath;
        await Db.updateTrack(track);
      }
    }
    if (options.fetchLyrics) {
      const local = readLocalLyrics(track);
      if (local != '') {
        if (track.lyrics != local) {
          track.lyrics = local;
          await Db.setLyrics(track.id, local);
          await Db.updateTrack(track);
          AppLog.info(`保留本地歌词：${track.fileName}`);
        }
      } else {
        const lyr = await MetadataProviders.fetchLyrics(
          cand.title, cand.artist, cand.album, track.durationMs / 1000,
          options.settings.useNetmusic, options.settings.useQqmusic, options.settings.useLrclib);
        if (lyr != undefined && lyr.lines.length > 0) {
          const text = lrcText(lyr.lines);
          track.lyrics = text;
          await Db.setLyrics(track.id, text);
          const dot = track.path.lastIndexOf('.');
          const base = dot > 0 ? track.path.substring(0, dot) : track.path;
          writeText(`${base}.lrc`, text);
          await Db.updateTrack(track);
        }
      }
    }
    return writeOk;
  }

  // 同一目录的曲目统一专辑名与年份（桌面版 phase 2）：
  // MusicBrainz 对同一张专辑的不同曲目可能匹配到不同发行版，逐首写入会出现多个年份。
  private static convergeAlbums(items: ApplyItem[]): void {
    const groups = new Map<string, ApplyItem[]>();
    for (const item of items) {
      const slash = item.track.path.lastIndexOf('/');
      const dir = slash > 0 ? item.track.path.substring(0, slash) : '';
      const arr = groups.get(dir);
      if (arr == undefined) {
        groups.set(dir, [item]);
      } else {
        arr.push(item);
      }
    }
    groups.forEach((group: ApplyItem[], dir: string) => {
      if (group.length < 2) {
        return;
      }
      const votes = new Map<string, number>();
      for (const item of group) {
        const album = item.cand.album.trim();
        if (album == '') {
          continue;
        }
        const year = item.cand.date.trim().substring(0, 4);
        const key = `${album}\u0001${year}`;
        const prev = votes.get(key);
        votes.set(key, (prev == undefined ? 0 : prev) + 1);
      }
      let bestKey = '';
      let bestHits = 0;
      votes.forEach((hits: number, key: string) => {
        if (hits > bestHits) {
          bestHits = hits;
          bestKey = key;
        }
      });
      // 必须形成严格多数（平票不收敛），避免把同目录的不同专辑硬拧成一个名字
      if (bestKey == '' || bestHits * 2 <= group.length) {
        return;
      }
      const sep = bestKey.indexOf('\u0001');
      const album = sep > 0 ? bestKey.substring(0, sep) : bestKey;
      const year = sep > 0 ? bestKey.substring(sep + 1) : '';
      let touched = 0;
      for (const item of group) {
        if (item.cand.album != album) {
          item.cand.album = album;
          touched++;
        }
        if (year != '' && item.cand.date.substring(0, 4) != year) {
          item.cand.date = year;
          touched++;
        }
      }
      if (touched > 0) {
        const slash = dir.lastIndexOf('/');
        const name = slash >= 0 ? dir.substring(slash + 1) : dir;
        AppLog.info(`专辑一致性：${name} → 《${album}》${year}（${touched} 处）`);
      }
    });
  }

  static async run(ctx: common.UIAbilityContext, tracks: Track[], options: AutoOptions,
    onProgress: (done: number, total: number, text: string) => void,
    cancelled: () => boolean): Promise<AutoReport> {
    const report = new AutoReport();
    report.total = tracks.length;
    const accepted: ApplyItem[] = [];
    let done = 0;
    // ---- 阶段一：只匹配，不落盘（便于同专辑统一发行版） ----
    for (const track of tracks) {
      if (cancelled()) {
        report.cancelled = true;
        break;
      }
      if (track.isCueChild == 1) {
        AppLog.info(`CUE 子轨跳过自动匹配：${track.fileName}（请用 CUE 分轨导出后处理）`);
        done++;
        onProgress(done, tracks.length, track.fileName);
        continue;
      }
      if (track.ext.toLowerCase() == '.iso') {
        AppLog.warn(`SACD ISO 暂不参与自动匹配：${track.fileName}`);
        done++;
        onProgress(done, tracks.length, track.fileName);
        continue;
      }
      onProgress(done + 1, tracks.length,
        `匹配中：${track.artist == '' ? '未知歌手' : track.artist} - ${track.title}`);
      try {
        const result = await MetadataMatcher.match(track, options.settings);
        if (result.candidate == undefined) {
          report.failed++;
          AppLog.info(`未找到候选：${track.fileName}`);
        } else {
          report.matched++;
          if (!result.accepted) {
            report.pending.push(new PendingMatch(track.id, result.candidate));
            AppLog.info(`待人工确认：${track.fileName} · ${result.reason}`);
          } else {
            accepted.push(new ApplyItem(track, result.candidate));
          }
        }
      } catch (e) {
        const err = e as BusinessError;
        report.failed++;
        AppLog.error(`自动匹配异常：${track.fileName} · ${err.message}`);
      }
      done++;
      onProgress(done, tracks.length, `匹配：${track.fileName}`);
      await new Promise<void>((resolve: () => void) => {
        setTimeout(resolve, 0);
      });
    }
    // ---- 阶段二：专辑一致性收敛 ----
    if (!report.cancelled && accepted.length > 0) {
      AutoRunner.convergeAlbums(accepted);
      // ---- 阶段三：落盘（标签 / 封面 / 歌词）----
      const writtenTracks: Track[] = [];
      for (const item of accepted) {
        if (cancelled()) {
          report.cancelled = true;
          break;
        }
        onProgress(report.total, tracks.length, `写入：${item.track.fileName}`);
        const ok = await AutoRunner.applyCandidate(ctx, item.track, item.cand, options);
        if (ok) {
          report.accepted++;
          report.written++;
          writtenTracks.push(item.track);
          AppLog.info(`自动匹配：${item.track.fileName} → ${item.cand.source} · ` +
            `${item.cand.artist} - ${item.cand.title}（${Math.round(item.cand.confidence * 100)}%）`);
        } else {
          report.failed++;
        }
        await new Promise<void>((resolve: () => void) => {
          setTimeout(resolve, 0);
        });
      }
      // ---- 阶段四：按模板重命名（可选，默认关闭）----
      if (!report.cancelled && options.settings.autoRename && writtenTracks.length > 0) {
        const template = options.settings.renameTemplate.trim() == '' ?
          '{artist} - {title}' : options.settings.renameTemplate;
        // CUE 母文件被 .cue 引用，改名会让子轨失联：收集后跳过
        const cuePaths: string[] = [];
        for (const t of tracks) {
          if (t.isCueChild == 1 && t.cuePath != '') {
            cuePaths.push(t.cuePath);
          }
        }
        const previews: RenamePreview[] = RenameDedupService.buildPreview(writtenTracks, template, cuePaths);
        const todo = previews.filter((p: RenamePreview) => p.status == '待应用' && p.newPath != p.oldPath);
        if (todo.length > 0) {
          onProgress(report.total, tracks.length, `重命名：${todo[0].oldName}`);
          const rr: RenameReport = await RenameDedupService.applyRenames(todo);
          report.renamed = rr.ok;
          AppLog.info(`自动重命名：成功 ${rr.ok}/${todo.length}` +
            (rr.failed > 0 ? ` · 失败 ${rr.failed}` : ''));
          if (rr.errors.length > 0) {
            AppLog.warn(`重命名错误：${rr.errors.slice(0, 3).join('；')}`);
          }
        }
      }
    }
    return report;
  }
}
