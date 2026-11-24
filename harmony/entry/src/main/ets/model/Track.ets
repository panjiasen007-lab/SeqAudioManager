// 曲目数据模型（与桌面版字段对齐的鸿蒙子集）
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
}

export const AUDIO_EXTS: string[] = ['.flac', '.mp3', '.wav', '.m4a', '.ogg', '.aac'];

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
    mtimeMs: 0
  };
}
