// CUE 分轨导出：调用原生 FFmpeg 解码 + FLAC/WAV 编码，逐轨写标签/封面/歌词。
import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import core from 'libseqaudio_core.so';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { TagPatch, TagWriter } from '../tags/TagWriter';
import { Scanner, ScanController } from '../scanner/Scanner';
import { MetadataProviders } from '../net/MetadataProviders';
import { LyricsResult, LyricLine } from '../lyrics/LyricsService';
import { AppLog } from '../utils/AppLog';

export class CueSplitOptions {
  format: string = 'flac';
  bitDepth: number = 16;
  fetchLyrics: boolean = true;
  trimPregap: boolean = false;
}

export class CueSplitReport {
  total: number = 0;
  created: number = 0;
  failed: number = 0;
  trimmed: number = 0;
  outputDir: string = '';
  errors: string[] = [];
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function safeName(text: string): string {
  let out = text.trim();
  for (const ch of ['\\', '/', ':', '*', '?', '"', '<', '>', '|']) {
    out = out.split(ch).join('_');
  }
  return out == '' ? 'Track' : out;
}

function titleOnly(title: string, trackNo: number): string {
  const prefix = `${trackNo}. `;
  return title.startsWith(prefix) ? title.substring(prefix.length) : title;
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
    // 忽略歌词侧车失败
  }
}

function lrcFromLines(lines: LyricLine[]): string {
  const out: string[] = [];
  for (const line of lines) {
    const ms = Math.max(0, line.timeMs);
    out.push(`[${pad2(Math.floor(ms / 60000))}:${pad2(Math.floor((ms % 60000) / 1000))}.` +
      `${pad2(Math.floor((ms % 1000) / 10))}]${line.text}`);
  }
  return out.join('\n');
}

