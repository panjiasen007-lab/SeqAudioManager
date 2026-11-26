import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { BusinessError } from '@ohos.base';
import { Db } from '../db/Db';
import { Meta, TagParser } from '../scanner/TagParser';

const MAX_WRITE_BYTES: number = 500 * 1024 * 1024;
const COPY_BUF_BYTES: number = 1024 * 1024;

export class TagPatch {
  title: string = '';
  artist: string = '';
  album: string = '';
  albumartist: string = '';
  genre: string = '';
  year: string = '';
  trackNo: number = 0;
  discNo: number = 0;
}

export class WriteResult {
  ok: boolean = false;
  message: string = '';
  backupPath: string = '';
}

interface BytePatch {
  pos: number;
  bytes: Uint8Array;
}

class RAF {
  private raf: fs.RandomAccessFile;

  constructor(f: fs.File) {
    this.raf = fs.createRandomAccessFileSync(f);
  }

  read(pos: number, len: number): Uint8Array {
    if (len <= 0) {
      return new Uint8Array(0);
    }
    const buf = new ArrayBuffer(len);
    const n = this.readInto(buf, pos, len);
    return new Uint8Array(buf, 0, n);
  }

  readInto(buf: ArrayBuffer, pos: number, len: number): number {
    if (len <= 0) {
      return 0;
    }
    try {
      this.raf.setFilePointer(pos);
      return this.raf.readSync(buf, { offset: 0, length: len });
    } catch (e) {
      return 0;
    }
  }
}

function concat(parts: Uint8Array[]): Uint8Array {
  let len = 0;
  for (const p of parts) {
    len += p.length;
  }
  const out = new Uint8Array(len);
  let pos = 0;
  for (const p of parts) {
    out.set(p, pos);
    pos += p.length;
  }
  return out;
}

function bytesOf(s: string): Uint8Array {
  const enc = new util.TextEncoder();
  return enc.encode(s);
}

function utf8(a: Uint8Array): string {
  const d = new util.TextDecoder();
  return d.decodeWithStream(a);
}

function ascii(a: Uint8Array, o: number, len: number): string {
  let s = '';
  for (let i = 0; i < len && o + i < a.length; i++) {
    s += String.fromCharCode(a[o + i]);
  }
  return s;
}

function u32be(v: number): Uint8Array {
  const b = new Uint8Array(4);
  b[0] = (v >>> 24) & 0xff;
  b[1] = (v >>> 16) & 0xff;
  b[2] = (v >>> 8) & 0xff;
  b[3] = v & 0xff;
  return b;
}

function u32le(v: number): Uint8Array {
  const b = new Uint8Array(4);
  b[0] = v & 0xff;
  b[1] = (v >>> 8) & 0xff;
  b[2] = (v >>> 16) & 0xff;
  b[3] = (v >>> 24) & 0xff;
  return b;
}

function u64le(v: number): Uint8Array {
  const b = new Uint8Array(8);
  let n = v;
  for (let i = 0; i < 8; i++) {
    b[i] = n & 0xff;
    n = Math.floor(n / 256);
  }
  return b;
}

function readU64le(a: Uint8Array, o: number): number {
  if (o + 8 > a.length) {
    return 0;
  }
  return a[o] + a[o + 1] * 256 + a[o + 2] * 65536 + a[o + 3] * 16777216 +
    a[o + 4] * 4294967296 + a[o + 5] * 1099511627776 + a[o + 6] * 281474976710656 +
    a[o + 7] * 72057594037927936;
}

function syncsafe(v: number): Uint8Array {
  const b = new Uint8Array(4);
  b[0] = (v >>> 21) & 0x7f;
  b[1] = (v >>> 14) & 0x7f;
  b[2] = (v >>> 7) & 0x7f;
  b[3] = v & 0x7f;
  return b;
}

function readSyncsafe(a: Uint8Array, o: number): number {
  return ((a[o] & 0x7f) << 21) | ((a[o + 1] & 0x7f) << 14) | ((a[o + 2] & 0x7f) << 7) | (a[o + 3] & 0x7f);
}

function readBe32(a: Uint8Array, o: number): number {
  return a[o] * 16777216 + (a[o + 1] << 16) + (a[o + 2] << 8) + a[o + 3];
}

function readLe32(a: Uint8Array, o: number): number {
  return a[o] + (a[o + 1] << 8) + (a[o + 2] << 16) + a[o + 3] * 16777216;
}

function writeBytes(fd: number, data: Uint8Array): void {
  if (data.length == 0) {
    return;
  }
  // 注意：必须顺序写（不带 options）。带 {offset:0} 是"定位写"，每次都会落在文件开头，
  // 结果是整个重写文件只剩最后一段（FLAC 会丢掉 fLaC/STREAMINFO，MP3 会丢掉 ID3 头）。
  let written = 0;
  while (written < data.length) {
    const rest = data.length - written;
    const ab = new ArrayBuffer(rest);
    new Uint8Array(ab).set(data.subarray(written));
    const n = fs.writeSync(fd, ab);
    if (n <= 0) {
      throw new Error('写入目标文件失败');
    }
    written += n;
  }
}

function applyPatches(buf: Uint8Array, absPos: number, patches: BytePatch[]): void {
  for (const p of patches) {
    const start = p.pos - absPos;
    if (start >= 0 && start + p.bytes.length <= buf.length) {
      buf.set(p.bytes, start);
    }
  }
}

function copyRange(r: RAF, dstFd: number, from: number, to: number,
  patches: BytePatch[]): void {
  if (to <= from) {
    return;
  }
  const buf = new ArrayBuffer(COPY_BUF_BYTES);
  let pos = from;
  while (pos < to) {
    const len = Math.min(COPY_BUF_BYTES, to - pos);
    const n = r.readInto(buf, pos, len);
    if (n <= 0) {
      break;
    }
    const part = new Uint8Array(buf, 0, n);
    applyPatches(part, pos, patches);
    writeBytes(dstFd, part);
    pos += n;
  }
}

function patchBe32(pos: number, value: number): BytePatch {
  return { pos: pos, bytes: u32be(value) };
}

// ---------------- FLAC ----------------

class FlacBlock {
  headerPos: number = 0;
  type: number = 0;
  dataStart: number = 0;
  dataEnd: number = 0;
  last: boolean = false;
}

function buildVorbisComment(vendor: Uint8Array, desired: Map<string, string>): Uint8Array {
  const entries: string[] = [];
  let p = 0;
  while (p + 4 <= vendor.length) {
    const l = readLe32(vendor, p);
    p += 4;
    if (l < 0 || p + l > vendor.length) {
      break;
    }
    const kv = utf8(vendor.subarray(p, p + l));
    p += l;
    const eq = kv.indexOf('=');
    if (eq <= 0) {
      continue;
    }
    const key = kv.substring(0, eq).toUpperCase();
    const val = desired.has(key) ? desired.get(key)! : kv.substring(eq + 1);
    entries.push(`${key}=${val}`);
  }
  desired.forEach((v: string, k: string) => {
    let found = false;
    for (const e of entries) {
      if (e.startsWith(`${k}=`)) {
        found = true;
        break;
      }
    }
    if (!found) {
      entries.push(`${k}=${v}`);
    }
  });
  const parts: Uint8Array[] = [u32le(vendor.length), vendor, u32le(entries.length)];
  for (const e of entries) {
    const b = bytesOf(e);
    parts.push(u32le(b.length));
    parts.push(b);
  }
  return concat(parts);
}

