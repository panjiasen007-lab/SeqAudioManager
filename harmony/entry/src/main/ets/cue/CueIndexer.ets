import fs from '@ohos.file.fs';
import { CUE_EXTS, Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { Db } from '../db/Db';
import { CueParser, CueSheet } from './CueParser';
import { SacdParser } from '../scanner/SacdParser';
import { AppLog } from '../utils/AppLog';

export class CueIndexResult {
  cueFiles: number = 0;
  children: number = 0;
  unmatched: string[] = [];
}

// 外置 CUE → 逻辑轨入库；重建时先清掉旧的逻辑轨，保证可重复执行
export class CueIndexer {
  static async rebuild(roots: string[]): Promise<CueIndexResult> {
    const res = new CueIndexResult();
    await Db.deleteCueChildren();
    const cues: string[] = [];
    for (const root of roots) {
      try {
        const rels = fs.listFileSync(root, { recursion: true, filter: { suffix: CUE_EXTS } });
        for (const rel of rels) {
          cues.push(root.endsWith('/') ? root + rel : `${root}/${rel}`);
        }
      } catch (e) {
        // 单个根目录不可读不影响其他根目录
      }
    }
    const batch: Track[] = [];
    for (const cuePath of cues) {
      res.cueFiles++;
      const sheet: CueSheet = CueParser.parseFile(cuePath);
      for (const file of sheet.files) {
        const name = file.fileName.substring(file.fileName.lastIndexOf('/') + 1).replace(/\\/g, '/');
        const parent = await Db.findTrackByFileName(name);
        if (!parent) {
          if (res.unmatched.length < 20) {
            res.unmatched.push(file.fileName);
          }
          continue;
        }
        if (parent.ext.toLowerCase() == '.iso') {
          // SACD ISO 的曲目表来自 Master TOC，由 indexSacd 单独展开
          continue;
        }
        for (const ct of file.tracks) {
          // 最后一轨 endMs=0：用父文件总时长兜底（父时长是文件绝对时长，不能再减起点）
          const fallbackEnd = parent.durationMs > ct.startMs ? parent.durationMs : ct.startMs;
          const end = ct.endMs > ct.startMs ? ct.endMs : fallbackEnd;
          const t = emptyTrack();
          t.path = `${parent.path}#cue/${name}/${ct.no}`;
          t.fileName = parent.fileName;
          t.ext = parent.ext;
          t.title = ct.title != '' ? `${ct.no}. ${ct.title}` : `${ct.no}. ${parent.title}`;
          t.artist = ct.performer != '' ? ct.performer : parent.artist;
          t.album = sheet.album != '' ? sheet.album : parent.album;
          t.albumartist = parent.albumartist;
          t.genre = parent.genre;
          t.year = parent.year;
          t.trackNo = ct.no;
          t.discNo = parent.discNo;
          t.durationMs = Math.max(0, end - ct.startMs);
          t.sampleRate = parent.sampleRate;
          t.channels = parent.channels;
          t.bitDepth = parent.bitDepth;
          t.bitrateKbps = parent.bitrateKbps;
          t.sizeBytes = parent.sizeBytes;
          t.mtimeMs = parent.mtimeMs;
          // 子轨与母文件共用封面缓存：重新索引时继承，避免封面嵌入后又被清空
          t.coverBlobPath = parent.coverBlobPath;
          t.metaSource = parent.metaSource;
          t.metaCheckedAt = parent.metaCheckedAt;
          t.cuePath = parent.path;
          t.isCueChild = 1;
          t.cueTrackNo = ct.no;
          t.cueStartMs = ct.startMs;
          t.cueEndMs = end;
          t.cuePregapMs = ct.pregapMs;
          t.healthStatus = parent.healthStatus;
          refreshSearchBlob(t);
          batch.push(t);
          res.children++;
        }
      }
    }
    if (batch.length > 0) {
      await Db.upsertTracks(batch);
    }
    await CueIndexer.indexSacd(roots);
    return res;
  }

  // SACD ISO：把 Master TOC 的曲目表展开成逻辑轨，复用 CUE 子轨的播放/导出通路
  private static async indexSacd(roots: string[]): Promise<void> {
    const isos: string[] = [];
    for (const root of roots) {
      try {
        const rels = fs.listFileSync(root, { recursion: true, filter: { suffix: ['.iso'] } });
        for (const rel of rels) {
          const clean = rel.replace(/^\/+/, '');
          isos.push(root.endsWith('/') ? root + clean : `${root}/${clean}`);
        }
      } catch (e) {
        // 单个根目录不可读不影响其他根目录
      }
    }
    const batch: Track[] = [];
    let added = 0;
    for (const iso of isos) {
      const info = SacdParser.parseFile(iso);
      if (!info.ok || info.tracks.length == 0) {
        AppLog.warn(`SACD 曲目表解析失败：${iso}（${info.error}）`);
        continue;
      }
      const parent = await Db.findTrackByPath(iso);
      if (!parent) {
        AppLog.warn(`SACD 未找到父记录：${iso}`);
        continue;
      }
      for (const tr of info.tracks) {
        const t = emptyTrack();
        t.path = `${iso}#sacd/${tr.no}`;
        t.fileName = parent.fileName;
        t.ext = '.iso';
        t.title = tr.title != '' ? `${tr.no}. ${tr.title}` : `${tr.no}. ${parent.title}`;
        t.artist = parent.artist != '' ? parent.artist : info.artist;
        t.album = parent.album != '' ? parent.album : info.album;
        t.albumartist = parent.albumartist != '' ? parent.albumartist : info.artist;
        t.genre = parent.genre;
        t.year = parent.year != '' ? parent.year : info.year;
        t.trackNo = tr.no;
        t.discNo = parent.discNo;
        t.durationMs = tr.durationMs;
        t.sampleRate = parent.sampleRate;
        t.channels = parent.channels;
        t.bitDepth = parent.bitDepth;
        t.bitrateKbps = parent.bitrateKbps;
        t.sizeBytes = parent.sizeBytes;
        t.mtimeMs = parent.mtimeMs;
        t.coverBlobPath = parent.coverBlobPath;
        t.metaSource = parent.metaSource;
        t.metaCheckedAt = parent.metaCheckedAt;
        t.cuePath = iso;
        t.isCueChild = 1;
        t.cueTrackNo = tr.no;
        t.cueStartMs = tr.startMs;
        t.cueEndMs = tr.startMs + tr.durationMs;
        t.healthStatus = parent.healthStatus;
        refreshSearchBlob(t);
        batch.push(t);
        added++;
      }
    }
    if (isos.length > 0) {
      AppLog.info(`SACD 索引：${isos.length} 个 ISO · 生成 ${added} 个逻辑轨`);
    }
    if (batch.length > 0) {
      await Db.upsertTracks(batch);
    }
  }
}