export class CueSplitService {
  // 读取 WAV 的 data 块并计算 RMS（dBFS）；用于判断预间隙里是否藏有真实音频
  private static wavRmsDb(path: string): number {
    let fd = -1;
    try {
      const st = fs.statSync(path);
      if (st.size <= 44) {
        return -120;
      }
      const buf = new ArrayBuffer(st.size);
      fd = fs.openSync(path, fs.OpenMode.READ_ONLY).fd;
      const n = fs.readSync(fd, buf, { offset: 0, length: st.size });
      const bytes = new Uint8Array(buf, 0, n);
      const view = new DataView(buf);
      let pos = 12;
      let dataOff = -1;
      let dataLen = 0;
      while (pos + 8 <= n) {
        const id = String.fromCharCode(bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3]);
        const len = bytes[pos + 4] + bytes[pos + 5] * 256 + bytes[pos + 6] * 65536 +
          bytes[pos + 7] * 16777216;
        if (id == 'data') {
          dataOff = pos + 8;
          dataLen = Math.min(len, n - dataOff);
          break;
        }
        pos += 8 + len + (len % 2);
      }
      if (dataOff < 0 || dataLen < 2) {
        return -120;
      }
      const count = Math.floor(dataLen / 2);
      let sum = 0;
      for (let i = 0; i < count; i++) {
        const v = view.getInt16(dataOff + i * 2, true);
        sum += v * v;
      }
      const rms = Math.sqrt(sum / count);
      return 20 * Math.log(Math.max(rms, 1) / 32768) / Math.LN10;
    } catch (e) {
      // 读取失败时保守视为有音频，避免误裁
      return 0;
    } finally {
      if (fd >= 0) {
        try {
          fs.closeSync(fd);
        } catch (e2) {
          // 已关闭
        }
      }
    }
  }

  // 预间隙 [startMs, endMs) 是否含真实音频：按桌面版在 10%/50%/90% 取样，>-75dBFS 视为有内容
  private static async pregapHasAudio(ctx: common.UIAbilityContext, src: string,
    startMs: number, endMs: number): Promise<boolean> {
    const span = endMs - startMs;
    if (span <= 300) {
      return false;
    }
    const fractions: number[] = [0.1, 0.5, 0.9];
    for (const frac of fractions) {
      const ss = Math.round(startMs + span * frac);
      const take = Math.min(1000, endMs - ss);
      if (take < 200) {
        continue;
      }
      const probe = `${ctx.cacheDir}/cue_pregap_${Date.now()}_${Math.round(ss)}.wav`;
      let code = -1;
      try {
        code = core.exportSegment(src, ss, take, 'wav', 16, probe);
      } catch (e) {
        code = -1;
      }
      if (code != 0 || !fileExists(probe)) {
        return true;
      }
      const db = CueSplitService.wavRmsDb(probe);
      try {
        fs.unlinkSync(probe);
      } catch (e) {
        // 缓存清理失败不阻塞
      }
      if (db > -75.0) {
        return true;
      }
    }
    return false;
  }

  static async split(ctx: common.UIAbilityContext, tracks: Track[], options: CueSplitOptions,
    onProgress: (done: number, total: number, cur: string) => void,
    cancelled: () => boolean): Promise<CueSplitReport> {
    const rep = new CueSplitReport();
    rep.total = tracks.length;
    const album = tracks.length > 0 && tracks[0].album != '' ? tracks[0].album : 'CUE 分轨';
    const dir = `${ctx.filesDir}/Exports/${safeName(album)}`;
    rep.outputDir = dir;
    let dirReady = false;
    try {
      // accessSync 返回 boolean，不存在时不会抛异常
      dirReady = fs.accessSync(dir);
    } catch (e) {
      dirReady = false;
    }
    if (!dirReady) {
      try {
        fs.mkdirSync(dir, true);
        dirReady = true;
      } catch (e) {
        const err = e as BusinessError;
        rep.errors.push(`无法创建输出目录：${dir}（${err.code} ${err.message}）`);
        AppLog.warn(`CUE 输出目录创建失败：${err.code} ${err.message} ${dir}`);
        return rep;
      }
    }
    let done = 0;
    const ordered = tracks.slice();
    ordered.sort((a: Track, b: Track) => {
      const na = a.cueTrackNo > 0 ? a.cueTrackNo : a.trackNo;
      const nb = b.cueTrackNo > 0 ? b.cueTrackNo : b.trackNo;
      return na - nb;
    });
    for (let ti = 0; ti < ordered.length; ti++) {
      const t = ordered[ti];
      if (cancelled()) {
        break;
      }
      done++;
      onProgress(done, ordered.length, t.title);
      const src = t.cuePath != '' ? t.cuePath : t.path;
      if (src == '' || !fileExists(src)) {
        rep.failed++;
        rep.errors.push(`${t.fileName}：找不到源文件`);
        continue;
      }
      const no = t.cueTrackNo > 0 ? t.cueTrackNo : t.trackNo;
      const title = titleOnly(t.title, no);
      const ext = options.format == 'wav' ? '.wav' : '.flac';
      let dst = `${dir}/${pad2(no)} - ${safeName(title)}${ext}`;
      let n = 1;
      while (fileExists(dst)) {
        dst = `${dir}/${pad2(no)} - ${safeName(title)} (${n})${ext}`;
        n++;
      }
      const startMs = t.cueStartMs;
      let endMs = t.cueEndMs > startMs ? t.cueEndMs : startMs + t.durationMs;
      // 预间隙裁剪：本轨在下一轨 INDEX 00 截止；间隙里检测到真实音频则保留
      if (options.trimPregap && ti + 1 < ordered.length) {
        const nxt = ordered[ti + 1];
        if (nxt.cuePath == src && nxt.cuePregapMs > startMs && nxt.cuePregapMs < nxt.cueStartMs) {
          const hasAudio = await CueSplitService.pregapHasAudio(ctx, src,
            nxt.cuePregapMs, nxt.cueStartMs);
          if (hasAudio) {
            AppLog.warn(`CUE 轨 ${no} 的预间隙含音频，已保留不裁剪（` +
              `${((nxt.cueStartMs - nxt.cuePregapMs) / 1000).toFixed(1)}s）`);
          } else {
            endMs = nxt.cuePregapMs;
            rep.trimmed++;
          }
        }
      }
      let durationMs = endMs - startMs;
      if (durationMs <= 0) {
        durationMs = t.durationMs;
      }
      if (durationMs <= 0) {
        rep.failed++;
        rep.errors.push(`${t.fileName}：CUE 时长无效`);
        continue;
      }
      const bitDepth = t.bitDepth >= 24 ? 24 : 16;
      let code = -1;
      try {
        code = core.exportSegment(src, startMs, durationMs, options.format, bitDepth, dst);
      } catch (e) {
        code = -1;
      }
      if (code != 0 || !fileExists(dst)) {
        rep.failed++;
        rep.errors.push(`${t.fileName}：编码失败（${code}）`);
        continue;
      }
      // 写标签
      const patch = new TagPatch();
      patch.title = title;
      patch.artist = t.artist;
      patch.album = t.album;
      patch.genre = t.genre;
      patch.year = t.year;
      patch.trackNo = no;
      const wr = await TagWriter.write(dst, patch);
      if (!wr.ok) {
        rep.errors.push(`${t.fileName}：标签写回失败 ${wr.message}`);
      }
      if (t.coverBlobPath != '' && fileExists(t.coverBlobPath)) {
        try {
          await TagWriter.embedCover(dst, t.coverBlobPath);
        } catch (e) {
          // 封面失败不阻塞
        }
      }
      if (options.fetchLyrics) {
        let lyr: LyricsResult | undefined = undefined;
        if (t.lyrics != '') {
          lyr = new LyricsResult();
          lyr.source = '曲库缓存';
          lyr.plain = t.lyrics;
        } else {
          lyr = await MetadataProviders.fetchLyrics(title, t.artist, t.album,
            durationMs / 1000, true, true, true);
        }
        if (lyr != undefined && lyr.lines.length > 0) {
          const lrc = lrcFromLines(lyr.lines);
          writeText(dst.substring(0, dst.lastIndexOf('.')) + '.lrc', lrc);
        }
      }
      rep.created++;
      await new Promise<void>((resolve: () => void) => {
        setTimeout(resolve, 0);
      });
    }
    // 把导出目录里的新文件扫进曲库，方便立即看到
    if (rep.created > 0) {
      try {
        const ctrl = new ScanController();
        await Scanner.scan([dir], ctrl, () => {
        });
      } catch (e) {
        const err = e as BusinessError;
        rep.errors.push(`导出后入库失败：${err.message}`);
      }
    }
    AppLog.info(`CUE 分轨：成功 ${rep.created}/${rep.total} · 预间隙裁剪 ${rep.trimmed} · ` +
      `输出 ${rep.outputDir}`);
    return rep;
  }
}

function fileExists(path: string): boolean {
  try {
    const st = fs.statSync(path);
    return st.size > 0;
  } catch (e) {
    return false;
  }
}
