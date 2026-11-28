// SACD ISO（Scarlet Book）曲目表解析。
// 只读 Master TOC（LSN 510）、Area TOC、Track List 与 SACDText，
// 用于扫描入库与生成逻辑轨；音频解码由原生 FFmpeg 的 SACD demuxer 完成。
import fs from '@ohos.file.fs';
import util from '@ohos.util';

const SECTOR_SIZE: number = 2048;
const RAW_SECTOR_SIZE: number = 2064;
const RAW_SECTOR_HEADER: number = 12;
const MASTER_TOC_LSN: number = 510;
const MAX_TRACKS: number = 255;
const MAX_TRACK_TEXT_SECTORS: number = 32;
const FRAMES_PER_SECOND: number = 75;

export class SacdTrackInfo {
  no: number = 0;
  title: string = '';
  startMs: number = 0;
  durationMs: number = 0;
}

export class SacdInfo {
  ok: boolean = false;
  error: string = '';
  area: string = '';
  channels: number = 0;
  frameFormat: number = 0;
  totalMs: number = 0;
  album: string = '';
  artist: string = '';
  year: string = '';
  tracks: SacdTrackInfo[] = [];
}

function tagAt(buf: ArrayBuffer, offset: number, text: string): boolean {
  if (offset < 0 || offset + text.length > buf.byteLength) {
    return false;
  }
  const u = new Uint8Array(buf, offset, text.length);
  for (let i = 0; i < text.length; i++) {
    if (u[i] != text.charCodeAt(i)) {
      return false;
    }
  }
  return true;
}

function u16be(buf: ArrayBuffer, offset: number): number {
  if (offset < 0 || offset + 2 > buf.byteLength) {
    return 0;
  }
  return new DataView(buf).getUint16(offset, false);
}

function u32be(buf: ArrayBuffer, offset: number): number {
  if (offset < 0 || offset + 4 > buf.byteLength) {
    return 0;
  }
  return new DataView(buf).getUint32(offset, false);
}

// 75 帧/秒的时间码（分:秒:帧）→ 帧数；非法返回 -1
function timeCode(buf: ArrayBuffer, offset: number): number {
  if (offset < 0 || offset + 3 > buf.byteLength) {
    return -1;
  }
  const u = new Uint8Array(buf, offset, 3);
  if (u[1] >= 60 || u[2] >= FRAMES_PER_SECOND) {
    return -1;
  }
  return (u[0] * 60 + u[1]) * FRAMES_PER_SECOND + u[2];
}

function tryDecode(bytes: Uint8Array, encoding: string): string {
  try {
    const decoder = util.TextDecoder.create(encoding, { fatal: true });
    return decoder.decodeToString(bytes);
  } catch (e) {
    return '';
  }
}

// SACD 文本规范是 ISO-8859-1；中文盘常见 GBK 字节，按桌面版顺序兜底
function decodeText(raw: Uint8Array): string {
  let n = 0;
  while (n < raw.length && raw[n] != 0) {
    n++;
  }
  if (n == 0) {
    return '';
  }
  const bytes = raw.subarray(0, n);
  let text = tryDecode(bytes, 'utf-8');
  if (text == '') {
    text = tryDecode(bytes, 'gb18030');
  }
  if (text == '') {
    text = tryDecode(bytes, 'iso-8859-1');
  }
  if (text == '') {
    let out = '';
    for (let i = 0; i < bytes.length; i++) {
      out += String.fromCharCode(bytes[i]);
    }
    text = out;
  }
  return text.trim();
}

function readAt(fd: number, offset: number, length: number): ArrayBuffer {
  const buf = new ArrayBuffer(length);
  if (offset < 0 || length <= 0) {
    return buf;
  }
  try {
    fs.readSync(fd, buf, { offset: offset, length: length });
  } catch (e) {
    // 越界读取返回空缓冲，由调用方按魔数判断
  }
  return buf;
}

function readSector(fd: number, lsn: number, sectorSize: number, sectorOffset: number): ArrayBuffer {
  return readAt(fd, lsn * sectorSize + sectorOffset, SECTOR_SIZE);
}

