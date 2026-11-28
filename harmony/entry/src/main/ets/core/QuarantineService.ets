// 隔离区管理：读取 Quarantine/<时间戳>/manifest.txt，支持恢复、单条删除与清空。
import fs from '@ohos.file.fs';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Scanner, ScanController } from '../scanner/Scanner';
import { AppLog } from '../utils/AppLog';

export class QuarantineEntry {
  stamp: string = '';
  dir: string = '';
  filePath: string = '';
  fileName: string = '';
  originalPath: string = '';
  verdict: string = '';
  reason: string = '';
  sizeBytes: number = 0;
}

export class QuarantineReport {
  ok: boolean = false;
  message: string = '';
  target: string = '';
}

export class QuarantineService {
  static root(ctx: common.UIAbilityContext): string {
    return `${ctx.filesDir}/Quarantine`;
  }

  static async list(ctx: common.UIAbilityContext): Promise<QuarantineEntry[]> {
    const out: QuarantineEntry[] = [];
    const root = QuarantineService.root(ctx);
    let manifests: string[] = [];
    try {
      const rels = fs.listFileSync(root, { recursion: true, filter: { suffix: ['.txt'] } });
      for (const rel of rels) {
        if (!rel.endsWith('manifest.txt')) {
          continue;
        }
        manifests.push(`${root}/${rel}`);
      }
    } catch (e) {
      return out;
    }
    for (const mf of manifests) {
      const slash = mf.lastIndexOf('/');
      const dir = slash > 0 ? mf.substring(0, slash) : root;
      const stamp = dir.substring(root.length + 1);
      let text = '';
      try {
        text = fs.readTextSync(mf);
      } catch (e) {
        continue;
      }
      for (const line of text.split('\n')) {
        const parts = line.trim().split('\t');
        if (parts.length < 2) {
          continue;
        }
        const e = new QuarantineEntry();
        e.stamp = stamp;
        e.dir = dir;
        e.originalPath = parts[0];
        e.filePath = parts[1];
        e.verdict = parts.length > 2 ? parts[2] : '';
        e.reason = parts.length > 3 ? parts[3] : '';
        const idx = e.filePath.lastIndexOf('/');
        e.fileName = idx >= 0 ? e.filePath.substring(idx + 1) : e.filePath;
        try {
          e.sizeBytes = fs.statSync(e.filePath).size;
        } catch (err) {
          // 文件已不存在：跳过
          continue;
        }
        out.push(e);
      }
    }
    out.sort((a: QuarantineEntry, b: QuarantineEntry) => b.stamp.localeCompare(a.stamp));
    return out;
  }

  private static uniquePath(path: string): string {
    if (!exists(path)) {
      return path;
    }
    const dot = path.lastIndexOf('.');
    const base = dot > 0 ? path.substring(0, dot) : path;
    const ext = dot > 0 ? path.substring(dot) : '';
    let n = 1;
    let out = `${base} (恢复 ${n})${ext}`;
    while (exists(out)) {
      n++;
      out = `${base} (恢复 ${n})${ext}`;
    }
    return out;
  }

  static async restore(ctx: common.UIAbilityContext, e: QuarantineEntry): Promise<QuarantineReport> {
    const rep = new QuarantineReport();
    let target = e.originalPath;
    if (target == '') {
      target = `${ctx.filesDir}/Music/${e.fileName}`;
    }
    const slash = target.lastIndexOf('/');
    const dir = slash > 0 ? target.substring(0, slash) : `${ctx.filesDir}/Music`;
    try {
      fs.mkdirSync(dir, true);
    } catch (err) {
      // 目录已存在
    }
    target = QuarantineService.uniquePath(target);
    try {
      fs.renameSync(e.filePath, target);
    } catch (err) {
      const be = err as BusinessError;
      rep.message = `恢复失败：${be.message}`;
      return rep;
    }
    // 同名 .lrc 侧车一起恢复
    const baseDot = e.fileName.lastIndexOf('.');
    const base = baseDot > 0 ? e.fileName.substring(0, baseDot) : e.fileName;
    const lrc = `${e.dir}/${base}.lrc`;
    if (exists(lrc)) {
      const tDot = target.lastIndexOf('.');
      const targetLrc = `${tDot > 0 ? target.substring(0, tDot) : target}.lrc`;
      try {
        fs.renameSync(lrc, targetLrc);
      } catch (err) {
        // 侧车失败不阻塞
      }
    }
    try {
      await Scanner.scan([dir], new ScanController(), () => {
      });
    } catch (err) {
      // 入库失败时文件已经恢复，提示用户手动扫描
    }
    rep.ok = true;
    rep.target = target;
    AppLog.info(`隔离区恢复：${e.fileName} → ${target}`);
    return rep;
  }

  static deleteOne(e: QuarantineEntry): boolean {
    try {
      fs.unlinkSync(e.filePath);
    } catch (err) {
      return false;
    }
    const baseDot = e.fileName.lastIndexOf('.');
    const base = baseDot > 0 ? e.fileName.substring(0, baseDot) : e.fileName;
    const lrc = `${e.dir}/${base}.lrc`;
    if (exists(lrc)) {
      try {
        fs.unlinkSync(lrc);
      } catch (err) {
        // 忽略
      }
    }
    AppLog.warn(`隔离区删除：${e.fileName}`);
    return true;
  }

  static async empty(ctx: common.UIAbilityContext): Promise<number> {
    const entries = await QuarantineService.list(ctx);
    let removed = 0;
    const dirs = new Set<string>();
    for (const e of entries) {
      if (QuarantineService.deleteOne(e)) {
        removed++;
      }
      dirs.add(e.dir);
    }
    for (const dir of dirs) {
      const mf = `${dir}/manifest.txt`;
      try {
        if (exists(mf)) {
          fs.unlinkSync(mf);
        }
        fs.rmdirSync(dir);
      } catch (err) {
        // 目录非空或已删除
      }
    }
    AppLog.warn(`隔离区清空：移除 ${removed} 个文件`);
    return removed;
  }
}

function exists(path: string): boolean {
  try {
    fs.statSync(path);
    return true;
  } catch (e) {
    return false;
  }
}