class RangeEdit {
  start: number = 0;
  end: number = 0;
  bytes: Uint8Array = new Uint8Array(0);
}

// FLAC PICTURE block（type 6）：front cover / MIME / 描述 / 尺寸 / 颜色 / 图像长度 / 图像
function buildPictureBlock(cover: Uint8Array, mime: string, last: boolean): Uint8Array {
  const mimeBytes = bytesOf(mime);
  const data = concat([
    u32be(3), u32be(mimeBytes.length), mimeBytes,
    u32be(0), u32be(0), u32be(0), u32be(0), u32be(0),
    u32be(cover.length), cover
  ]);
  const h = new Uint8Array(4);
  h[0] = (last ? 0x80 : 0) | 0x06;
  h[1] = (data.length >>> 16) & 0xff;
  h[2] = (data.length >>> 8) & 0xff;
  h[3] = data.length & 0xff;
  return concat([h, data]);
}

function writeFlac(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const head = r.read(0, 4);
  if (ascii(head, 0, 4) != 'fLaC') {
    throw new Error('FLAC 头校验失败');
  }
  const blocks: FlacBlock[] = [];
  let pos = 4;
  while (pos + 4 <= size) {
    const h = r.read(pos, 4);
    if (h.length < 4) {
      break;
    }
    const last = (h[0] & 0x80) != 0;
    const type = h[0] & 0x7f;
    const len = (h[1] << 16) | (h[2] << 8) | h[3];
    const dataStart = pos + 4;
    if (dataStart + len > size) {
      break;
    }
    const block = new FlacBlock();
    block.headerPos = pos;
    block.type = type;
    block.dataStart = dataStart;
    block.dataEnd = dataStart + len;
    block.last = last;
    blocks.push(block);
    pos = dataStart + len;
    if (last) {
      break;
    }
  }
  const desired = new Map<string, string>();
  if (p.title != '') desired.set('TITLE', p.title);
  if (p.artist != '') desired.set('ARTIST', p.artist);
  if (p.album != '') desired.set('ALBUM', p.album);
  if (p.albumartist != '') desired.set('ALBUMARTIST', p.albumartist);
  if (p.genre != '') desired.set('GENRE', p.genre);
  if (p.year != '') desired.set('DATE', p.year);
  if (p.trackNo > 0) desired.set('TRACKNUMBER', `${p.trackNo}`);
  if (p.discNo > 0) desired.set('DISCNUMBER', `${p.discNo}`);

  const vc = blocks.find((b: FlacBlock) => b.type == 4);
  const pic = blocks.find((b: FlacBlock) => b.type == 6);
  const patches: BytePatch[] = [];
  const edits: RangeEdit[] = [];
  let insertAt: number = -1;
  let insertBytes = new Uint8Array(0);

  if (vc) {
    const old = r.read(vc.dataStart, vc.dataEnd - vc.dataStart);
    const vl = readLe32(old, 0);
    const vendor = old.subarray(4, 4 + vl);
    const vcData = buildVorbisComment(vendor, desired);
    const header = new Uint8Array(4);
    header[0] = (vc.last ? 0x80 : 0) | 0x04;
    header[1] = (vcData.length >>> 16) & 0xff;
    header[2] = (vcData.length >>> 8) & 0xff;
    header[3] = vcData.length & 0xff;
    const edit = new RangeEdit();
    edit.start = vc.headerPos;
    edit.end = vc.dataEnd;
    edit.bytes = concat([header, vcData]);
    edits.push(edit);
  } else {
    const first = blocks.length > 0 ? blocks[0] : undefined;
    insertAt = first ? first.dataEnd : 4;
    const lastBlock = blocks.length > 0 ? blocks[blocks.length - 1] : undefined;
    if (insertAt >= size && lastBlock) {
      const h = r.read(lastBlock.headerPos, 1);
      patches.push({ pos: lastBlock.headerPos, bytes: new Uint8Array([h[0] & 0x7f]) });
    }
    const vcData = buildVorbisComment(new Uint8Array(0), desired);
    const header = new Uint8Array(4);
    header[0] = (insertAt >= size ? 0x80 : 0) | 0x04;
    header[1] = (vcData.length >>> 16) & 0xff;
    header[2] = (vcData.length >>> 8) & 0xff;
    header[3] = vcData.length & 0xff;
    insertBytes = concat([insertBytes, header, vcData]);
  }

  if (cover) {
    if (pic) {
      const edit = new RangeEdit();
      edit.start = pic.headerPos;
      edit.end = pic.dataEnd;
      edit.bytes = buildPictureBlock(cover, mime, pic.last);
      edits.push(edit);
    } else {
      // 插在 Vorbis Comment 之前；若无 VC 则与 VC 一起插在 STREAMINFO 之后
      if (insertAt < 0) {
        insertAt = vc ? vc.headerPos : (blocks.length > 0 ? blocks[0].dataEnd : 4);
      }
      insertBytes = concat([insertBytes, buildPictureBlock(cover, mime, false)]);
    }
  }
  if (insertBytes.length > 0 && insertAt >= 0) {
    const edit = new RangeEdit();
    edit.start = insertAt;
    edit.end = insertAt;
    edit.bytes = insertBytes;
    edits.push(edit);
  }
  edits.sort((a: RangeEdit, b: RangeEdit) => a.start - b.start);
  let cursor = 0;
  for (const edit of edits) {
    copyRange(r, dstFd, cursor, edit.start, patches);
    writeBytes(dstFd, edit.bytes);
    cursor = edit.end;
  }
  copyRange(r, dstFd, cursor, size, patches);
}

// ---------------- MP3 ----------------

interface Id3Frame {
  id: string;
  body: Uint8Array;
}

function decodeId3(body: Uint8Array): string {
  if (body.length < 1) {
    return '';
  }
  const enc = body[0];
  if (enc == 0 || enc == 3) {
    const d = new util.TextDecoder();
    return d.decodeWithStream(body.subarray(1));
  }
  let cs = '';
  for (let i = 1; i + 1 < body.length; i += 2) {
    cs += String.fromCharCode(body[i] | (body[i + 1] << 8));
  }
  return cs.replace(/\0+$/, '');
}

function encodeId3(s: string): Uint8Array {
  // UTF-8（encoding=3），兼容 ID3v2.3 / v2.4
  return concat([new Uint8Array([3]), bytesOf(s)]);
}

function buildId3v1(p: TagPatch): Uint8Array {
  const b = new Uint8Array(128);
  b.set(bytesOf('TAG'), 0);
  const put = (s: string, off: number, len: number): void => {
    const src = bytesOf(s);
    const n = Math.min(len - 1, src.length);
    for (let i = 0; i < n; i++) {
      b[off + i] = src[i];
    }
  };
  put(p.title, 3, 30);
  put(p.artist, 33, 30);
  put(p.album, 63, 30);
  put(p.year, 93, 4);
  if (p.trackNo > 0 && p.trackNo < 256) {
    b[125] = p.trackNo;
  }
  return b;
}

