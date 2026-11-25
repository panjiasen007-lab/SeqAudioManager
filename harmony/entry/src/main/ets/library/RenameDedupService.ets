import fs from '@ohos.file.fs';
import { BusinessError } from '@ohos.base';
import { Track, emptyTrack } from '../model/Track';
import { Db } from '../db/Db';
import { PlayerEngine } from '../player/PlayerEngine';

export class RenamePreview {
  id: number = 0;
  oldPath: string = '';
  newPath: string = '';
  oldName: string = '';
  newName: string = '';
  status: string = '待应用';
}

export class RenameReport {
  total: number = 0;
  ok: number = 0;
  failed: number = 0;
  rolledBack: number = 0;
  errors: string[] = [];
}

export class DuplicateGroup {
  key: string = '';
  tracks: Track[] = [];
  keepId: number = 0;
}

class RenameDone {
  preview: RenamePreview = new RenamePreview();
  old: Track = emptyTrack();
}

function sanitize(s: string): string {
  const cleaned = s.replace(/[\\/:*?"<>|\r\n\t]/g, '_').replace(/\s+/g, ' ').trim();
  return cleaned.length > 120 ? cleaned.substring(0, 120) : cleaned;
}

function extOf(path: string): string {
  const dot = path.lastIndexOf('.');
  return dot >= 0 ? path.substring(dot) : '';
}

function dirOf(path: string): string {
  const i = path.lastIndexOf('/');
  return i > 0 ? path.substring(0, i) : '';
}

function norm(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, ' ');
}

function cloneTrack(t: Track): Track {
  const c = emptyTrack();
  c.id = t.id; c.path = t.path; c.fileName = t.fileName; c.ext = t.ext;
  c.title = t.title; c.artist = t.artist; c.album = t.album; c.genre = t.genre; c.year = t.year;
  c.trackNo = t.trackNo; c.durationMs = t.durationMs; c.sampleRate = t.sampleRate;
  c.channels = t.channels; c.bitDepth = t.bitDepth; c.bitrateKbps = t.bitrateKbps;
  c.sizeBytes = t.sizeBytes; c.mtimeMs = t.mtimeMs; c.cuePath = t.cuePath;
  c.isCueChild = t.isCueChild; c.cueTrackNo = t.cueTrackNo; c.cueStartMs = t.cueStartMs;
  c.cueEndMs = t.cueEndMs; c.replayGain = t.replayGain; c.healthStatus = t.healthStatus;
  c.healthDetail = t.healthDetail; c.searchBlob = t.searchBlob; c.lyrics = t.lyrics;
  c.coverBlobPath = t.coverBlobPath; c.metaSource = t.metaSource; c.metaCheckedAt = t.metaCheckedAt;
  return c;
}

export class RenameDedupService {
  // {title} {artist} {album} {year} {track} {ext} {index}
  static buildPreview(tracks: Track[], template: string, cuePaths?: string[]): RenamePreview[] {
    const out: RenamePreview[] = [];
    const used = new Set<string>();
    const cues = new Set<string>();
    if (cuePaths != undefined) {
      for (const c of cuePaths) {
        cues.add(c);
      }
    }
    for (let i = 0; i < tracks.length; i++) {
      const t = tracks[i];
      if (cues.has(t.path)) {
        // CUE 母文件被 .cue 的 FILE 行按名字引用：改名会让子轨全部失联，直接跳过
        const cp = new RenamePreview();
        cp.id = t.id;
        cp.oldPath = t.path;
        cp.newPath = t.path;
        cp.oldName = t.fileName;
        cp.newName = t.fileName;
        cp.status = 'CUE 母文件跳过';
        out.push(cp);
        continue;
      }
      const ext = extOf(t.path);
      const trackStr = t.trackNo > 0 ? (t.trackNo < 10 ? `0${t.trackNo}` : `${t.trackNo}`) : '';
      let name = template
        .replace(/\{title\}/g, t.title)
        .replace(/\{artist\}/g, t.artist)
        .replace(/\{album\}/g, t.album)
        .replace(/\{year\}/g, t.year)
        .replace(/\{track\}/g, trackStr)
        .replace(/\{index\}/g, `${i + 1}`);
      // 变量为空时只留值不留分隔符，否则会生成 "- - 03_sine_48k.wav" 这类残名
      name = name.replace(/\s*-\s*-\s*/g, ' - ').replace(/^[\s-]+/, '').replace(/[\s-]+$/, '');
      name = sanitize(name);
      if (name == '' || name == ext) {
        name = `${t.fileName}${ext}`;
      }
      if (!name.endsWith(ext)) {
        name += ext;
      }
      const dir = dirOf(t.path);
      let candidate = dir.endsWith('/') ? dir + name : `${dir}/${name}`;
      let n = 2;
      while (used.has(candidate.toLowerCase()) || (candidate != t.path && RenameDedupService.fileExists(candidate))) {
        const stem = name.substring(0, name.length - ext.length);
        const next = `${stem} (${n})${ext}`;
        candidate = dir.endsWith('/') ? dir + next : `${dir}/${next}`;
        n++;
        if (n > 999) {
          break;
        }
      }
      used.add(candidate.toLowerCase());
      const p = new RenamePreview();
      p.id = t.id;
      p.oldPath = t.path;
      p.newPath = candidate;
      p.oldName = t.fileName;
      p.newName = candidate.substring(candidate.lastIndexOf('/') + 1);
      p.status = candidate == t.path ? '无需变更' : '待应用';
      out.push(p);
    }
    return out;
  }

  private static fileExists(path: string): boolean {
    try {
      fs.statSync(path);
      return true;
    } catch (e) {
      return false;
    }
  }

  static async applyRenames(items: RenamePreview[]): Promise<RenameReport> {
    const rep = new RenameReport();
    rep.total = items.length;
    const done: RenameDone[] = [];
    const engine = PlayerEngine.get();
    for (const item of items) {
      if (item.newPath == item.oldPath) {
        rep.ok++;
        continue;
      }
      const before = await Db.getTrackById(item.id);
      if (!before) {
        rep.failed++;
        rep.errors.push(`${item.oldName}｜记录不存在`);
        break;
      }
      try {
        fs.renameSync(item.oldPath, item.newPath);
        const updated = cloneTrack(before);
        updated.path = item.newPath;
        updated.fileName = item.newName;
        if (!(await Db.updateTrack(updated))) {
          throw new Error('数据库更新失败');
        }
        for (const q of engine.queue) {
          if (q.path == item.oldPath) {
            q.path = item.newPath;
            q.fileName = item.newName;
          }
        }
        const record = new RenameDone();
        record.preview = item;
        record.old = before;
        done.push(record);
        item.status = '成功';
        rep.ok++;
      } catch (e) {
        const err = e as BusinessError;
        item.status = '失败';
        rep.failed++;
        rep.errors.push(`${item.oldName} → ${item.newName}｜${err.message}`);
        // 失败即回滚：全批恢复，避免半预览状态
        for (let i = done.length - 1; i >= 0; i--) {
          const d = done[i];
          try {
            fs.renameSync(d.preview.newPath, d.preview.oldPath);
            await Db.updateTrack(d.old);
            for (const q of engine.queue) {
              if (q.path == d.preview.newPath) {
                q.path = d.old.path;
                q.fileName = d.old.fileName;
              }
            }
            d.preview.status = '已回滚';
            rep.rolledBack++;
          } catch (e2) {
            const err2 = e2 as BusinessError;
            rep.errors.push(`回滚失败：${d.preview.newName}｜${err2.message}`);
          }
        }
        if (engine.current()?.path == item.oldPath) {
          engine.stop();
        }
        break;
      }
    }
    return rep;
  }

  static async findDuplicates(): Promise<DuplicateGroup[]> {
    const all = await Db.listTracks('');
    const map = new Map<string, DuplicateGroup>();
    for (const t of all) {
      if (t.isCueChild == 1) {
        continue;
      }
      // 标签+规格指纹：歌手/专辑/标题归一化 + 时长（1s 容差）+ 采样率/位深
      const key = `${norm(t.artist)}|${norm(t.album)}|${norm(t.title)}|${Math.round(t.durationMs / 1000)}|${t.sampleRate}|${t.bitDepth}`;
      let g = map.get(key);
      if (!g) {
        g = new DuplicateGroup();
        g.key = key;
        g.keepId = t.id;
        map.set(key, g);
      }
      g.tracks.push(t);
    }
    const groups: DuplicateGroup[] = [];
    map.forEach((g: DuplicateGroup) => {
      if (g.tracks.length > 1) {
        groups.push(g);
      }
    });
    return groups;
  }

  static async markDuplicates(groups: DuplicateGroup[]): Promise<number> {
    let n = 0;
    for (const g of groups) {
      for (const t of g.tracks) {
        if (t.id == g.keepId) {
          continue;
        }
        const updated = cloneTrack(t);
        updated.healthStatus = 'warn';
        updated.healthDetail = `疑似重复：与 #${g.keepId} 标签/规格一致；仅标记不删除`;
        if (await Db.updateTrack(updated)) {
          t.healthStatus = updated.healthStatus;
          t.healthDetail = updated.healthDetail;
          n++;
        }
      }
    }
    return n;
  }

  // 仅移动副本到隔离目录，绝不删除；保留原始曲目不动
  static async quarantineDuplicates(groups: DuplicateGroup[], destDir: string): Promise<number> {
    try {
      fs.mkdirSync(destDir, true);
    } catch (e) {
      // 目录已存在
    }
    let n = 0;
    const engine = PlayerEngine.get();
    for (const g of groups) {
      for (const t of g.tracks) {
        if (t.id == g.keepId) {
          continue;
        }
        const name = t.fileName;
        let dest = destDir.endsWith('/') ? destDir + name : `${destDir}/${name}`;
        let k = 2;
        while (RenameDedupService.fileExists(dest)) {
          const dot = name.lastIndexOf('.');
          const stem = dot > 0 ? name.substring(0, dot) : name;
          const ext = dot > 0 ? name.substring(dot) : '';
          dest = destDir.endsWith('/') ? `${destDir}/${stem} (${k})${ext}` : `${destDir}/${stem} (${k})${ext}`;
          k++;
        }
        try {
          fs.renameSync(t.path, dest);
          const updated = cloneTrack(t);
          updated.path = dest;
          updated.fileName = dest.substring(dest.lastIndexOf('/') + 1);
          updated.healthStatus = 'warn';
          updated.healthDetail = `已隔离：重复项 #${g.keepId}`;
          await Db.updateTrack(updated);
          for (const q of engine.queue) {
            if (q.path == t.path) {
              q.path = dest;
            }
          }
          n++;
        } catch (e) {
          const err = e as BusinessError;
          console.error(`隔离失败 ${t.path}: ${err.message}`);
        }
      }
    }
    return n;
  }
}
