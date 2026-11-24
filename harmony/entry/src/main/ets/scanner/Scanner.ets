import fs from '@ohos.file.fs';
import { AUDIO_EXTS, Track, emptyTrack } from '../model/Track';
import { TagParser, Meta } from './TagParser';
import { Db } from '../db/Db';

export class ScanResult {
  files: number = 0;
  added: number = 0;
  updated: number = 0;
  skipped: number = 0;
  failed: number = 0;
  costMs: number = 0;
}

export class Scanner {
  // 递归收集 roots 下所有音频文件（listFileSync 递归返回以 / 开头的相对路径）
  private static collectFiles(roots: string[]): string[] {
    const out: string[] = [];
    for (const root of roots) {
      let rels: string[] = [];
      try {
        rels = fs.listFileSync(root, { recursion: true, filter: { suffix: AUDIO_EXTS } });
      } catch (e) {
        continue;
      }
      for (const rel of rels) {
        const full = root.endsWith('/') ? root + rel : root + rel;
        out.push(full);
      }
    }
    return out;
  }

  static async scan(roots: string[],
    onProgress: (done: number, total: number, cur: string) => void): Promise<ScanResult> {
    const res = new ScanResult();
    const t0 = Date.now();
    // 1. 收集文件
    const files = Scanner.collectFiles(roots);
    res.files = files.length;
    // 2. 已入库记录（path -> mtime），用于跳过未变化文件
    const known = await Db.pathMtimes();
    // 3. 逐个解析并批量入库
    let batch: Track[] = [];
    for (let i = 0; i < files.length; i++) {
      const p = files[i];
      onProgress(i, files.length, p);
      try {
        const st = fs.statSync(p);
        const mtimeMs = Math.round(st.mtime * 1000);
        const old = known.get(p);
        if (old != undefined && old == mtimeMs) {
          res.skipped++;
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
        batch.push(t);
        if (known.has(p)) {
          res.updated++;
        } else {
          res.added++;
        }
      } catch (e) {
        res.failed++;
      }
      if (batch.length >= 100) {
        await Db.upsertTracks(batch);
        batch = [];
      }
    }
    if (batch.length > 0) {
      await Db.upsertTracks(batch);
    }
    onProgress(files.length, files.length, '');
    res.costMs = Date.now() - t0;
    return res;
  }
}