function mergeId3Frames(frames: Id3Frame[], p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const desired = new Map<string, string>();
  if (p.title != '') desired.set('TIT2', p.title);
  if (p.artist != '') desired.set('TPE1', p.artist);
  if (p.albumartist != '') desired.set('TPE2', p.albumartist);
  if (p.album != '') desired.set('TALB', p.album);
  if (p.genre != '') desired.set('TCON', p.genre);
  if (p.year != '') desired.set('TYER', p.year);
  if (p.trackNo > 0) desired.set('TRCK', `${p.trackNo}`);
  if (p.discNo > 0) desired.set('TPOS', `${p.discNo}`);
  const idMap = ['TIT2', 'TPE1', 'TPE2', 'TALB', 'TCON', 'TYER', 'TRCK', 'TPOS'];
  for (const id of idMap) {
    const text = desired.get(id);
    if (text == undefined) {
      continue;
    }
    let found = false;
    for (const f of frames) {
      if (f.id == id) {
        f.body = encodeId3(text);
        found = true;
        break;
      }
    }
    if (!found) {
      frames.push({ id: id, body: encodeId3(text) });
    }
  }
  if (cover && cover.length > 0) {
    const apic = concat([
      new Uint8Array([3]), bytesOf(mime), new Uint8Array([0, 3, 0]), cover
    ]);
    let foundApic = false;
    for (const f of frames) {
      if (f.id == 'APIC') {
        f.body = apic;
        foundApic = true;
        break;
      }
    }
    if (!foundApic) {
      frames.push({ id: 'APIC', body: apic });
    }
  }
}

function buildId3TagBytes(frames: Id3Frame[]): Uint8Array {
  const frameParts: Uint8Array[] = [];
  let tagBodyLen = 0;
  for (const f of frames) {
    const h = concat([bytesOf(f.id), u32be(f.body.length), new Uint8Array([0, 0])]);
    frameParts.push(h);
    frameParts.push(f.body);
    tagBodyLen += h.length + f.body.length;
  }
  // ID3v2 头固定 10 字节：'ID3' + 版本(2) + 标志(1) + syncsafe 长度(4)
  return concat([bytesOf('ID3'), new Uint8Array([3, 0, 0]), syncsafe(tagBodyLen), concat(frameParts)]);
}

function writeMp3(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  let audioStart = 0;
  let major = 3;
  const frames: Id3Frame[] = [];
  const head = r.read(0, 10);
  if (ascii(head, 0, 3) == 'ID3') {
    major = head[3];
    const tagSize = readSyncsafe(head, 6); // ID3v2 全系标签头长度均为 syncsafe
    audioStart = Math.min(size, 10 + tagSize);
    let pos = 10;
    while (pos + 10 <= audioStart) {
      const fh = r.read(pos, 10);
      if (!/^[A-Z0-9]{4}$/.test(ascii(fh, 0, 4))) {
        break;
      }
      const fsize = major >= 4 ? readSyncsafe(fh, 4) : readBe32(fh, 4);
      if (fsize <= 0 || fsize > 16777216 || pos + 10 + fsize > audioStart + 8) {
        break;
      }
      frames.push({ id: ascii(fh, 0, 4), body: r.read(pos + 10, fsize) });
      pos += 10 + fsize;
    }
  }
  mergeId3Frames(frames, p, cover, mime);
  const id3 = buildId3TagBytes(frames);
  const tail = r.read(Math.max(0, size - 128), 128);
  const hasId3v1 = ascii(tail, 0, 3) == 'TAG';
  const audioEnd = hasId3v1 ? size - 128 : size;
  writeBytes(dstFd, id3);
  copyRange(r, dstFd, audioStart, audioEnd, []);
  if (hasId3v1) {
    const v1 = buildId3v1(p);
    // 仅在提供了对应字段时覆盖，否则保留原 ID3v1 值
    const old = tail;
    if (p.title == '') v1.set(old.subarray(3, 33), 3);
    if (p.artist == '') v1.set(old.subarray(33, 63), 33);
    if (p.album == '') v1.set(old.subarray(63, 93), 63);
    if (p.year == '') v1.set(old.subarray(93, 97), 93);
    if (p.trackNo == 0) v1[125] = old[125];
    v1.set(old.subarray(97, 125), 97);
    v1[125] = p.trackNo > 0 && p.trackNo < 256 ? p.trackNo : old[125];
    v1.set(old.subarray(126, 128), 126);
    writeBytes(dstFd, v1);
  }
}

// ---------------- WAV（ID3 chunk） ----------------

interface WavChunk {
  id: string;
  dataStart: number;
  dataLen: number;
}

// 与桌面版一致：WAV 的标签写成 RIFF 内的 ID3 块，不碰 fmt/data。
function writeWav(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const head = r.read(0, 12);
  if (head.length < 12 || ascii(head, 0, 4) != 'RIFF' || ascii(head, 8, 4) != 'WAVE') {
    throw new Error('不是合法的 WAV 文件');
  }
  const declared = readLe32(head, 4) + 8;
  const end = Math.min(size, declared > 12 ? declared : size);
  const chunks: WavChunk[] = [];
  let tagStart = -1;
  let tagLen = 0;
  let pos = 12;
  while (pos + 8 <= end) {
    const h = r.read(pos, 8);
    if (h.length < 8) {
      break;
    }
    const cid = ascii(h, 0, 4);
    const clen = readLe32(h, 4);
    const dataStart = pos + 8;
    const dataLen = Math.min(clen, Math.max(0, end - dataStart));
    if (cid == 'ID3 ' || cid == 'id3 ') {
      tagStart = dataStart;
      tagLen = dataLen;
    } else {
      chunks.push({ id: cid, dataStart: dataStart, dataLen: dataLen });
    }
    pos = dataStart + clen + (clen & 1);
  }
  const frames: Id3Frame[] = [];
  if (tagStart >= 0 && tagLen >= 10) {
    const th = r.read(tagStart, 10);
    if (ascii(th, 0, 3) == 'ID3') {
      const major = th[3];
      const tagEnd = Math.min(tagStart + 10 + readSyncsafe(th, 6), size);
      let fp = tagStart + 10;
      while (fp + 10 <= tagEnd) {
        const fh = r.read(fp, 10);
        const id = ascii(fh, 0, 4);
        if (!/^[A-Z0-9]{4}$/.test(id)) {
          break;
        }
        const fsize = major >= 4 ? readSyncsafe(fh, 4) : readBe32(fh, 4);
        if (fsize <= 0 || fsize > 16777216 || fp + 10 + fsize > tagEnd + 8) {
          break;
        }
        frames.push({ id: id, body: r.read(fp + 10, fsize) });
        fp += 10 + fsize;
      }
    }
  }
  mergeId3Frames(frames, p, cover, mime);
  const id3 = buildId3TagBytes(frames);
  let total = 12 + 8 + id3.length + (id3.length & 1);
  for (const c of chunks) {
    total += 8 + c.dataLen + (c.dataLen & 1);
  }
  writeBytes(dstFd, concat([bytesOf('RIFF'), u32le(total - 8), bytesOf('WAVE')]));
  for (const c of chunks) {
    writeBytes(dstFd, concat([bytesOf(c.id), u32le(c.dataLen)]));
    copyRange(r, dstFd, c.dataStart, c.dataStart + c.dataLen, []);
    if ((c.dataLen & 1) != 0) {
      writeBytes(dstFd, new Uint8Array(1));
    }
  }
  writeBytes(dstFd, concat([bytesOf('ID3 '), u32le(id3.length), id3]));
  if ((id3.length & 1) != 0) {
    writeBytes(dstFd, new Uint8Array(1));
  }
}

