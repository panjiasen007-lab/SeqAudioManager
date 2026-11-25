import core from 'libseqaudio_core.so';

// 原生内核报告的源/输出格式（用于 UI 展示"直通"信息）
export interface NativeInfo {
  codec: string;
  sampleRate: number;
  channels: number;
  bits: number;
  durationMs: number;
  outRate: number;
  outChannels: number;
  outBits: number;
  eq: boolean;
  eqBands: number;
  fadeMs: number;
}

const NATIVE_EXTS: string[] = ['.flac', '.mp3', '.m4a', '.aac', '.wav', '.ape'];

// FFmpeg + OH_AudioRenderer 直通播放的薄封装；失败时由 PlayerEngine 回退 AVPlayer
export class NativePlayer {
  static available: boolean = true;
  private static opened: boolean = false;
  // EQ 在原生单例内跨曲目保持，这里同时缓存一份供 UI 与重开时对齐
  private static eqOn: boolean = false;
  private static eqGains: number[] = [];

  static supports(path: string): boolean {
    if (!NativePlayer.available) {
      return false;
    }
    const dot = path.lastIndexOf('.');
    if (dot < 0) {
      return false;
    }
    return NATIVE_EXTS.indexOf(path.substring(dot).toLowerCase()) >= 0;
  }

  static open(path: string): number {
    let r = -1;
    try {
      r = core.open(path);
    } catch (e) {
      r = -1;
    }
    NativePlayer.opened = r == 0;
    if (!NativePlayer.opened) {
      NativePlayer.available = false;
    } else if (NativePlayer.eqOn || NativePlayer.eqGains.length > 0) {
      // 原生层已按上次设置重建 DSP，这里再做一次幂等对齐，防止多处调用顺序不一致
      NativePlayer.setEq(NativePlayer.eqOn, NativePlayer.eqGains);
    }
    return r;
  }

  static start(): void {
    if (NativePlayer.opened) {
      core.start();
    }
  }

  static pause(): void {
    if (NativePlayer.opened) {
      core.pause();
    }
  }

  static stop(): void {
    if (NativePlayer.opened) {
      core.stop();
    }
  }

  static release(): void {
    if (NativePlayer.opened) {
      NativePlayer.opened = false;
      try {
        core.release();
      } catch (e) {
        // ignore
      }
    }
  }

  static seek(ms: number): void {
    if (NativePlayer.opened) {
      core.seek(Math.max(0, Math.round(ms)));
    }
  }

  static setVolume(v: number): void {
    if (NativePlayer.opened) {
      core.setVolume(v);
    }
  }

  // EQ 设置：控件一改就下发，原生层在渲染回调内生效（≤1 个回调，约 10–20ms）
  // 注意：@State 数组在 ArkUI 内是代理对象，NAPI 侧按普通数组读元素会拿到空数组，
  // 必须先复制成普通 number[] 再跨语言传递。
  static setEq(enabled: boolean, gains: number[]): void {
    const plain: number[] = [];
    for (let i = 0; i < gains.length; i++) {
      plain.push(gains[i]);
    }
    NativePlayer.eqOn = enabled;
    NativePlayer.eqGains = plain;
    try {
      core.setEq(enabled, plain);
    } catch (e) {
      // 原生库不可用：AVPlayer 降级路径没有 EQ
    }
  }

  static eqEnabled(): boolean {
    return NativePlayer.eqOn;
  }

  // DSP 数值自检（1kHz +12dB 与限幅上限），返回 JSON 字符串；失败返回空串
  static selfTest(): string {
    try {
      return core.selfTest();
    } catch (e) {
      return '';
    }
  }

  static position(): number {
    if (!NativePlayer.opened) {
      return 0;
    }
    return core.position();
  }

  static duration(): number {
    if (!NativePlayer.opened) {
      return 0;
    }
    return core.duration();
  }

  static takeInterrupt(): number {
    if (!NativePlayer.opened) {
      return 0;
    }
    return core.takeInterrupt();
  }

  static info(): NativeInfo {
    const out: NativeInfo = {
      codec: '', sampleRate: 0, channels: 0, bits: 0,
      durationMs: 0, outRate: 0, outChannels: 0, outBits: 0,
      eq: false, eqBands: 0, fadeMs: 0
    };
    if (!NativePlayer.opened) {
      return out;
    }
    try {
      const raw = JSON.parse(core.info()) as NativeInfo;
      out.codec = raw.codec;
      out.sampleRate = raw.sampleRate;
      out.channels = raw.channels;
      out.bits = raw.bits;
      out.durationMs = raw.durationMs;
      out.outRate = raw.outRate;
      out.outChannels = raw.outChannels;
      out.outBits = raw.outBits;
      out.eq = raw.eq;
      out.eqBands = raw.eqBands;
      out.fadeMs = raw.fadeMs;
    } catch (e) {
      // 解析失败保持默认值
    }
    return out;
  }

  // UI 用：如 "FLAC 24bit/96kHz · 直通 96kHz/24bit"
  static label(): string {
    const f = NativePlayer.info();
    if (f.codec == '') {
      return '';
    }
    const src = `${f.codec.toUpperCase()} ${f.bits}bit/${(f.sampleRate / 1000).toFixed(1)}kHz`;
    const same = f.outRate == f.sampleRate && f.outBits == f.bits;
    return same ? `${src} · 直通` : `${src} → ${f.outBits}bit/${(f.outRate / 1000).toFixed(1)}kHz`;
  }
}
