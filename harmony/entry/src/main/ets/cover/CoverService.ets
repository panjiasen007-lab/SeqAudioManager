import fs from '@ohos.file.fs';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Track, emptyTrack } from '../model/Track';
import { Db } from '../db/Db';
import { HttpStack } from '../net/HttpStack';
import { TagWriter, WriteResult } from '../tags/TagWriter';
import { PictureBytes, TagParser } from '../scanner/TagParser';

interface ItunesItem {
  artistName: string;
  collectionName: string;
  artworkUrl100: string;
}

interface ItunesResp {
  resultCount: number;
  results: ItunesItem[];
}

interface NeteaseArtist {
  name: string;
}

interface NeteaseAlbum {
  name: string;
  picUrl: string;
  artist: NeteaseArtist;
}

interface NeteaseResult {
  albums: NeteaseAlbum[];
}

interface NeteaseResp {
  result: NeteaseResult;
}

export class CoverCandidate {
  source: string = '';
  title: string = '';
  artist: string = '';
  url: string = '';
  score: number = 0;
}

export class CoverApplyResult {
  ok: boolean = false;
  message: string = '';
  cachePath: string = '';
}

export class CoverExportReport {
  total: number = 0;
  exported: number = 0;
  skipped: number = 0;
  folders: number = 0;
  dir: string = '';
  errors: string[] = [];
}