// ---------------- M4A / MP4 ----------------

interface Mp4Atom {
  start: number;
  size: number;
  type: number;
  headerLen: number;
}

function atomType(s: string): number {
  return s.charCodeAt(0) * 16777216 + s.charCodeAt(1) * 65536 + s.charCodeAt(2) * 256 + s.charCodeAt(3);
}

function findAtom(r: RAF, start: number, end: number, type: number): Mp4Atom | undefined {
  let pos = start;
  while (pos + 8 <= end) {
    const h = r.read(pos, 8);
    if (h.length < 8) {
      break;
    }
    const size = readBe32(h, 0);
    const t = readBe32(h, 4);
    const headerLen = size == 1 ? 16 : 8;
    if (size < headerLen || pos + size > end) {
      break;
    }
    if (t == type) {
      return { start: pos, size: size, type: t, headerLen: headerLen };
    }
    pos += size;
  }
  return undefined;
}

function buildIlstChild(name: string, text: string): Uint8Array {
  const textBytes = bytesOf(text);
  // data box：8 字节 version/flags/locale（文本类型=1）+ UTF-8 正文
  const data = concat([new Uint8Array([0, 0, 0, 1, 0, 0, 0, 0]), textBytes]);
  const inner = concat([u32be(data.length), bytesOf('data'), data]);
  return concat([u32be(inner.length + 8), bytesOf(name), inner]);
}

function buildIlstDisk(disc: number): Uint8Array {
  // data box：8 字节头 + reserved(2) + disc(2) + total(2)
  const data = concat([
    new Uint8Array([0, 0, 0, 0, 0, 0, 0, 0]),
    new Uint8Array([0, 0, (disc >>> 8) & 0xff, disc & 0xff, 0, 0])
  ]);
  const inner = concat([u32be(data.length), bytesOf('data'), data]);
  return concat([u32be(inner.length + 8), bytesOf('disk'), inner]);
}

function writeM4a(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const moov = findAtom(r, 0, size, atomType('moov'));
  if (!moov) {
    throw new Error('M4A 缺少 moov');
  }
  const moovEnd = moov.start + moov.size;
  const udta = findAtom(r, moov.start + moov.headerLen, moovEnd, atomType('udta'));
  if (!udta) {
    throw new Error('M4A 缺少 udta');
  }
  const udtaEnd = udta.start + udta.size;
  const meta = findAtom(r, udta.start + udta.headerLen, udtaEnd, atomType('meta'));
  if (!meta) {
    throw new Error('M4A 缺少 meta');
  }
  const ilst = findAtom(r, meta.start + meta.headerLen + 4, meta.start + meta.size, atomType('ilst'));
  if (!ilst) {
    throw new Error('M4A 缺少 ilst');
  }
  const ilstEnd = ilst.start + ilst.size;

  // 保留非文本原子（封面等），替换/追加文本原子
  const keep: Uint8Array[] = [];
  let pos = ilst.start + 8;
  const names = ['©nam', '©ART', 'aART', '©alb', '©day', '©gen', 'covr'];
  while (pos + 8 <= ilstEnd) {
    const h = r.read(pos, 8);
    const childSize = readBe32(h, 0);
    if (childSize < 8 || pos + childSize > ilstEnd) {
      break;
    }
    const name = ascii(h, 4, 4);
    if (names.indexOf(name) < 0) {
      keep.push(r.read(pos, childSize));
    }
    pos += childSize;
  }
  const values: string[] = [p.title, p.artist, p.albumartist, p.album, p.year, p.genre];
  const children: Uint8Array[] = [];
  for (let i = 0; i < values.length; i++) {
    if (values[i] != '') {
      children.push(buildIlstChild(names[i], values[i]));
    }
  }
  for (const b of keep) {
    children.push(b);
  }
  if (p.discNo > 0) {
    children.push(buildIlstDisk(p.discNo));
  }
  if (cover) {
    const dataType: number = mime == 'image/png' ? 14 : 13;
    const data = concat([new Uint8Array([0, 0, 0, dataType, 0, 0, 0, 0]), cover]);
    const inner = concat([u32be(data.length + 8), bytesOf('data'), data]);
    children.push(concat([u32be(inner.length + 8), bytesOf('covr'), inner]));
  }
  const body = concat(children);
  const newIlst = concat([u32be(body.length + 8), bytesOf('ilst'), body]);
  const delta = newIlst.length - ilst.size;
  const patches: BytePatch[] = [];
  if (delta != 0) {
    patches.push(patchBe32(moov.start, moov.size + delta));
    patches.push(patchBe32(udta.start, udta.size + delta));
    patches.push(patchBe32(meta.start, meta.size + delta));
  }
  copyRange(r, dstFd, 0, ilst.start, patches);
  writeBytes(dstFd, newIlst);
  copyRange(r, dstFd, ilstEnd, size, []);
}

// ---------------- DSF（ID3v2.3） ----------------

function utf16leBytes(text: string): Uint8Array {
  const out = new Uint8Array(text.length * 2);
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    out[i * 2] = c & 0xff;
    out[i * 2 + 1] = (c >>> 8) & 0xff;
  }
  return out;
}

function id3TextFrame(id: string, text: string): Uint8Array {
  // 编码字节 1 = UTF-16 + BOM（ID3v2.3 支持中文）；帧头 10 字节含 2 字节 flags
  const body = concat([new Uint8Array([1, 0xff, 0xfe]), utf16leBytes(text)]);
  return concat([bytesOf(id), u32be(body.length), new Uint8Array([0, 0]), body]);
}

function id3ApicFrame(mime: string, cover: Uint8Array): Uint8Array {
  const body = concat([
    new Uint8Array([0]),
    bytesOf(mime),
    new Uint8Array([0, 3, 0]),
    cover
  ]);
  return concat([bytesOf('APIC'), u32be(body.length), new Uint8Array([0, 0]), body]);
}