// 读取文件头部的一段文本（以 NUL 截止），offset 相对块起点
function textAt(block: ArrayBuffer, offset: number, maxLen: number): string {
  if (offset <= 8 || offset >= block.byteLength) {
    return '';
  }
  const len = Math.min(maxLen, block.byteLength - offset);
  return decodeText(new Uint8Array(block, offset, len));
}

function baseName(path: string): string {
  const slash = path.lastIndexOf('/');
  return slash >= 0 ? path.substring(slash + 1) : path;
}

export class SacdParser {
  private static detectLayout(fd: number): number {
    const plain = readSector(fd, MASTER_TOC_LSN, SECTOR_SIZE, 0);
    if (tagAt(plain, 0, 'SACDMTOC')) {
      return SECTOR_SIZE;
    }
    const raw = readSector(fd, MASTER_TOC_LSN, RAW_SECTOR_SIZE, RAW_SECTOR_HEADER);
    if (tagAt(raw, 0, 'SACDMTOC')) {
      return RAW_SECTOR_SIZE;
    }
    return 0;
  }

  private static readAreaToc(fd: number, lsn: number, sectorSize: number, sectorOffset: number,
    signature: string, areaName: string, info: SacdInfo): boolean {
    const buf = readSector(fd, lsn, sectorSize, sectorOffset);
    if (!tagAt(buf, 0, signature)) {
      return false;
    }
    const tocSize = u16be(buf, 0x0a);
    info.area = areaName;
    info.frameFormat = new Uint8Array(buf, 0x15, 1)[0] & 0x0f;
    info.channels = new Uint8Array(buf, 0x20, 1)[0];
    const nbFrames = timeCode(buf, 0x40);
    const nbTracks = new Uint8Array(buf, 0x45, 1)[0];
    const trackTextPos = u16be(buf, 0x80);
    if (nbFrames <= 0 || nbTracks <= 0) {
      info.error = 'SACD 区域 TOC 无有效曲目';
      return false;
    }

    const tracks = readSector(fd, lsn + 2, sectorSize, sectorOffset);
    if (!tagAt(tracks, 0, 'SACDTRL2')) {
      info.error = '找不到 SACD 曲目表（SACDTRL2）';
      return false;
    }
    const firstFrame = timeCode(tracks, 8);
    if (firstFrame < 0 || firstFrame >= nbFrames) {
      info.error = 'SACD 曲目表起点无效';
      return false;
    }

    // 逐轨标题（SACDTTxt）；没有文本时保持空串，由上层用 Track NN 占位
    let trackText: ArrayBuffer | undefined = undefined;
    if (trackTextPos > 0 && trackTextPos < tocSize) {
      const sectors = Math.min(tocSize - trackTextPos, MAX_TRACK_TEXT_SECTORS);
      const block = new ArrayBuffer(sectors * SECTOR_SIZE);
      const dst = new Uint8Array(block);
      for (let s = 0; s < sectors; s++) {
        const one = readSector(fd, lsn + trackTextPos + s, sectorSize, sectorOffset);
        dst.set(new Uint8Array(one), s * SECTOR_SIZE);
      }
      if (tagAt(block, 0, 'SACDTTxt')) {
        trackText = block;
      }
    }

    const list: SacdTrackInfo[] = [];
    const count = Math.min(nbTracks, MAX_TRACKS);
    for (let i = 0; i < count; i++) {
      const start = timeCode(tracks, 8 + 4 * i);
      const length = timeCode(tracks, 8 + 4 * MAX_TRACKS + 4 * i);
      if (start < firstFrame || length < 0) {
        continue;
      }
      const t = new SacdTrackInfo();
      t.no = list.length + 1;
      t.startMs = Math.round((start - firstFrame) * 1000 / FRAMES_PER_SECOND);
      t.durationMs = Math.round(length * 1000 / FRAMES_PER_SECOND);
      if (trackText != undefined) {
        t.title = SacdParser.trackTextAt(trackText, i);
      }
      list.push(t);
    }
    if (list.length == 0) {
      info.error = 'SACD 曲目表为空';
      return false;
    }
    // 时长缺失（部分盘最后一轨）用下一轨起点或区域总长兜底
    for (let i = 0; i < list.length; i++) {
      if (list[i].durationMs > 0) {
        continue;
      }
      if (i + 1 < list.length) {
        list[i].durationMs = Math.max(0, list[i + 1].startMs - list[i].startMs);
      } else {
        list[i].durationMs = Math.max(0, Math.round((nbFrames - firstFrame) * 1000 / FRAMES_PER_SECOND) -
          list[i].startMs);
      }
    }
    info.tracks = list;
    info.totalMs = Math.round((nbFrames - firstFrame) * 1000 / FRAMES_PER_SECOND);
    return true;
  }

