import fs from '@ohos.file.fs';
import { BusinessError } from '@ohos.base';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { Meta, TagParser } from '../scanner/TagParser';

export class HealthItem {
  id: number = 0;
  path: string = '';
  fileName: string = '';
  status: string = 'ok';
  detail: string = '';
}

export class HealthReport {
  checked: number = 0;
  ok: number = 0;
  warned: number = 0;
  bad: number = 0;
  items: HealthItem[] = [];
}

export class HealthCheck {
  static async checkAll(onProgress: (done: number, total: number) => void): Promise<HealthReport> {
    const rep = new HealthReport();
    const tracks = await Db.listTracks('');
    const total = tracks.length;
    for (let i = 0; i < total; i++) {
      const t = tracks[i];
      onProgress(i + 1, total);
      if (t.isCueChild == 1) {
        continue;
      }
      const item = new HealthItem();
      item.id = t.id;
      item.path = t.path;
      item.fileName = t.fileName;
      const problems: string[] = [];
      let level = 0; // 0=ok 1=warn 2=bad
      try {
        const st = fs.statSync(t.path);
        if (st.size <= 0) {
          problems.push('文件为空');
          level = 2;
        } else {
          if (t.sizeBytes > 0 && Math.abs(st.size - t.sizeBytes) > 1024) {
            problems.push(`大小变化 ${t.sizeBytes} → ${st.size}`);
            level = Math.max(level, 1);
          }
          const f = fs.openSync(t.path, fs.OpenMode.READ_ONLY);
          let meta = new Meta();
          try {
            meta = TagParser.parse(f, t.ext, st.size);
          } finally {
            fs.closeSync(f);
          }
          if (meta.sampleRate <= 0) {
            problems.push('采样率无法解析，可能损坏或格式不支持');
            level = Math.max(level, 1);
          }
          if (meta.durationMs <= 0) {
            problems.push('时长无法解析，可能截断');
            level = Math.max(level, 1);
          }
          if (t.durationMs > 0 && meta.durationMs > 0 && Math.abs(t.durationMs - meta.durationMs) > 1000) {
            problems.push(`时长不一致 ${t.durationMs}ms → ${meta.durationMs}ms`);
            level = Math.max(level, 1);
          }
          // CBR MP3 体积与时长严重不符视为截断
          if (t.ext == '.mp3' && meta.durationMs > 0 && meta.bitrateKbps > 0) {
            const expected = meta.durationMs * meta.bitrateKbps / 8;
            if (expected > 0 && st.size < expected * 0.5) {
              problems.push('音频数据疑似截断（体积远小于时长所需）');
              level = Math.max(level, 2);
            }
          }
        }
      } catch (e) {
        const err = e as BusinessError;
        problems.push(`读取失败：${err.message}`);
        level = 2;
      }
      item.status = level == 2 ? 'bad' : (level == 1 ? 'warn' : 'ok');
      item.detail = problems.join('；');
      t.healthStatus = item.status;
      t.healthDetail = item.detail;
      await Db.updateTrack(t);
      rep.checked++;
      if (item.status == 'ok') rep.ok++;
      else if (item.status == 'warn') rep.warned++;
      else rep.bad++;
      rep.items.push(item);
    }
    onProgress(total, total);
    return rep;
  }

  static async refreshSummary(): Promise<HealthReport> {
    const rep = new HealthReport();
    const tracks = await Db.listTracks('');
    for (const t of tracks) {
      if (t.isCueChild == 1) {
        continue;
      }
      rep.checked++;
      const s = t.healthStatus || 'ok';
      if (s == 'bad') rep.bad++;
      else if (s == 'warn') rep.warned++;
      else rep.ok++;
    }
    return rep;
  }
}