function buildId3Tag(patch: TagPatch, cover?: Uint8Array, mime?: string): Uint8Array {
  const frames: Uint8Array[] = [];
  const add = (id: string, text: string): void => {
    if (text != '') {
      frames.push(id3TextFrame(id, text));
    }
  };
  add('TIT2', patch.title);
  add('TPE1', patch.artist);
  add('TPE2', patch.albumartist);
  add('TALB', patch.album);
  add('TCON', patch.genre);
  add('TYER', patch.year);
  if (patch.trackNo > 0) {
    add('TRCK', `${patch.trackNo}`);
  }
  if (patch.discNo > 0) {
    add('TPOS', `${patch.discNo}`);
  }
  if (cover != undefined && cover.length > 0) {
    frames.push(id3ApicFrame(mime != undefined ? mime : 'image/jpeg', cover));
  }
  const body = concat(frames);
  return concat([bytesOf('ID3'), new Uint8Array([3, 0, 0]), syncsafe(body.length), body]);
}

function copyRangeFd(srcFd: number, dstFd: number, from: number, to: number,
  patches: BytePatch[]): void {
  if (to <= from) {
    return;
  }
  const buf = new ArrayBuffer(COPY_BUF_BYTES);
  let pos = from;
  while (pos < to) {
    const len = Math.min(COPY_BUF_BYTES, to - pos);
    let n = 0;
    try {
      n = fs.readSync(srcFd, buf, { offset: pos, length: len });
    } catch (e) {
      break;
    }
    if (n <= 0) {
      break;
    }
    const part = new Uint8Array(buf, 0, n);
    applyPatches(part, pos, patches);
    writeBytes(dstFd, part);
    pos += n;
  }
}

// DSF 追加/替换 ID3；音频区原样复制，保证 DSD 位流不被重编码
function writeDsf(dstFd: number, srcFd: number, size: number, patch: TagPatch,
  cover?: Uint8Array, mime?: string): void {
  const hBuf = new ArrayBuffer(92);
  const hn = fs.readSync(srcFd, hBuf, { offset: 0, length: 92 });
  const h = new Uint8Array(hBuf, 0, hn);
  if (h.length < 92) {
    throw new Error(`DSF 头读取失败（${h.length} 字节）`);
  }
  if (ascii(h, 0, 4) != 'DSD ') {
    throw new Error(`DSF 标识异常：${ascii(h, 0, 4)}`);
  }
  const metadataPtr = readU64le(h, 20);
  const audioEnd = metadataPtr > 0 && metadataPtr < size ? metadataPtr : size;
  const tag = buildId3Tag(patch, cover, mime);
  const newSize = audioEnd + tag.length;
  const patches: BytePatch[] = [
    { pos: 12, bytes: u64le(newSize) },
    { pos: 20, bytes: u64le(tag.length > 0 ? audioEnd : 0) }
  ];
  copyRangeFd(srcFd, dstFd, 0, audioEnd, patches);
  writeBytes(dstFd, tag);
}

// ---------------- AIFF（FORM 内 ID3 块） ----------------

interface AiffChunk {
  id: string;
  start: number;
  len: number;
}

function readId3Frames(r: RAF, tagStart: number, tagLen: number, size: number): Id3Frame[] {
  const frames: Id3Frame[] = [];
  if (tagStart < 0 || tagLen < 10) {
    return frames;
  }
  const th = r.read(tagStart, 10);
  if (th.length < 10 || ascii(th, 0, 3) != 'ID3') {
    return frames;
  }
  const major = th[3];
  const tagEnd = Math.min(tagStart + 10 + readSyncsafe(th, 6), size);
  let fp = tagStart + 10;
  while (fp + 10 <= tagEnd) {
    const fh = r.read(fp, 10);
    if (fh.length < 10) {
      break;
    }
    const id = ascii(fh, 0, 4);
    if (!/^[A-Z0-9]{4}$/.test(id)) {
      break;
    }
    const fsize = major >= 4 ? readSyncsafe(fh, 4) : readBe32(fh, 4);
    if (fsize <= 0 || fsize > 16777216 || fp + 10 + fsize > tagEnd + 8) {
      break;
    }
    frames.push({ id: id, body: r.read(fp + 10, fsize) });
    fp += 10 + fsize;
  }
  return frames;
}

// AIFF/AIFC：删除旧 ID3 块，其余 chunk 原样复制，末尾追加新 ID3 块并修正 FORM 长度
function writeAiff(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const head = r.read(0, 12);
  if (head.length < 12 || ascii(head, 0, 4) != 'FORM') {
    throw new Error('不是合法的 AIFF 文件');
  }
  const formType = ascii(head, 8, 4);
  if (formType != 'AIFF' && formType != 'AIFC') {
    throw new Error(`AIFF 类型异常：${formType}`);
  }
  const chunks: AiffChunk[] = [];
  let tagStart = -1;
  let tagLen = 0;
  let pos = 12;
  while (pos + 8 <= size) {
    const h = r.read(pos, 8);
    if (h.length < 8) {
      break;
    }
    const cid = ascii(h, 0, 4);
    const clen = readBe32(h, 4);
    const dataStart = pos + 8;
    const dataLen = Math.min(clen, Math.max(0, size - dataStart));
    if (cid == 'ID3 ' || cid == 'id3 ') {
      tagStart = dataStart;
      tagLen = dataLen;
    } else {
      chunks.push({ id: cid, start: dataStart, len: dataLen });
    }
    pos = dataStart + clen + (clen & 1);
  }
  const frames = readId3Frames(r, tagStart, tagLen, size);
  mergeId3Frames(frames, p, cover, mime);
  const id3 = buildId3TagBytes(frames);
  let total = 4;
  for (const c of chunks) {
    total += 8 + c.len + (c.len & 1);
  }
  total += 8 + id3.length + (id3.length & 1);
  writeBytes(dstFd, concat([bytesOf('FORM'), u32be(total), bytesOf(formType)]));
  for (const c of chunks) {
    writeBytes(dstFd, concat([bytesOf(c.id), u32be(c.len)]));
    copyRange(r, dstFd, c.start, c.start + c.len, []);
    if ((c.len & 1) != 0) {
      writeBytes(dstFd, new Uint8Array(1));
    }
  }
  writeBytes(dstFd, concat([bytesOf('ID3 '), u32be(id3.length), id3]));
  if ((id3.length & 1) != 0) {
    writeBytes(dstFd, new Uint8Array(1));
  }
}

// ---------------- APEv2（TTA / WavPack / TAK） ----------------

function apeItem(key: string, value: Uint8Array, flags: number): Uint8Array {
  return concat([u32le(value.length), u32le(flags), bytesOf(key), new Uint8Array(1), value]);
}

function buildApeTag(p: TagPatch, cover?: Uint8Array, mime: string = ''): Uint8Array {
  const items: Uint8Array[] = [];
  const addText = (key: string, val: string): void => {
    if (val != '') {
      items.push(apeItem(key, bytesOf(val), 0));
    }
  };
  addText('Title', p.title);
  addText('Artist', p.artist);
  addText('Album', p.album);
  addText('Album Artist', p.albumartist);
  addText('Genre', p.genre);
  addText('Year', p.year);
  if (p.trackNo > 0) {
    addText('Track', `${p.trackNo}`);
  }
  if (p.discNo > 0) {
    addText('Disc', `${p.discNo}`);
  }
  if (cover != undefined && cover.length > 0) {
    const name = bytesOf(mime == 'image/png' ? 'cover.png' : 'cover.jpg');
    items.push(apeItem('Cover Art (Front)', concat([name, new Uint8Array(1), cover]), 2));
  }
  const body = concat(items);
  const tagSize = body.length + 32;
  const count = items.length;
  const header = concat([bytesOf('APETAGEX'), u32le(2000), u32le(tagSize), u32le(count),
    u32le(0xA0000000), u64le(0)]);
  const footer = concat([bytesOf('APETAGEX'), u32le(2000), u32le(tagSize), u32le(count),
    u32le(0x80000000), u64le(0)]);
  return concat([header, body, footer]);
}

