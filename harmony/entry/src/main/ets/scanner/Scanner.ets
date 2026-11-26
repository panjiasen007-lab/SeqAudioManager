import fs from '@ohos.file.fs';
import { taskpool } from '@kit.ArkTS';
import { common } from '@kit.AbilityKit';
import { AUDIO_EXTS, Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { TagParser, Meta } from './TagParser';
import { SacdParser, SacdInfo } from './SacdParser';
import { Db } from '../db/Db';

const CONCURRENCY: number = 3;
const CHUNK_SIZE: number = 40;
const DEFAULT_ROOT_NAME: string = 'Music';

export class ScanResult {
  files: number = 0;
  added: number = 0;
  updated: number = 0;
  skipped: number = 0;
  failed: number = 0;
  removed: number = 0;
  costMs: number = 0;
  issues: string[] = [];
}

function fileExists(path: string): boolean {
  try {
    fs.statSync(path);
    return true;
  } catch (e) {
    return false;
  }
}

export class ScanController {
  paused: boolean = false;
  cancelled: boolean = false;

  pause(): void {
    this.paused = true;
  }

  resume(): void {
    this.paused = false;
  }

  cancel(): void {
    this.cancelled = true;
    this.paused = false;
  }
}

interface ParsedBatch {
  tracks: Track[];
  skipped: number;
  failed: string[];
}

// SACD ISO：解析 Master TOC / Area TOC 得到曲目表与专辑信息；
// 音频解码由原生 FFmpeg 的 SACD demuxer 完成。
function fillIsoTrack(t: Track, info: SacdInfo, p: string, size: number, mtimeMs: number): void {
  t.path = p;
  t.fileName = p.substring(p.lastIndexOf('/') + 1);
  t.ext = '.iso';
  t.title = info.album != '' ? info.album : t.fileName.replace(/\.iso$/i, '');
  t.album = info.album;
  t.artist = info.artist;
  t.albumartist = info.artist;
  t.year = info.year;
  t.durationMs = info.totalMs;
  t.sampleRate = 2822400;
  t.bitDepth = 1;
  t.channels = info.channels > 0 ? info.channels : 2;
  t.bitrateKbps = Math.round(t.sampleRate * t.channels / 1000);
  t.sizeBytes = size;
  t.mtimeMs = mtimeMs;
  if (info.ok) {
    const fmt = info.frameFormat == 0 ? 'DST' : 'DSD';
    t.healthDetail = `SACD ISO · ${info.tracks.length} 轨 · ${info.area} · ${fmt}`;
  } else {
    t.healthStatus = 'warn';
    t.healthDetail = `SACD ISO：${info.error}`;
  }
  refreshSearchBlob(t);
}

function makeIsoTrack(p: string, size: number, mtimeMs: number): Track {
  const t = emptyTrack();
  fillIsoTrack(t, SacdParser.parseFile(p), p, size, mtimeMs);
  return t;
}

// 纯函数：解析一批文件；在 TaskPool 工作线程或主线程均可执行
function parseChunk(files: string[], knownPaths: string[], knownTimes: number[], minSec: number): ParsedBatch {
  const out: ParsedBatch = { tracks: [], skipped: 0, failed: [] };
  const known = new Map<string, number>();
  for (let i = 0; i < knownPaths.length && i < knownTimes.length; i++) {
    known.set(knownPaths[i], knownTimes[i]);
  }
  for (const p of files) {
    try {
      const st = fs.statSync(p);
      const mtimeMs = Math.round(st.mtime * 1000);
      const old = known.get(p);
      if (old != undefined && old == mtimeMs) {
        out.skipped++;
        continue;
      }
      const lower = p.toLowerCase();
      let ext = '';
      for (const e of AUDIO_EXTS) {
        if (lower.endsWith(e)) {
          ext = e;
          break;
        }
      }
      if (ext == '.iso') {
        out.tracks.push(makeIsoTrack(p, st.size, mtimeMs));
        continue;
      }
      let meta = new Meta();
      const f = fs.openSync(p, fs.OpenMode.READ_ONLY);
      try {
        meta = TagParser.parse(f, ext, st.size);
      } finally {
        try {
          fs.closeSync(f);
        } catch (e) {
          // ignore
        }
      }
      if (minSec > 0 && meta.durationMs > 0 && meta.durationMs < minSec * 1000) {
        out.skipped++;
        continue;
      }
      const t = emptyTrack();
      t.path = p;
      t.fileName = p.substring(p.lastIndexOf('/') + 1);
      t.ext = ext;
      t.title = meta.title != '' ? meta.title : t.fileName.replace(ext, '');
      t.artist = meta.artist;
      t.album = meta.album;
      t.albumartist = meta.albumartist;
      t.genre = meta.genre;
      t.year = meta.year;
      t.trackNo = meta.trackNo;
      t.discNo = meta.discNo;
      t.durationMs = meta.durationMs;
      t.sampleRate = meta.sampleRate;
      t.channels = meta.channels;
      t.bitDepth = meta.bitDepth;
      t.bitrateKbps = meta.bitrateKbps;
      if (meta.healthHint != '') {
        t.healthStatus = 'warn';
        t.healthDetail = meta.healthHint;
      }
      t.sizeBytes = st.size;
      t.mtimeMs = mtimeMs;
      refreshSearchBlob(t);
      out.tracks.push(t);
    } catch (e) {
      out.failed.push(p);
    }
  }
  return out;
}

@Concurrent
function parseChunkConcurrent(files: string[], knownPaths: string[], knownTimes: number[],
  minSec: number): ParsedBatch {
  const out: ParsedBatch = { tracks: [], skipped: 0, failed: [] };
  const known = new Map<string, number>();
  for (let i = 0; i < knownPaths.length && i < knownTimes.length; i++) {
    known.set(knownPaths[i], knownTimes[i]);
  }
  for (const p of files) {
    try {
      const st = fs.statSync(p);
      const mtimeMs = Math.round(st.mtime * 1000);
      const old = known.get(p);
      if (old != undefined && old == mtimeMs) {
        out.skipped++;
        continue;
      }
      const lower = p.toLowerCase();
      let ext = '';
      for (const e of AUDIO_EXTS) {
        if (lower.endsWith(e)) {
          ext = e;
          break;
        }
      }
      if (ext == '.iso') {
        // @Concurrent 内联：解析 SACD TOC，避免依赖非并发函数
        const info = SacdParser.parseFile(p);
        const iso = emptyTrack();
        iso.path = p;
        iso.fileName = p.substring(p.lastIndexOf('/') + 1);
        iso.ext = '.iso';
        iso.title = info.album != '' ? info.album : iso.fileName.replace(/\.iso$/i, '');
        iso.album = info.album;
        iso.artist = info.artist;
        iso.albumartist = info.artist;
        iso.year = info.year;
        iso.durationMs = info.totalMs;
        iso.sampleRate = 2822400;
        iso.bitDepth = 1;
        iso.channels = info.channels > 0 ? info.channels : 2;
        iso.bitrateKbps = Math.round(iso.sampleRate * iso.channels / 1000);
        if (info.ok) {
          const fmt = info.frameFormat == 0 ? 'DST' : 'DSD';
          iso.healthDetail = `SACD ISO · ${info.tracks.length} 轨 · ${info.area} · ${fmt}`;
        } else {
          iso.healthStatus = 'warn';
          iso.healthDetail = `SACD ISO：${info.error}`;
        }
        iso.sizeBytes = st.size;
        iso.mtimeMs = mtimeMs;
        refreshSearchBlob(iso);
        out.tracks.push(iso);
        continue;
      }
      let meta = new Meta();
      const f = fs.openSync(p, fs.OpenMode.READ_ONLY);
      try {
        meta = TagParser.parse(f, ext, st.size);
      } finally {
        try {
          fs.closeSync(f);
        } catch (e) {
          // ignore
        }
      }
      if (minSec > 0 && meta.durationMs > 0 && meta.durationMs < minSec * 1000) {
        out.skipped++;
        continue;
      }
      const t = emptyTrack();
      t.path = p;
      t.fileName = p.substring(p.lastIndexOf('/') + 1);
      t.ext = ext;
      t.title = meta.title != '' ? meta.title : t.fileName.replace(ext, '');
      t.artist = meta.artist;
      t.album = meta.album;
      t.albumartist = meta.albumartist;
      t.genre = meta.genre;
      t.year = meta.year;
      t.trackNo = meta.trackNo;
      t.discNo = meta.discNo;
      t.durationMs = meta.durationMs;
      t.sampleRate = meta.sampleRate;
      t.channels = meta.channels;
      t.bitDepth = meta.bitDepth;
      t.bitrateKbps = meta.bitrateKbps;
      if (meta.healthHint != '') {
        t.healthStatus = 'warn';
        t.healthDetail = meta.healthHint;
      }
      t.sizeBytes = st.size;
      t.mtimeMs = mtimeMs;
      refreshSearchBlob(t);
      out.tracks.push(t);
    } catch (e) {
      out.failed.push(p);
    }
  }
  return out;
}

async function waitResume(ctrl: ScanController): Promise<boolean> {
  while (ctrl.paused && !ctrl.cancelled) {
    await new Promise<void>((resolve: () => void) => {
      setTimeout(resolve, 100);
    });
  }
  return !ctrl.cancelled;
}

export class ScanRoots {
  static defaultRoot(ctx: common.UIAbilityContext): string {
    return `${ctx.filesDir}/${DEFAULT_ROOT_NAME}`;
  }

  static async load(ctx: common.UIAbilityContext): Promise<string[]> {
    const saved = await Db.getSetting('scan_roots', '');
    if (saved == '') {
      return [ScanRoots.defaultRoot(ctx)];
    }
    try {
      const arr = JSON.parse(saved) as string[];
      if (arr.length > 0) {
        return arr;
      }
    } catch (e) {
      // 配置损坏：回退默认根
    }
    return [ScanRoots.defaultRoot(ctx)];
  }

  static async save(roots: string[]): Promise<void> {
    await Db.setSetting('scan_roots', JSON.stringify(roots));
  }

  static async add(ctx: common.UIAbilityContext, root: string): Promise<string[]> {
    const roots = await ScanRoots.load(ctx);
    const cleaned = root.trim().replace(/\\/g, '/');
    if (cleaned != '' && roots.indexOf(cleaned) < 0) {
      roots.push(cleaned);
      await ScanRoots.save(roots);
    }
    return roots;
  }

  static async remove(ctx: common.UIAbilityContext, root: string, clearTracks: boolean): Promise<string[]> {
    let roots = await ScanRoots.load(ctx);
    roots = roots.filter((r: string) => r != root);
    if (roots.length == 0) {
      roots = [ScanRoots.defaultRoot(ctx)];
    }
    await ScanRoots.save(roots);
    if (clearTracks) {
      await Db.deleteTracksUnderRoot(root);
    }
    return roots;
  }
}

export class Scanner {
  // 扫描最短时长过滤（秒）：0 表示不过滤，由参数页写入
  static minDurationSec: number = 0;

  // 递归收集 roots 下所有音频文件
  private static collectFiles(roots: string[]): string[] {
    const out: string[] = [];
    for (const root of roots) {
      try {
        const base = root.replace(/\/+$/, '');
        const rels = fs.listFileSync(root, { recursion: true, filter: { suffix: AUDIO_EXTS } });
        for (const rel of rels) {
          out.push(`${base}/${rel.replace(/^\/+/, '')}`);
        }
      } catch (e) {
        // 单个根目录不可读不影响其他根目录
      }
    }
    return out;
  }

  static async scan(roots: string[], ctrl: ScanController,
    onProgress: (done: number, total: number, cur: string) => void): Promise<ScanResult> {
    const res = new ScanResult();
    const t0 = Date.now();
    // 清理历史遗留的双斜杠路径（会被本次扫描以规范路径重新入库）
    await Db.purgeBadPaths();
    const files = Scanner.collectFiles(roots);
    res.files = files.length;
    // 清理扫描范围内已被删除的文件记录（CUE 子轨由 rebuildCues 重建，不在此处理）
    try {
      const present = new Set<string>(files);
      const tracked = await Db.listTracks('');
      const stale: number[] = [];
      for (const t of tracked) {
        if (t.isCueChild == 1 || present.has(t.path)) {
          continue;
        }
        let underRoot = false;
        for (const root of roots) {
          const prefix = root.endsWith('/') ? root : `${root}/`;
          if (t.path.startsWith(prefix)) {
            underRoot = true;
            break;
          }
        }
        if (underRoot && !fileExists(t.path)) {
          stale.push(t.id);
        }
      }
      if (stale.length > 0) {
        await Db.deleteTracks(stale);
        res.removed = stale.length;
        console.info(`scan prune: removed ${stale.length} missing tracks`);
      }
    } catch (e) {
      // 清理失败不影响本次扫描
    }
    const known = await Db.pathMtimes();
    const knownPaths: string[] = [];
    const knownTimes: number[] = [];
    known.forEach((v: number, k: string) => {
      knownPaths.push(k);
      knownTimes.push(v);
    });

    let pending: Track[] = [];
    let done = 0;
    for (let i = 0; i < files.length; i += CONCURRENCY * CHUNK_SIZE) {
      if (!(await waitResume(ctrl))) {
        break;
      }
      const batches: string[][] = [];
      for (let j = 0; j < CONCURRENCY; j++) {
        const start = i + j * CHUNK_SIZE;
        if (start >= files.length) {
          break;
        }
        batches.push(files.slice(start, start + CHUNK_SIZE));
      }
      let results: ParsedBatch[] = [];
      try {
        const tasks: Promise<ParsedBatch>[] = [];
        for (const b of batches) {
          tasks.push(taskpool.execute(parseChunkConcurrent, b, knownPaths, knownTimes,
            Scanner.minDurationSec) as Promise<ParsedBatch>);
        }
        results = await Promise.all(tasks);
      } catch (e) {
        // TaskPool 不可用时主线程兜底，扫描不中断
        results = [];
        for (const b of batches) {
          results.push(parseChunk(b, knownPaths, knownTimes, Scanner.minDurationSec));
        }
      }
      for (const r of results) {
        res.skipped += r.skipped;
        res.failed += r.failed.length;
        for (const p of r.failed) {
          if (res.issues.length < 50) {
            res.issues.push(p);
          }
        }
        for (const t of r.tracks) {
          if (known.has(t.path)) {
            res.updated++;
          } else {
            res.added++;
          }
          pending.push(t);
        }
      }
      done = Math.min(files.length, i + CONCURRENCY * CHUNK_SIZE);
      onProgress(done, files.length, done < files.length ? files[done] : '');
      if (pending.length > 0) {
        await Db.upsertTracks(pending);
        pending = [];
      }
    }
    if (pending.length > 0) {
      await Db.upsertTracks(pending);
    }
    onProgress(files.length, files.length, '');
    res.costMs = Date.now() - t0;
    return res;
  }
}