  // 轨道文本块中第 track 项（0 基）的标题；布局与 FFmpeg sacddec.c 一致
  private static trackTextAt(block: ArrayBuffer, track: number): string {
    const size = block.byteLength;
    const pos = u16be(block, 8 + 2 * track);
    if (pos <= 0 || pos + 4 > size) {
      return '';
    }
    const u = new Uint8Array(block);
    const items = u[pos];
    let p = pos + 4;
    for (let i = 0; i < items && p + 2 < size; i++) {
      const type = u[p];
      let len = 0;
      while (p + 2 + len < size && u[p + 2 + len] != 0) {
        len++;
      }
      if (type == 1) {
        return decodeText(u.subarray(p + 2, p + 2 + len));
      }
      p += 2 + len + 1;
      p = (p + 3) & ~3;
    }
    return '';
  }

  static parseFile(path: string): SacdInfo {
    const info = new SacdInfo();
    let fd = -1;
    try {
      fd = fs.openSync(path, fs.OpenMode.READ_ONLY).fd;
    } catch (e) {
      info.error = '无法打开 SACD ISO';
      return info;
    }
    try {
      const sectorSize = SacdParser.detectLayout(fd);
      if (sectorSize == 0) {
        info.error = '不是 SACD ISO（第 510 扇区没有 SACDMTOC）';
        return info;
      }
      const sectorOffset = sectorSize == RAW_SECTOR_SIZE ? RAW_SECTOR_HEADER : 0;
      const master = readSector(fd, MASTER_TOC_LSN, sectorSize, sectorOffset);
      const stereoLsn = u32be(master, 0x40);
      const multiLsn = u32be(master, 0x48);

      let parsed = false;
      if (stereoLsn > 0) {
        parsed = SacdParser.readAreaToc(fd, stereoLsn, sectorSize, sectorOffset,
          'TWOCHTOC', '2 Channel', info);
      }
      if (!parsed && multiLsn > 0) {
        info.tracks = [];
        info.error = '';
        parsed = SacdParser.readAreaToc(fd, multiLsn, sectorSize, sectorOffset,
          'MULCHTOC', 'Multi Channel', info);
      }
      if (!parsed) {
        if (info.error == '') {
          info.error = 'SACD 镜像没有可读的立体声/多声道区';
        }
        return info;
      }

      // 专辑/歌手来自 Master TOC 后一个扇区的 SACDText
      const text = readSector(fd, MASTER_TOC_LSN + 1, sectorSize, sectorOffset);
      if (tagAt(text, 0, 'SACDText')) {
        const album = textAt(text, u16be(text, 0x10), 256);
        const artist = textAt(text, u16be(text, 0x12), 256);
        info.album = album != '' ? album : textAt(text, u16be(text, 0x20), 256);
        info.artist = artist != '' ? artist : textAt(text, u16be(text, 0x22), 256);
      }
      const year = u16be(master, 0x78);
      if (year >= 1900 && year <= 2100) {
        info.year = `${year}`;
      }
      if (info.album == '') {
        info.album = baseName(path).replace(/\.iso$/i, '');
      }
      info.ok = true;
      return info;
    } catch (e) {
      info.ok = false;
      info.error = 'SACD ISO 解析失败';
      return info;
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
}