// 去掉文件尾部旧的 APEv2 / ID3v1 标签后，重新追加 APEv2
function writeApe(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = ''): void {
  let audioEnd = size;
  if (audioEnd >= 128) {
    const tail = r.read(audioEnd - 128, 3);
    if (ascii(tail, 0, 3) == 'TAG') {
      audioEnd -= 128;
    }
  }
  if (audioEnd >= 32) {
    const footer = r.read(audioEnd - 32, 32);
    if (ascii(footer, 0, 8) == 'APETAGEX') {
      const tagSize = readLe32(footer, 12);
      const flags = readLe32(footer, 20);
      const hasHeader = (flags & 0x80000000) != 0;
      const start = audioEnd - tagSize - (hasHeader ? 32 : 0);
      if (start >= 0 && tagSize > 32) {
        audioEnd = start;
      }
    }
  }
  copyRange(r, dstFd, 0, audioEnd, []);
  writeBytes(dstFd, buildApeTag(p, cover, mime));
}

// ---------------- Ogg（Vorbis / Opus 注释重写） ----------------

class OggPageInfo {
  start: number = 0;
  headerLen: number = 0;
  bodyLen: number = 0;
  dataStart: number = 0;
  seq: number = 0;
  serial: number = 0;
}

const OGG_CRC_TABLE: number[] = [];

function oggCrcTable(): number[] {
  if (OGG_CRC_TABLE.length == 0) {
    for (let i = 0; i < 256; i++) {
      let r = i << 24;
      for (let j = 0; j < 8; j++) {
        if ((r & 0x80000000) != 0) {
          r = (r << 1) ^ 0x04c11db7;
        } else {
          r = r << 1;
        }
      }
      OGG_CRC_TABLE.push(r >>> 0);
    }
  }
  return OGG_CRC_TABLE;
}

function oggCrc(data: Uint8Array): number {
  const table = oggCrcTable();
  let crc = 0;
  for (let i = 0; i < data.length; i++) {
    crc = ((crc << 8) ^ table[((crc >>> 24) ^ data[i]) & 0xff]) >>> 0;
  }
  return crc >>> 0;
}

function readOggPage(r: RAF, pos: number, size: number): OggPageInfo | undefined {
  if (pos + 27 > size) {
    return undefined;
  }
  const h = r.read(pos, 27);
  if (h.length < 27 || ascii(h, 0, 4) != 'OggS') {
    return undefined;
  }
  const segCount = h[26];
  const lacing = r.read(pos + 27, segCount);
  if (lacing.length < segCount) {
    return undefined;
  }
  let dataLen = 0;
  for (let i = 0; i < segCount; i++) {
    dataLen += lacing[i];
  }
  const page = new OggPageInfo();
  page.start = pos;
  page.headerLen = 27 + segCount;
  page.bodyLen = dataLen;
  page.dataStart = pos + 27 + segCount;
  page.seq = readLe32(h, 18);
  page.serial = readLe32(h, 14);
  return page;
}

function buildOggPage(serial: number, seq: number, headerType: number,
  granule: number, packet: Uint8Array): Uint8Array {
  if (packet.length > 254 + 254 * 255) {
    throw new Error('Ogg 注释包超过 64KB，暂不支持写入');
  }
  const lacing: number[] = [];
  let left = packet.length;
  while (left >= 255) {
    lacing.push(255);
    left -= 255;
  }
  lacing.push(left);
  const header = new Uint8Array(27);
  header.set(bytesOf('OggS'), 0);
  header[5] = headerType;
  let g = granule;
  for (let i = 0; i < 8; i++) {
    header[6 + i] = g & 0xff;
    g = Math.floor(g / 256);
  }
  header.set(u32le(serial), 14);
  header.set(u32le(seq), 18);
  header[26] = lacing.length;
  const page = concat([header, new Uint8Array(lacing), packet]);
  const crc = oggCrc(page);
  page[22] = crc & 0xff;
  page[23] = (crc >>> 8) & 0xff;
  page[24] = (crc >>> 16) & 0xff;
  page[25] = (crc >>> 24) & 0xff;
  return page;
}

function base64(data: Uint8Array): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let out = '';
  for (let i = 0; i < data.length; i += 3) {
    const b0 = data[i];
    const b1 = i + 1 < data.length ? data[i + 1] : 0;
    const b2 = i + 2 < data.length ? data[i + 2] : 0;
    out += chars.charAt(b0 >> 2);
    out += chars.charAt(((b0 & 3) << 4) | (b1 >> 4));
    out += i + 1 < data.length ? chars.charAt(((b1 & 15) << 2) | (b2 >> 6)) : '=';
    out += i + 2 < data.length ? chars.charAt(b2 & 63) : '=';
  }
  return out;
}

