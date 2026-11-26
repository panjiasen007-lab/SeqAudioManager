// 处理报表：把当前曲库导出为 CSV（UTF-8 BOM），优先走系统保存选择器。
import fs from '@ohos.file.fs';
import util from '@ohos.util';
import picker from '@ohos.file.picker';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Track } from '../model/Track';
import { T } from '../theme/Tokens';

export class ReportResult {
  ok: boolean = false;
  canceled: boolean = false;
  path: string = '';
  message: string = '';
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function stamp(): string {
  const d = new Date();
  return `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}_` +
    `${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
}

function field(v: string): string {
  if (v.indexOf(',') >= 0 || v.indexOf('"') >= 0 || v.indexOf('\n') >= 0) {
    return `"${v.split('"').join('""')}"`;
  }
  return v;
}

function writeText(path: string, text: string): void {
  const bytes = new util.TextEncoder().encodeInto(text);
  const f = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
  try {
    fs.writeSync(f.fd, bytes.buffer);
  } finally {
    fs.closeSync(f);
  }
}

export class ReportService {
  static buildCsv(tracks: Track[]): string {
    const headers: string[] = ['状态', '歌名', '歌手', '专辑', '专辑艺术家', '年份', '轨号', '碟号',
      '格式', '采样率', '位深', '声道', '时长(秒)', '码率(kbps)', '大小(MB)', '判定',
      '判定置信度', '匹配置信度', '来源', '健康', '路径'];
    const lines: string[] = [headers.join(',')];
    for (const t of tracks) {
      const cells: string[] = [
        T.statusLabel(t), t.title, t.artist, t.album, t.albumartist, t.year,
        t.trackNo > 0 ? `${t.trackNo}` : '', t.discNo > 0 ? `${t.discNo}` : '',
        t.ext.replace('.', '').toUpperCase(),
        t.sampleRate > 0 ? `${(t.sampleRate / 1000).toFixed(1)}kHz` : '',
        t.bitDepth > 0 ? `${t.bitDepth}bit` : '', t.channels > 0 ? `${t.channels}ch` : '',
        (t.durationMs / 1000).toFixed(2),
        t.bitrateKbps > 0 ? `${t.bitrateKbps}` : '',
        (t.sizeBytes / 1048576).toFixed(2),
        t.verdict, t.verdictConfidence.toFixed(2), t.matchConfidence.toFixed(2),
        t.metaSource, t.healthStatus, t.path
      ];
      const out: string[] = [];
      for (const c of cells) {
        out.push(field(c));
      }
      lines.push(out.join(','));
    }
    return `\uFEFF${lines.join('\r\n')}`;
  }

  // 通用文本保存：优先系统保存选择器，失败回退沙箱 Reports
  static async saveText(ctx: common.UIAbilityContext, name: string, text: string): Promise<ReportResult> {
    const res = new ReportResult();
    try {
      const dp = new picker.DocumentViewPicker();
      const opts = new picker.DocumentSaveOptions();
      opts.newFileNames = [name];
      const uris = await dp.save(opts);
      if (!uris || uris.length == 0) {
        res.canceled = true;
        return res;
      }
      writeText(uris[0], text);
      res.ok = true;
      res.path = uris[0];
      return res;
    } catch (e) {
      // 选择器不可用：回退到应用沙箱 Reports
    }
    const dir = `${ctx.filesDir}/Reports`;
    const path = `${dir}/${name}`;
    try {
      try {
        fs.mkdirSync(dir, true);
      } catch (e) {
        // 已存在
      }
      writeText(path, text);
      res.ok = true;
      res.path = path;
    } catch (e) {
      const err = e as BusinessError;
      res.message = err.message ? err.message : `${e}`;
    }
    return res;
  }

  static async export(ctx: common.UIAbilityContext, tracks: Track[]): Promise<ReportResult> {
    const res = new ReportResult();
    if (tracks.length == 0) {
      res.message = '曲库为空';
      return res;
    }
    const name = `处理报表_${stamp()}.csv`;
    const text = ReportService.buildCsv(tracks);
    const out = await ReportService.saveText(ctx, name, text);
    return out;
  }
}
