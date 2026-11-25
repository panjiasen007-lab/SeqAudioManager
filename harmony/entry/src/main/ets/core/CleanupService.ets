// 清理假无损：移入应用隔离区（不直接删除），并移除曲库记录。
import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import {
  VERDICT_FAKE, VERDICT_FAKE_DSD, VERDICT_SUSPECT, VERDICT_UPSAMPLED, VERDICT_UPSCALED
} from '../core/AuthenticityService';
import { AppLog } from '../utils/AppLog';

export class CleanupReport {
  total: number = 0;
  moved: number = 0;
  failed: number = 0;
  dir: string = '';
  errors: string[] = [];
}

export class CleanupService {
  static removable(tracks: Track[], includeSuspect: boolean): Track[] {
    const out: Track[] = [];
    for (const t of tracks) {
      if (t.isCueChild == 1) {
        continue;
      }
      if (t.verdict == VERDICT_FAKE || t.verdict == VERDICT_FAKE_DSD) {
        out.push(t);
      } else if (includeSuspect && (t.verdict == VERDICT_SUSPECT ||
        t.verdict == VERDICT_UPSAMPLED || t.verdict == VERDICT_UPSCALED)) {
        out.push(t);
      }
    }
    return out;
  }

  static async quarantine(ctx: common.UIAbilityContext, tracks: Track[],
    onProgress: (done: number, total: number, cur: string) => void): Promise<CleanupReport> {
    const rep = new CleanupReport();
    rep.total = tracks.length;
    const d = new Date();
    const stamp = `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-` +
      `${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
    const dir = `${ctx.filesDir}/Quarantine/${stamp}`;
    rep.dir = dir;
    try {
      fs.mkdirSync(dir, true);
    } catch (e) {
      rep.errors.push(`无法创建隔离目录：${dir}`);
      return rep;
    }
    const manifest: string[] = [];
    let done = 0;
    for (const t of tracks) {
      done++;
      onProgress(done, tracks.length, t.fileName);
      const dot = t.fileName.lastIndexOf('.');
      const base = dot > 0 ? t.fileName.substring(0, dot) : t.fileName;
      const ext = dot > 0 ? t.fileName.substring(dot) : '';
      let dest = `${dir}/${base}${ext}`;
      let n = 1;
      while (exists(dest)) {
        dest = `${dir}/${base} (${n})${ext}`;
        n++;
      }
      try {
        fs.renameSync(t.path, dest);
        rep.moved++;
        manifest.push(`${t.path}\t${dest}\t${t.verdict}\t${t.verdictReason}`);
        // 歌词侧车一并带走
        const lrc = `${t.path.substring(0, t.path.lastIndexOf('.'))}.lrc`;
        if (exists(lrc)) {
          try {
            fs.renameSync(lrc, `${dir}/${base}.lrc`);
          } catch (e2) {
            // 忽略
          }
        }
        await Db.deleteTracksByCuePath(t.path);
        await Db.deleteTrack(t.id);
      } catch (e) {
        const err = e as BusinessError;
        rep.failed++;
        rep.errors.push(`${t.fileName}：${err.message}`);
      }
      await new Promise<void>((resolve: () => void) => {
        setTimeout(resolve, 0);
      });
    }
    try {
      const bytes = new util.TextEncoder().encodeInto(manifest.join('\n'));
      const f = fs.openSync(`${dir}/manifest.txt`, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE);
      try {
        fs.writeSync(f.fd, bytes.buffer);
      } finally {
        fs.closeSync(f);
      }
    } catch (e) {
      // manifest 写失败不影响隔离本身
    }
    AppLog.warn(`清理假无损：隔离 ${rep.moved}/${rep.total} 到 ${rep.dir}`);
    return rep;
  }
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function exists(path: string): boolean {
  try {
    fs.statSync(path);
    return true;
  } catch (e) {
    return false;
  }
}