// 重建前 2~3 个头部页（标识 / 注释 / Vorbis 建立表），其余音频页只改页码并重算 CRC
function writeOgg(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const pkts: Uint8Array[] = [];
  let partial: number[] = [];
  let pos = 0;
  let serial = 0;
  let headerPages = 0;
  let kind = '';
  let need = 3;
  let resume = 0;
  while (pos + 27 <= size) {
    const page = readOggPage(r, pos, size);
    if (page == undefined) {
      throw new Error('Ogg 页结构损坏，未写入');
    }
    if (headerPages == 0) {
      serial = page.serial;
    }
    const lacing = r.read(pos + 27, page.headerLen - 27);
    const data = r.read(page.dataStart, page.bodyLen);
    let off = 0;
    for (let i = 0; i < lacing.length; i++) {
      const l = lacing[i];
      for (let j = 0; j < l && off + j < data.length; j++) {
        partial.push(data[off + j]);
      }
      off += l;
      if (l < 255) {
        pkts.push(new Uint8Array(partial));
        partial = [];
        if (pkts.length == 1) {
          const first = pkts[0];
          if (first.length >= 8 && ascii(first, 0, 8) == 'OpusHead') {
            kind = 'opus';
            need = 2;
          } else if (first.length >= 7 && first[0] == 1 && ascii(first, 1, 6) == 'vorbis') {
            kind = 'vorbis';
            need = 3;
          } else {
            throw new Error('不是可写的 Ogg Vorbis / Opus 文件');
          }
        }
        if (pkts.length >= need) {
          break;
        }
      }
    }
    pos += page.headerLen + page.bodyLen;
    headerPages++;
    if (pkts.length >= need) {
      resume = pos;
      break;
    }
    if (headerPages > 32) {
      throw new Error('Ogg 头部页过多，未写入');
    }
  }
  if (pkts.length < need || resume <= 0) {
    throw new Error('Ogg 头解析不完整，未写入');
  }
  const desired = new Map<string, string>();
  if (p.title != '') desired.set('TITLE', p.title);
  if (p.artist != '') desired.set('ARTIST', p.artist);
  if (p.album != '') desired.set('ALBUM', p.album);
  if (p.albumartist != '') desired.set('ALBUMARTIST', p.albumartist);
  if (p.genre != '') desired.set('GENRE', p.genre);
  if (p.year != '') desired.set('DATE', p.year);
  if (p.trackNo > 0) desired.set('TRACKNUMBER', `${p.trackNo}`);
  if (p.discNo > 0) desired.set('DISCNUMBER', `${p.discNo}`);

  const prefixLen = kind == 'vorbis' ? 7 : 8;
  const prefix = pkts[1].subarray(0, prefixLen);
  let oldBlock = pkts[1].subarray(prefixLen);
  if (oldBlock.length < 8) {
    const vendor = bytesOf('SeqAudioManager');
    oldBlock = concat([u32le(vendor.length), vendor, u32le(0)]);
  }
  if (cover != undefined && cover.length > 0) {
    const pic = buildPictureBlock(cover, mime, false).subarray(4);
    desired.set('METADATA_BLOCK_PICTURE', base64(pic));
  }
  const commentPkt = concat([prefix, buildVorbisComment(oldBlock, desired)]);
  const pages: Uint8Array[] = [];
  pages.push(buildOggPage(serial, 0, 0x02, 0, pkts[0]));
  pages.push(buildOggPage(serial, 1, 0, 0, commentPkt));
  if (kind == 'vorbis') {
    pages.push(buildOggPage(serial, 2, 0, 0, pkts[2]));
  }
  for (const pg of pages) {
    writeBytes(dstFd, pg);
  }
  const newHeaderPages = pages.length;
  let cur = resume;
  while (cur + 27 <= size) {
    const page = readOggPage(r, cur, size);
    if (page == undefined) {
      throw new Error('Ogg 音频页结构损坏，未写入');
    }
    const hdr = r.read(cur, page.headerLen);
    const body = r.read(page.dataStart, page.bodyLen);
    const newSeq = page.seq - headerPages + newHeaderPages;
    hdr[18] = newSeq & 0xff;
    hdr[19] = (newSeq >>> 8) & 0xff;
    hdr[20] = (newSeq >>> 16) & 0xff;
    hdr[21] = (newSeq >>> 24) & 0xff;
    hdr[22] = 0;
    hdr[23] = 0;
    hdr[24] = 0;
    hdr[25] = 0;
    const whole = concat([hdr, body]);
    const crc = oggCrc(whole);
    whole[22] = crc & 0xff;
    whole[23] = (crc >>> 8) & 0xff;
    whole[24] = (crc >>> 16) & 0xff;
    whole[25] = (crc >>> 24) & 0xff;
    writeBytes(dstFd, whole);
    cur += page.headerLen + page.bodyLen;
  }
}

// ---------------- 入口 ----------------

function extOf(path: string): string {
  const dot = path.lastIndexOf('.');
  return dot >= 0 ? path.substring(dot).toLowerCase() : '';
}

export class TagWriter {
  static hasBackup(path: string): boolean {
    try {
      fs.statSync(`${path}.bak`);
      return true;
    } catch (e) {
      return false;
    }
  }

  // 撤销最近一次标签写回：把 .bak 复制回原文件（保留 .bak 本身）
  static restoreBackup(path: string): boolean {
    const bak = `${path}.bak`;
    try {
      fs.statSync(bak);
      fs.copyFileSync(bak, path);
      return true;
    } catch (e) {
      return false;
    }
  }

