import fs from '@ohos.file.fs';
import { taskpool } from '@kit.ArkTS';
import { common } from '@kit.AbilityKit';
import { AUDIO_EXTS, Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { TagParser, Meta } from './TagParser';
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
  costMs: number = 0;
  issues: string[] = [];
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

// 纯函数：解析一批文件；在 TaskPool 工作线程或主线程均可执行
function parseChunk(files: string[], knownPaths: string[], knownTimes: number[]): ParsedBatch {
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
      const t = emptyTrack();
      t.path = p;
      t.fileName = p.substring(p.lastIndexOf('/') + 1);
      t.ext = ext;
      t.title = meta.title != '' ? meta.title : t.fileName.replace(ext, '');
      t.artist = meta.artist;
      t.album = meta.album;
      t.genre = meta.genre;
      t.year = meta.year;
      t.trackNo = meta.trackNo;
      t.durationMs = meta.durationMs;
      t.sampleRate = meta.sampleRate;
      t.channels = meta.channels;
      t.bitDepth = meta.bitDepth;
      t.bitrateKbps = meta.bitrateKbps;
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
function parseChunkConcurrent(files: string[], knownPaths: string[], knownTimes: number[]): ParsedBatch {
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
      const t = emptyTrack();
      t.path = p;
      t.fileName = p.substring(p.lastIndexOf('/') + 1);
      t.ext = ext;
      t.title = meta.title != '' ? meta.title : t.fileName.replace(ext, '');
      t.artist = meta.artist;
      t.album = meta.album;
      t.genre = meta.genre;
      t.year = meta.year;
      t.trackNo = meta.trackNo;
      t.durationMs = meta.durationMs;
      t.sampleRate = meta.sampleRate;
      t.channels = meta.channels;
      t.bitDepth = meta.bitDepth;
      t.bitrateKbps = meta.bitrateKbps;
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
          tasks.push(taskpool.execute(parseChunkConcurrent, b, knownPaths, knownTimes) as Promise<ParsedBatch>);
        }
        results = await Promise.all(tasks);
      } catch (e) {
        // TaskPool 不可用时主线程兜底，扫描不中断
        results = [];
        for (const b of batches) {
          results.push(parseChunk(b, knownPaths, knownTimes));
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
