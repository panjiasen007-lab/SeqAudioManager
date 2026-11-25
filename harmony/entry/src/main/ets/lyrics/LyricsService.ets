import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { BusinessError } from '@ohos.base';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { HttpStack } from '../net/HttpStack';

export class LyricLine {
  timeMs: number = 0;
  text: string = '';
}

export class LyricsResult {
  source: string = '';
  lines: LyricLine[] = [];
  plain: string = '';
}

interface LrclibItem {
  trackName: string;
  artistName: string;
  syncedLyrics: string;
  plainLyrics: string;
}

function stampMs(s: string): number {
  const parts = s.split(':');
  if (parts.length < 2) {
    return 0;
  }
  const m = parseInt(parts[0]);
  const sec = parseFloat(parts[1]);
  if (isNaN(m) || isNaN(sec)) {
    return 0;
  }
  return Math.round((m * 60 + sec) * 1000);
}

export class LyricsService {
  static parseLrc(text: string): LyricLine[] {
    const out: LyricLine[] = [];
    const lines = text.replace(/\r/g, '').split('\n');
    for (const raw of lines) {
      const line = raw.trim();
      const m = line.match(/^\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\](.*)$/);
      if (m) {
        const outLine = new LyricLine();
        outLine.timeMs = stampMs(`${m[1]}:${m[2]}`);
        outLine.text = m[3].trim();
        out.push(outLine);
      }
    }
    if (out.length == 0 && text.trim() != '') {
      for (const raw of lines) {
        const l = raw.trim();
        if (l == '' || l.startsWith('[')) {
          continue;
        }
        const item = new LyricLine();
        item.timeMs = 0;
        item.text = l;
        out.push(item);
      }
    }
    out.sort((a: LyricLine, b: LyricLine) => a.timeMs - b.timeMs);
    return out;
  }

  static async findLocal(track: Track): Promise<string> {
    const dot = track.path.lastIndexOf('.');
    const base = dot > 0 ? track.path.substring(0, dot) : track.path;
    const lrcPath = `${base}.lrc`;
    try {
      const st = fs.statSync(lrcPath);
      if (st.size <= 0 || st.size > 1024 * 1024) {
        return '';
      }
      const f = fs.openSync(lrcPath, fs.OpenMode.READ_ONLY);
      try {
        const buf = new ArrayBuffer(st.size);
        const n = fs.readSync(f.fd, buf, { offset: 0, length: st.size });
        return utilDecode(buf, n);
      } finally {
        fs.closeSync(f);
      }
    } catch (e) {
      return '';
    }
  }

  static async searchOnline(track: Track): Promise<LyricsResult> {
    const res = new LyricsResult();
    res.source = 'lrclib';
    const title = encodeURIComponent(track.title);
    // 逐级放宽条件：专辑名对不上时 lrclib 直接返回空数组，不放宽就永远搜不到
    const tries: string[] = [];
    if (track.album != '' && track.artist != '') {
      tries.push(`track_name=${title}&artist_name=${encodeURIComponent(track.artist)}&album_name=${encodeURIComponent(track.album)}`);
    }
    if (track.artist != '') {
      tries.push(`track_name=${title}&artist_name=${encodeURIComponent(track.artist)}`);
    }
    tries.push(`track_name=${title}`);
    for (const q of tries) {
      try {
        const text = await HttpStack.getText(`https://lrclib.net/api/search?${q}`);
        const arr = JSON.parse(text) as LrclibItem[];
        if (arr.length > 0) {
          const item = arr[0];
          const synced = item.syncedLyrics ? item.syncedLyrics : '';
          res.plain = item.plainLyrics ? item.plainLyrics : '';
          res.lines = LyricsService.parseLrc(synced != '' ? synced : res.plain);
          if (res.lines.length > 0) {
            return res;
          }
        }
      } catch (e) {
        const err = e as BusinessError;
        console.error(`LyricsService search fail: ${err.message}`);
      }
    }
    return res;
  }

  static async load(track: Track, forceOnline: boolean): Promise<LyricsResult> {
    if (!forceOnline && track.lyrics != '') {
      const cached = new LyricsResult();
      cached.source = '曲库缓存';
      cached.lines = LyricsService.parseLrc(track.lyrics);
      cached.plain = track.lyrics;
      if (cached.lines.length > 0) {
        return cached;
      }
    }
    const local = await LyricsService.findLocal(track);
    if (local != '' && !forceOnline) {
      const r = new LyricsResult();
      r.source = '本地 LRC';
      r.plain = local;
      r.lines = LyricsService.parseLrc(local);
      await Db.setLyrics(track.id, local);
      return r;
    }
    const online = await LyricsService.searchOnline(track);
    if (online.lines.length > 0) {
      await Db.setLyrics(track.id, online.plain);
    }
    return online;
  }
}

function utilDecode(buf: ArrayBuffer, n: number): string {
  const bytes = new Uint8Array(buf, 0, n);
  // UTF-8 优先，失败回退 latin1
  try {
    const decoder = util.TextDecoder.create('utf-8');
    return decoder.decodeWithStream(bytes);
  } catch (e) {
    let s = '';
    for (const b of bytes) {
      s += String.fromCharCode(b);
    }
    return s;
  }
}
