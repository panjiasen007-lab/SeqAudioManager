// 曲目数据模型（与桌面版字段对齐的鸿蒙子集；v1.1 扩展 CUE/健康/歌词/封面/元数据字段）
export interface Track {
  id: number;
  path: string;
  fileName: string;
  ext: string;
  title: string;
  artist: string;
  album: string;
  genre: string;
  year: string;
  trackNo: number;
  durationMs: number;
  sampleRate: number;
  channels: number;
  bitDepth: number;
  bitrateKbps: number;
  sizeBytes: number;
  mtimeMs: number;
  // v1.1 起新增
  cuePath: string;
  isCueChild: number;
  cueTrackNo: number;
  cueStartMs: number;
  cueEndMs: number;
  replayGain: number;
  healthStatus: string;
  healthDetail: string;
  searchBlob: string;
  lyrics: string;
  coverBlobPath: string;
  metaSource: string;
  metaCheckedAt: number;
}

export const AUDIO_EXTS: string[] = ['.flac', '.mp3', '.wav', '.m4a', '.ogg', '.aac'];
export const CUE_EXTS: string[] = ['.cue'];

export function emptyTrack(): Track {
  return {
    id: 0,
    path: '',
    fileName: '',
    ext: '',
    title: '',
    artist: '',
    album: '',
    genre: '',
    year: '',
    trackNo: 0,
    durationMs: 0,
    sampleRate: 0,
    channels: 0,
    bitDepth: 0,
    bitrateKbps: 0,
    sizeBytes: 0,
    mtimeMs: 0,
    cuePath: '',
    isCueChild: 0,
    cueTrackNo: 0,
    cueStartMs: 0,
    cueEndMs: 0,
    replayGain: 0,
    healthStatus: 'ok',
    healthDetail: '',
    searchBlob: '',
    lyrics: '',
    coverBlobPath: '',
    metaSource: '',
    metaCheckedAt: 0
  };
}

export function refreshSearchBlob(t: Track): string {
  const parts: string[] = [t.title, t.artist, t.album, t.fileName, t.genre, t.year];
  const out: string[] = [];
  for (const p of parts) {
    if (p.trim() != '') {
      out.push(p.trim());
    }
  }
  t.searchBlob = out.join(' ').toLowerCase();
  return t.searchBlob;
}
