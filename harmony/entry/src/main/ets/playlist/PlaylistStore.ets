import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';

export class Playlist {
  name: string = '';
  paths: string[] = [];
  updatedAt: number = 0;
}

// playlists.json 原子读写：坏文件不崩，写失败保留旧文件。
export class PlaylistStore {
  static filePath(ctx: common.UIAbilityContext): string {
    return `${ctx.filesDir}/playlists.json`;
  }

  static load(ctx: common.UIAbilityContext): Playlist[] {
    const path = PlaylistStore.filePath(ctx);
    try {
      const text = fs.readTextSync(path);
      if (text.trim() == '') {
        return [];
      }
      const arr = JSON.parse(text) as Playlist[];
      const out: Playlist[] = [];
      for (const item of arr) {
        if (item.name == undefined || item.name == '') {
          continue;
        }
        const p = new Playlist();
        p.name = item.name;
        p.updatedAt = item.updatedAt ? item.updatedAt : 0;
        if (item.paths != undefined) {
          for (const s of item.paths) {
            if (typeof s == 'string' && s != '') {
              p.paths.push(s);
            }
          }
        }
        out.push(p);
      }
      return out;
    } catch (e) {
      return [];
    }
  }

  private static saveAll(ctx: common.UIAbilityContext, items: Playlist[]): boolean {
    const path = PlaylistStore.filePath(ctx);
    const tmp = `${path}.tmp`;
    try {
      const text = JSON.stringify(items);
      const bytes = new util.TextEncoder().encodeInto(text);
      const f = fs.openSync(tmp, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        fs.writeSync(f.fd, bytes.buffer);
      } finally {
        fs.closeSync(f);
      }
      // 原子替换：先 remove 再 rename（Harmony rename 不覆盖已存在目标）
      try {
        fs.unlinkSync(path);
      } catch (e) {
        // 目标不存在：直接 rename
      }
      fs.renameSync(tmp, path);
      return true;
    } catch (e) {
      const err = e as BusinessError;
      console.error(`PlaylistStore.save fail: ${err.message}`);
      try {
        fs.unlinkSync(tmp);
      } catch (e2) {
        // ignore
      }
      return false;
    }
  }

  static create(ctx: common.UIAbilityContext, name: string, paths: string[]): boolean {
    const clean = name.trim();
    if (clean == '') {
      return false;
    }
    const items = PlaylistStore.load(ctx);
    let target: Playlist | undefined = undefined;
    for (const p of items) {
      if (p.name == clean) {
        target = p;
        break;
      }
    }
    if (target == undefined) {
      target = new Playlist();
      target.name = clean;
      items.push(target);
    }
    target.paths = paths.slice();
    target.updatedAt = Date.now();
    return PlaylistStore.saveAll(ctx, items);
  }

  static remove(ctx: common.UIAbilityContext, name: string): boolean {
    const items = PlaylistStore.load(ctx).filter((p: Playlist) => p.name != name);
    return PlaylistStore.saveAll(ctx, items);
  }

  static rename(ctx: common.UIAbilityContext, oldName: string, newName: string): boolean {
    const clean = newName.trim();
    if (clean == '') {
      return false;
    }
    const items = PlaylistStore.load(ctx);
    let exists = false;
    for (const p of items) {
      if (p.name == clean && p.name != oldName) {
        exists = true;
      }
    }
    if (exists) {
      return false;
    }
    for (const p of items) {
      if (p.name == oldName) {
        p.name = clean;
        p.updatedAt = Date.now();
      }
    }
    return PlaylistStore.saveAll(ctx, items);
  }
}
