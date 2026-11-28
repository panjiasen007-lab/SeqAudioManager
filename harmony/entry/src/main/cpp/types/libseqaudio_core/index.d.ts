export const open: (path: string) => number;
export const start: () => number;
export const pause: () => number;
export const stop: () => number;
export const release: () => void;
export const seek: (ms: number) => number;
export const setVolume: (volume: number) => number;
export const setEq: (enabled: boolean, gainsDb: number[]) => number;
export const selfTest: () => string;
export const position: () => number;
export const duration: () => number;
export const takeInterrupt: () => number;
export const info: () => string;
export const isOpen: () => boolean;
export const analyze: (path: string) => string;
export const exportSegment: (src: string, startMs: number, durationMs: number,
  format: string, bitDepth: number, dst: string) => number;
export const exportDsdSegment: (src: string, startMs: number, durationMs: number,
  dst: string) => number;
