import fs from '@ohos.file.fs';
import { CUE_EXTS, Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { Db } from '../db/Db';
import { CueParser, CueSheet } from './CueParser';

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
        for (const ct of file.tracks) {
          const end = ct.endMs > ct.startMs ? ct.endMs : Math.max(0, parent.durationMs - ct.startMs);
          const t = emptyTrack();
          t.path = `${parent.path}#cue/${name}/${ct.no}`;
          t.fileName = parent.fileName;
          t.ext = parent.ext;
          t.title = ct.title != '' ? `${ct.no}. ${ct.title}` : `${ct.no}. ${parent.title}`;
          t.artist = ct.performer != '' ? ct.performer : parent.artist;
          t.album = sheet.album != '' ? sheet.album : parent.album;
          t.genre = parent.genre;
          t.year = parent.year;
          t.trackNo = ct.no;
          t.durationMs = Math.max(0, end - ct.startMs);
          t.sampleRate = parent.sampleRate;
          t.channels = parent.channels;
          t.bitDepth = parent.bitDepth;
          t.bitrateKbps = parent.bitrateKbps;
          t.sizeBytes = parent.sizeBytes;
          t.mtimeMs = parent.mtimeMs;
          t.cuePath = parent.path;
          t.isCueChild = 1;
          t.cueTrackNo = ct.no;
          t.cueStartMs = ct.startMs;
          t.cueEndMs = end;
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
    return res;
  }
}