  static async write(path: string, patch: TagPatch): Promise<WriteResult> {
    const res = new WriteResult();
    const ext = extOf(path);
    if (ext != '.flac' && ext != '.mp3' && ext != '.m4a' && ext != '.wav' && ext != '.dsf' &&
      ext != '.aiff' && ext != '.aif' && ext != '.tta' && ext != '.wv' && ext != '.tak' &&
      ext != '.ogg' && ext != '.opus') {
      res.message = `不支持写入的格式：${ext}`;
      return res;
    }
    const st = fs.statSync(path);
    if (st.size > MAX_WRITE_BYTES) {
      res.message = '文件超过 500MB，按策略拒绝写入';
      return res;
    }
    const tmp = `${path}.seqaudio.tmp`;
    const old = `${path}.seqaudio.old`;
    const keepBackup = (await Db.getSetting('tag_write_backup', '1')) != '0';
    const bak = `${path}.bak`;
    const src = fs.openSync(path, fs.OpenMode.READ_ONLY);
    let dstFile: fs.File | undefined = undefined;
    try {
      const before = TagParser.parse(src, ext, st.size);
      console.info(`tag write ${ext} size=${st.size} dur=${before.durationMs} sr=${before.sampleRate} title=${before.title}`);
      dstFile = fs.openSync(tmp, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      const r = new RAF(src);
      if (ext == '.flac') {
        writeFlac(dstFile.fd, r, st.size, patch);
      } else if (ext == '.mp3') {
        writeMp3(dstFile.fd, r, st.size, patch);
      } else if (ext == '.wav') {
        writeWav(dstFile.fd, r, st.size, patch);
      } else if (ext == '.dsf') {
        writeDsf(dstFile.fd, src.fd, st.size, patch);
      } else if (ext == '.aiff' || ext == '.aif') {
        writeAiff(dstFile.fd, r, st.size, patch);
      } else if (ext == '.tta' || ext == '.wv' || ext == '.tak') {
        writeApe(dstFile.fd, r, st.size, patch);
      } else if (ext == '.ogg' || ext == '.opus') {
        writeOgg(dstFile.fd, r, st.size, patch);
      } else {
        writeM4a(dstFile.fd, r, st.size, patch);
      }
      fs.closeSync(dstFile);
      dstFile = undefined;
      // 写后校验：可解析 + 时长差异 ≤1s + 目标字段回读一致
      const tmpFile = fs.openSync(tmp, fs.OpenMode.READ_ONLY);
      try {
        const tmpSize = fs.statSync(tmp).size;
        const after = TagParser.parse(tmpFile, ext, tmpSize);
        console.info(`tag verify dur=${after.durationMs} sr=${after.sampleRate} title=${after.title} tmp=${tmpSize}`);
        if (Math.abs(after.durationMs - before.durationMs) > 1000) {
          throw new Error('写后时长偏差超过 1s，已回滚');
        }
        const check = (want: string, got: string): void => {
          if (want != '' && got != want) {
            throw new Error(`写后回读不一致：期望 ${want}，实际 ${got}`);
          }
        };
        check(patch.title, after.title);
        check(patch.artist, after.artist);
        check(patch.album, after.album);
        check(patch.albumartist, after.albumartist);
        if (patch.discNo > 0 && after.discNo != patch.discNo) {
          throw new Error(`写后碟号回读不一致：期望 ${patch.discNo}，实际 ${after.discNo}`);
        }
        if (patch.year != '') {
          const wantYear = patch.year.match(/\d{4}/);
          const gotYear = after.year.match(/\d{4}/);
          if (wantYear && (!gotYear || wantYear[0] != gotYear[0])) {
            throw new Error(`写后年份回读不一致：期望 ${patch.year}，实际 ${after.year}`);
          }
        }
      } finally {
        fs.closeSync(tmpFile);
      }
      // 原子替换：原文件改名 → tmp 改名；失败则从备份/旧文件恢复
      const removeIfExists = (p: string): void => {
        try {
          fs.unlinkSync(p);
        } catch (e) {
          // ignore
        }
      };
      removeIfExists(keepBackup ? bak : old);
      fs.renameSync(path, keepBackup ? bak : old);
      try {
        fs.renameSync(tmp, path);
      } catch (e) {
        fs.renameSync(keepBackup ? bak : old, path);
        const err = e as BusinessError;
        throw new Error(err.message ? err.message : '原子替换失败');
      }
      if (!keepBackup) {
        removeIfExists(old);
      } else {
        res.backupPath = bak;
      }
      res.ok = true;
      res.message = keepBackup ? '写入成功（已保留 .bak）' : '写入成功';
    } catch (e) {
      const err = e as BusinessError;
      res.ok = false;
      res.message = err.message ? err.message : `${e}`;
      // 清理可能残留的 tmp（原文件仍在或已由 rename 恢复）
      try {
        fs.unlinkSync(tmp);
      } catch (e2) {
        // ignore
      }
    } finally {
      if (dstFile != undefined) {
        try {
          fs.closeSync(dstFile);
        } catch (e) {
          // ignore
        }
      }
      try {
        fs.closeSync(src);
      } catch (e) {
        // ignore
      }
    }
    return res;
  }

  // 封面嵌入：复用同一套全量重写 + 原子替换 + .bak 策略
  static async embedCover(path: string, coverPath: string): Promise<WriteResult> {
    const res = new WriteResult();
    const ext = extOf(path);
    if (ext != '.flac' && ext != '.mp3' && ext != '.m4a' && ext != '.wav' && ext != '.dsf' &&
      ext != '.aiff' && ext != '.aif' && ext != '.tta' && ext != '.wv' && ext != '.tak' &&
      ext != '.ogg' && ext != '.opus') {
      res.message = `不支持写入的格式：${ext}`;
      return res;
    }
    const mime = extOf(coverPath) == '.png' ? 'image/png' : 'image/jpeg';
    const st = fs.statSync(path);
    if (st.size > MAX_WRITE_BYTES) {
      res.message = '文件超过 500MB，按策略拒绝写入';
      return res;
    }
    const cf = fs.openSync(coverPath, fs.OpenMode.READ_ONLY);
    let cover: Uint8Array = new Uint8Array(0);
    try {
      const cs = fs.statSync(coverPath);
      const cab = new ArrayBuffer(cs.size);
      const n = fs.readSync(cf.fd, cab, { offset: 0, length: cs.size });
      cover = new Uint8Array(cab, 0, n);
    } finally {
      fs.closeSync(cf);
    }
    if (cover.length == 0) {
      res.message = '封面文件为空';
      return res;
    }
    const tmp = `${path}.seqaudio.tmp`;
    const old = `${path}.seqaudio.old`;
    const bak = `${path}.bak`;
    const keepBackup = (await Db.getSetting('tag_write_backup', '1')) != '0';
    const src = fs.openSync(path, fs.OpenMode.READ_ONLY);
    let dstFile: fs.File | undefined = undefined;
    try {
      const before = TagParser.parse(src, ext, st.size);
      dstFile = fs.openSync(tmp, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      const r = new RAF(src);
      // 只加封面时也要把原标签读回来重写：writeFlac 会用空 patch 重建 Vorbis Comment，
      // 不带上原字段就会把标题/歌手/专辑全部抹掉。
      const patch = new TagPatch();
      patch.title = before.title;
      patch.artist = before.artist;
      patch.album = before.album;
      patch.albumartist = before.albumartist;
      patch.genre = before.genre;
      patch.year = before.year;
      patch.trackNo = before.trackNo;
      patch.discNo = before.discNo;
      if (ext == '.flac') {
        writeFlac(dstFile.fd, r, st.size, patch, cover, mime);
      } else if (ext == '.mp3') {
        writeMp3(dstFile.fd, r, st.size, patch, cover, mime);
      } else if (ext == '.wav') {
        writeWav(dstFile.fd, r, st.size, patch, cover, mime);
      } else if (ext == '.dsf') {
        writeDsf(dstFile.fd, src.fd, st.size, patch, cover, mime);
      } else if (ext == '.aiff' || ext == '.aif') {
        writeAiff(dstFile.fd, r, st.size, patch, cover, mime);
      } else if (ext == '.tta' || ext == '.wv' || ext == '.tak') {
        writeApe(dstFile.fd, r, st.size, patch, cover, mime);
      } else if (ext == '.ogg' || ext == '.opus') {
        writeOgg(dstFile.fd, r, st.size, patch, cover, mime);
      } else {
        writeM4a(dstFile.fd, r, st.size, patch, cover, mime);
      }
      fs.closeSync(dstFile);
      dstFile = undefined;
      const tmpFile = fs.openSync(tmp, fs.OpenMode.READ_ONLY);
      try {
      const tmpSize = fs.statSync(tmp).size;
      const after = TagParser.parse(tmpFile, ext, tmpSize);
      if (Math.abs(after.durationMs - before.durationMs) > 1000) {
          throw new Error('写后时长偏差超过 1s，已回滚');
        }
        if (before.title != '' && after.title != before.title) {
          throw new Error(`嵌入封面后标签丢失：${before.title} → ${after.title || '(空)'}`);
        }
        if (before.album != '' && after.album != before.album) {
          throw new Error(`嵌入封面后专辑丢失：${before.album} → ${after.album || '(空)'}`);
        }
      } finally {
        fs.closeSync(tmpFile);
      }
      const removeIfExists = (p: string): void => {
        try {
          fs.unlinkSync(p);
        } catch (e) {
          // ignore
        }
      };
      removeIfExists(keepBackup ? bak : old);
      fs.renameSync(path, keepBackup ? bak : old);
      try {
        fs.renameSync(tmp, path);
      } catch (e) {
        fs.renameSync(keepBackup ? bak : old, path);
        const err = e as BusinessError;
        throw new Error(err.message ? err.message : '原子替换失败');
      }
      if (!keepBackup) {
        removeIfExists(old);
      } else {
        res.backupPath = bak;
      }
      res.ok = true;
      res.message = keepBackup ? '封面已嵌入（保留 .bak）' : '封面已嵌入';
    } catch (e) {
      const err = e as BusinessError;
      res.ok = false;
      res.message = err.message ? err.message : `${e}`;
      try {
        fs.unlinkSync(tmp);
      } catch (e2) {
        // ignore
      }
    } finally {
      if (dstFile != undefined) {
        try {
          fs.closeSync(dstFile);
        } catch (e) {
          // ignore
        }
      }
      try {
        fs.closeSync(src);
      } catch (e) {
        // ignore
      }
    }
    return res;
  }
}