function pad2n(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function safeFile(text: string): string {
  let out = text.trim();
  for (const ch of ['\\', '/', ':', '*', '?', '"', '<', '>', '|']) {
    out = out.split(ch).join('_');
  }
  out = out.replace(/\s+/g, ' ').trim();
  if (out == '') {
    out = 'cover';
  }
  return out.length > 80 ? out.substring(0, 80) : out;
}

function bigArtwork(url: string): string {
  return url.replace('100x100bb', '600x600bb').replace('100x100', '600x600');
}

function scoreOf(title: string, artist: string, wantAlbum: string, wantArtist: string): number {
  let s = 0;
  const a = title.trim().toLowerCase();
  const b = artist.trim().toLowerCase();
  if (a == wantAlbum.trim().toLowerCase()) s += 60;
  else if (a.includes(wantAlbum.trim().toLowerCase()) || wantAlbum.trim().toLowerCase().includes(a)) s += 30;
  if (b == wantArtist.trim().toLowerCase()) s += 40;
  else if (b.includes(wantArtist.trim().toLowerCase()) || wantArtist.trim().toLowerCase().includes(b)) s += 20;
  return s;
}

export class CoverService {
  static async search(artist: string, album: string): Promise<CoverCandidate[]> {
    const out: CoverCandidate[] = [];
    const q = `${artist} ${album}`.trim();
    if (q == '') {
      return out;
    }
    // iTunes Search API（官方）
    try {
      const url = `https://itunes.apple.com/search?term=${encodeURIComponent(q)}&entity=album&limit=8`;
      const resp = JSON.parse(await HttpStack.getText(url)) as ItunesResp;
      for (const item of resp.results) {
        if (!item.artworkUrl100) {
          continue;
        }
        const c = new CoverCandidate();
        c.source = 'iTunes';
        c.title = item.collectionName ? item.collectionName : '';
        c.artist = item.artistName ? item.artistName : '';
        c.url = bigArtwork(item.artworkUrl100);
        c.score = scoreOf(c.title, c.artist, album, artist) + 10;
        out.push(c);
      }
    } catch (e) {
      // 网络失败：静默降级到下一源
    }
    // 网易云（非官方接口，仅作补充，失败不阻塞）
    try {
      const url = `https://music.163.com/api/search/get/web?s=${encodeURIComponent(q)}&type=10&offset=0&limit=8`;
      const resp = JSON.parse(await HttpStack.getText(url)) as NeteaseResp;
      const albums = resp.result && resp.result.albums ? resp.result.albums : [];
      for (const item of albums) {
        if (!item.picUrl) {
          continue;
        }
        const c = new CoverCandidate();
        c.source = '网易云';
        c.title = item.name ? item.name : '';
        c.artist = item.artist && item.artist.name ? item.artist.name : '';
        c.url = item.picUrl;
        c.score = scoreOf(c.title, c.artist, album, artist);
        out.push(c);
      }
    } catch (e) {
      // ignore
    }
    out.sort((a: CoverCandidate, b: CoverCandidate) => b.score - a.score);
    return out.slice(0, 12);
  }

  static async cacheDir(ctx: common.UIAbilityContext): Promise<string> {
    const dir = `${ctx.filesDir}/Covers`;
    try {
      fs.mkdirSync(dir, true);
    } catch (e) {
      // 目录已存在
    }
    return dir;
  }

  static async apply(ctx: common.UIAbilityContext, track: Track, c: CoverCandidate): Promise<CoverApplyResult> {
    const res = new CoverApplyResult();
    try {
      const bytes = await HttpStack.getBytes(c.url);
      if (bytes.length == 0) {
        res.message = '封面下载为空';
        return res;
      }
      const ext = c.url.toLowerCase().indexOf('.png') >= 0 ? '.png' : '.jpg';
      const path = `${await CoverService.cacheDir(ctx)}/${track.id}${ext}`;
      const f = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        fs.writeSync(f.fd, bytes.buffer, { offset: 0, length: bytes.length });
      } finally {
        fs.closeSync(f);
      }
      const wr: WriteResult = await TagWriter.embedCover(track.path, path);
      res.ok = wr.ok;
      res.message = wr.message;
      if (wr.ok) {
        const updated = emptyTrack();
        updated.id = track.id;
        updated.path = track.path;
        updated.fileName = track.fileName;
        updated.ext = track.ext;
        updated.title = track.title;
        updated.artist = track.artist;
        updated.album = track.album;
        updated.albumartist = track.albumartist;
        updated.genre = track.genre;
        updated.year = track.year;
        updated.trackNo = track.trackNo;
        updated.discNo = track.discNo;
        updated.durationMs = track.durationMs;
        updated.sampleRate = track.sampleRate;
        updated.channels = track.channels;
        updated.bitDepth = track.bitDepth;
        updated.bitrateKbps = track.bitrateKbps;
        // 嵌入封面会改写文件：同步刷新大小/mtime，避免曲库显示旧值
        try {
          const st = fs.statSync(track.path);
          updated.sizeBytes = st.size;
          updated.mtimeMs = Math.round(st.mtime * 1000);
        } catch (e) {
          updated.sizeBytes = track.sizeBytes;
          updated.mtimeMs = track.mtimeMs;
        }
        updated.cuePath = track.cuePath;
        updated.isCueChild = track.isCueChild;
        updated.cueTrackNo = track.cueTrackNo;
        updated.cueStartMs = track.cueStartMs;
        updated.cueEndMs = track.cueEndMs;
        updated.cuePregapMs = track.cuePregapMs;
        updated.replayGain = track.replayGain;
        updated.healthStatus = track.healthStatus;
        updated.healthDetail = track.healthDetail;
        updated.searchBlob = track.searchBlob;
        updated.lyrics = track.lyrics;
        updated.coverBlobPath = path;
        updated.metaSource = c.source;
        updated.metaCheckedAt = Date.now();
        await Db.updateTrack(updated);
        // 同一物理文件的所有记录（含 CUE 子轨）统一刷新大小/mtime/封面
        await Db.updateFileByPath(track.path, updated.sizeBytes, updated.mtimeMs, path);
        res.cachePath = path;
        // 同步内存对象，封面页与详情卡立即显示新封面
        track.coverBlobPath = path;
        track.sizeBytes = updated.sizeBytes;
        track.mtimeMs = updated.mtimeMs;
        track.metaSource = c.source;
        track.metaCheckedAt = updated.metaCheckedAt;
      }
    } catch (e) {
      const err = e as BusinessError;
      res.ok = false;
      res.message = err.message ? err.message : `${e}`;
    }
    return res;
  }

  // 手动指定：把 picker 选中的图片复制到缓存再嵌入
  static async applyLocal(ctx: common.UIAbilityContext, track: Track, uri: string): Promise<CoverApplyResult> {
    const res = new CoverApplyResult();
    try {
      const f = fs.openSync(uri, fs.OpenMode.READ_ONLY);
      const st = fs.statSync(uri);
      const buf = new ArrayBuffer(st.size);
      const n = fs.readSync(f.fd, buf, { offset: 0, length: st.size });
      fs.closeSync(f);
      const ext = uri.toLowerCase().indexOf('.png') >= 0 ? '.png' : '.jpg';
      const path = `${await CoverService.cacheDir(ctx)}/${track.id}_manual${ext}`;
      const dst = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        fs.writeSync(dst.fd, buf, { offset: 0, length: n });
      } finally {
        fs.closeSync(dst);
      }
      const c = new CoverCandidate();
      c.source = '手动';
      c.url = path;
      c.title = track.album;
      c.artist = track.artist;
      const wr = await TagWriter.embedCover(track.path, path);
      res.ok = wr.ok;
      res.message = wr.message;
      if (wr.ok) {
        const updated = emptyTrack();
        updated.id = track.id;
        updated.path = track.path;
        updated.fileName = track.fileName;
        updated.ext = track.ext;
        updated.title = track.title;
        updated.artist = track.artist;
        updated.album = track.album;
        updated.albumartist = track.albumartist;
        updated.genre = track.genre;
        updated.year = track.year;
        updated.trackNo = track.trackNo;
        updated.discNo = track.discNo;
        updated.durationMs = track.durationMs;
        updated.sampleRate = track.sampleRate;
        updated.channels = track.channels;
        updated.bitDepth = track.bitDepth;
        updated.bitrateKbps = track.bitrateKbps;
        // 嵌入封面会改写文件：同步刷新大小/mtime，避免曲库显示旧值
        try {
          const st = fs.statSync(track.path);
          updated.sizeBytes = st.size;
          updated.mtimeMs = Math.round(st.mtime * 1000);
        } catch (e) {
          updated.sizeBytes = track.sizeBytes;
          updated.mtimeMs = track.mtimeMs;
        }
        updated.cuePath = track.cuePath;
        updated.isCueChild = track.isCueChild;
        updated.cueTrackNo = track.cueTrackNo;
        updated.cueStartMs = track.cueStartMs;
        updated.cueEndMs = track.cueEndMs;
        updated.cuePregapMs = track.cuePregapMs;
        updated.replayGain = track.replayGain;
        updated.healthStatus = track.healthStatus;
        updated.healthDetail = track.healthDetail;
        updated.searchBlob = track.searchBlob;
        updated.lyrics = track.lyrics;
        updated.coverBlobPath = path;
        updated.metaSource = '手动';
        updated.metaCheckedAt = Date.now();
        await Db.updateTrack(updated);
        // 同一物理文件的所有记录（含 CUE 子轨）统一刷新大小/mtime/封面
        await Db.updateFileByPath(track.path, updated.sizeBytes, updated.mtimeMs, path);
        res.cachePath = path;
        // 同步内存对象，封面页与详情卡立即显示新封面
        track.coverBlobPath = path;
        track.sizeBytes = updated.sizeBytes;
        track.mtimeMs = updated.mtimeMs;
        track.metaSource = c.source;
        track.metaCheckedAt = updated.metaCheckedAt;
      }
    } catch (e) {
      const err = e as BusinessError;
      res.ok = false;
      res.message = err.message ? err.message : `${e}`;
    }
    return res;
  }

  // 兜底：文件里已经内嵌了封面（比如清空曲库后重扫、换设备重扫），直接从文件抽出来补缓存
  static async ensureCover(ctx: common.UIAbilityContext, track: Track): Promise<string> {
    if (track.ext != '.flac' && track.ext != '.mp3' && track.ext != '.wav') {
      console.info(`ensureCover: skip ext=${track.ext}`);
      return '';
    }
    try {
      const st = fs.statSync(track.path);
      const f = fs.openSync(track.path, fs.OpenMode.READ_ONLY);
      let pic: PictureBytes | undefined = undefined;
      try {
        pic = TagParser.extractCover(f, track.ext, st.size);
      } finally {
        fs.closeSync(f);
      }
      console.info(`ensureCover: track=${track.id} ext=${track.ext} pic=${pic ? pic.bytes.length : -1}`);
      if (pic == undefined || pic.bytes.length == 0) {
        return '';
      }
      const ext = pic.mime.indexOf('png') >= 0 ? '.png' : '.jpg';
      const dir = await CoverService.cacheDir(ctx);
      const path = `${dir}/${track.id}${ext}`;
      const out = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        const ab = new ArrayBuffer(pic.bytes.length);
        new Uint8Array(ab).set(pic.bytes);
        fs.writeSync(out.fd, ab);
      } finally {
        fs.closeSync(out);
      }
      const row = await Db.getTrackById(track.id);
      if (row) {
        row.coverBlobPath = path;
        await Db.updateTrack(row);
      }
      console.info(`ensureCover: ok track=${track.id} path=${path}`);
      return path;
    } catch (e) {
      const err = e as BusinessError;
      console.error(`ensureCover: fail ${err.message}`);
      return '';
    }
  }

  private static copyFile(src: string, dst: string): void {
    fs.copyFileSync(src, dst);
  }

  private static async coverSource(ctx: common.UIAbilityContext, t: Track): Promise<string> {
    if (t.coverBlobPath != '') {
      try {
        fs.statSync(t.coverBlobPath);
        return t.coverBlobPath;
      } catch (e) {
        // 缓存丢失，继续尝试内嵌封面
      }
    }
    return CoverService.ensureCover(ctx, t);
  }

  // 批量导出封面：Exports/Covers/NN - 歌手 - 专辑.jpg
  static async exportCovers(ctx: common.UIAbilityContext,
    tracks: Track[]): Promise<CoverExportReport> {
    const rep = new CoverExportReport();
    rep.total = tracks.length;
    const dir = `${ctx.filesDir}/Exports/Covers`;
    rep.dir = dir;
    try {
      fs.mkdirSync(dir, true);
    } catch (e) {
      // 已存在
    }
    let index = 0;
    for (const t of tracks) {
      if (t.isCueChild == 1) {
        rep.skipped++;
        continue;
      }
      index++;
      const src = await CoverService.coverSource(ctx, t);
      if (src == '') {
        rep.skipped++;
        continue;
      }
      const ext = src.toLowerCase().endsWith('.png') ? '.png' : '.jpg';
      const base = `${pad2n(index)} - ${safeFile(t.artist == '' ? '未知歌手' : t.artist)} - ` +
        `${safeFile(t.album != '' ? t.album : (t.title != '' ? t.title : t.fileName))}`;
      let dst = `${dir}/${base}${ext}`;
      let n = 1;
      while (CoverService.fileExists(dst)) {
        dst = `${dir}/${base} (${n})${ext}`;
        n++;
      }
      try {
        CoverService.copyFile(src, dst);
        rep.exported++;
      } catch (e) {
        const err = e as BusinessError;
        rep.errors.push(`${t.fileName}：${err.message}`);
      }
    }
    return rep;
  }

  // 生成文件夹封面：每个目录写一张 cover.jpg/png，供其他播放器/文件管理器识别
  static async exportFolderCovers(ctx: common.UIAbilityContext,
    tracks: Track[]): Promise<CoverExportReport> {
    const rep = new CoverExportReport();
    rep.total = tracks.length;
    const sources = new Map<string, string>();
    const dirs = new Set<string>();
    const tried = new Set<string>();
    for (const t of tracks) {
      if (t.isCueChild == 1) {
        continue;
      }
      const slash = t.path.lastIndexOf('/');
      if (slash <= 0) {
        continue;
      }
      const dir = t.path.substring(0, slash);
      dirs.add(dir);
      if (sources.has(dir) || tried.has(dir)) {
        continue;
      }
      tried.add(dir);
      const src = await CoverService.coverSource(ctx, t);
      if (src != '') {
        sources.set(dir, src);
      }
    }
    rep.total = dirs.size;
    for (const dir of sources.keys()) {
      const src = sources.get(dir);
      if (src == undefined) {
        continue;
      }
      const ext = src.toLowerCase().endsWith('.png') ? '.png' : '.jpg';
      const dst = `${dir}/cover${ext}`;
      try {
        CoverService.copyFile(src, dst);
        rep.exported++;
        rep.folders++;
      } catch (e) {
        const err = e as BusinessError;
        rep.errors.push(`${dir}：${err.message}`);
      }
    }
    rep.skipped = Math.max(0, dirs.size - rep.folders);
    return rep;
  }

  private static fileExists(path: string): boolean {
    try {
      fs.statSync(path);
      return true;
    } catch (e) {
      return false;
    }
  }
}
