import media from '@ohos.multimedia.media';
import fs from '@ohos.file.fs';
import { BusinessError } from '@ohos.base';
import { Track } from '../model/Track';

export enum PlayMode {
  ORDER = 0,
  LOOP = 1,
  SINGLE = 2,
  RANDOM = 3
}

export const MODE_NAMES: string[] = ['顺序', '列表循环', '单曲循环', '随机'];

class StateWaiter {
  target: string = '';
  resolve: () => void = () => { };
}

// AVPlayer 封装：队列播放 / 四种模式 / 进度事件 / AppStorage 同步（供迷你条与播放页共享）
export class PlayerEngine {
  private static inst?: PlayerEngine;

  static get(): PlayerEngine {
    if (!PlayerEngine.inst) {
      PlayerEngine.inst = new PlayerEngine();
    }
    return PlayerEngine.inst;
  }

  private av?: media.AVPlayer;
  private fd: number = -1;
  private stateWaiter?: StateWaiter;
  private errStreak: number = 0;

  private setupStateWaiter(target: string): Promise<void> {
    return new Promise<void>((resolve: () => void, reject: (e: Error) => void) => {
      let done = false;
      const timer = setTimeout(() => {
        if (!done) {
          done = true;
          this.stateWaiter = undefined;
          reject(new Error(`wait state ${target} timeout`));
        }
      }, 5000);
      const w = new StateWaiter();
      w.target = target;
      w.resolve = () => {
        if (!done) {
          done = true;
          clearTimeout(timer);
          resolve();
        }
      };
      this.stateWaiter = w;
    });
  }

  private notifyState(state: string): void {
    const w = this.stateWaiter;
    if (w && state == w.target) {
      this.stateWaiter = undefined;
      w.resolve();
    }
  }

  queue: Track[] = [];
  index: number = -1;
  mode: PlayMode = PlayMode.LOOP;
  playing: boolean = false;
  positionMs: number = 0;
  durationMs: number = 0;
  volume: number = 1.0;
  // 会话恢复时待定位的进度（首次加载完成后 seek）
  private pendingSeekMs: number = 0;

  private listeners: (() => void)[] = [];
  // 由 Index 注入：关键事件立即持久化会话（避免 PlayerEngine 与 SessionStore 循环依赖）
  onSessionDirty?: (force: boolean) => void;

  addListener(cb: () => void): void {
    this.listeners.push(cb);
  }

  removeListener(cb: () => void): void {
    const i = this.listeners.indexOf(cb);
    if (i >= 0) {
      this.listeners.splice(i, 1);
    }
  }

  private emit(): void {
    for (const l of this.listeners) {
      try {
        l();
      } catch (e) {
        // ignore
      }
    }
    AppStorage.setOrCreate('npPlaying', this.playing);
    AppStorage.setOrCreate('npPos', this.positionMs);
    AppStorage.setOrCreate('npDur', this.durationMs);
    AppStorage.setOrCreate('npIdx', this.index);
    const t = this.current();
    AppStorage.setOrCreate('npTitle', t ? t.title : '');
    AppStorage.setOrCreate('npArtist', t ? t.artist : '');
  }

  current(): Track | undefined {
    if (this.index >= 0 && this.index < this.queue.length) {
      return this.queue[this.index];
    }
    return undefined;
  }

  private async ensurePlayer(): Promise<media.AVPlayer> {
    if (this.av) {
      return this.av;
    }
    const av = await media.createAVPlayer();
    const eng = this;
    av.on('stateChange', (state: string) => {
      eng.notifyState(state);
      if (state == 'playing') {
        eng.errStreak = 0;
        eng.playing = true;
        eng.emit();
      } else if (state == 'paused' || state == 'stopped') {
        eng.playing = false;
        eng.emit();
      } else if (state == 'completed') {
        eng.onTrackEnd();
      } else if (state == 'error') {
        eng.playing = false;
        eng.emit();
      }
    });
    av.on('timeUpdate', (t: number) => {
      eng.positionMs = t;
      AppStorage.setOrCreate('npPos', t);
      for (const l of eng.listeners) {
        try {
          l();
        } catch (e) {
          // ignore
        }
      }
    });
    av.on('durationUpdate', (d: number) => {
      eng.durationMs = d;
      AppStorage.setOrCreate('npDur', d);
    });
    av.on('error', (err: BusinessError) => {
      console.error(`PlayerEngine error: ${err.message}`);
      eng.playing = false;
      eng.emit();
      // 自动跳到下一曲，避免整条队列卡死（连续失败超过队列长度则停下）
      if (eng.queue.length > 1 && eng.errStreak < eng.queue.length) {
        eng.errStreak++;
        eng.nextByUser();
      }
    });
    this.av = av;
    return av;
  }

  private closeFd(): void {
    if (this.fd >= 0) {
      try {
        fs.closeSync(this.fd);
      } catch (e) {
        // ignore
      }
      this.fd = -1;
    }
  }

  private async loadCurrent(): Promise<void> {
    const t = this.current();
    if (!t) {
      return;
    }
    const av = await this.ensurePlayer();
    this.closeFd();
    try {
      const f = fs.openSync(t.path, fs.OpenMode.READ_ONLY);
      this.fd = f.fd;
      await av.reset();
      // 等 reset→initialized（设置 fdSrc 后异步迁移），再 prepare
      const ready = this.setupStateWaiter('initialized');
      av.fdSrc = { fd: this.fd };
      await ready;
      this.positionMs = 0;
      this.durationMs = t.durationMs;
      this.playing = false;
      this.emit();
      // prepare 的 Promise 在任务入队时即返回，需等 'prepared' 状态真正到达
      const prepared = this.setupStateWaiter('prepared');
      await av.prepare();
      await prepared;
      await av.setVolume(this.volume);
      if (this.pendingSeekMs > 0) {
        const target = Math.min(this.pendingSeekMs,
          this.durationMs > 0 ? this.durationMs - 50 : this.pendingSeekMs);
        av.seek(Math.max(0, target), media.SeekMode.SEEK_PREV_SYNC);
        this.positionMs = Math.max(0, target);
        this.pendingSeekMs = 0;
        this.emit();
      }
      await av.play();
    } catch (e) {
      const err = e as BusinessError;
      console.error(`loadCurrent fail: ${err.message}`);
      this.playing = false;
      this.emit();
    }
  }

  setQueue(tracks: Track[], startIndex: number): void {
    this.queue = tracks.slice();
    this.playIndex(startIndex);
  }

  // 会话恢复：只装载队列与进度，不自动播放；用户点播时从断点续播
  restoreQueue(tracks: Track[], startIndex: number, positionMs: number): void {
    this.queue = tracks.slice();
    this.index = startIndex >= 0 && startIndex < this.queue.length ? startIndex : -1;
    this.pendingSeekMs = Math.max(0, Math.round(positionMs));
    this.positionMs = this.pendingSeekMs;
    const cur = this.current();
    this.durationMs = cur ? cur.durationMs : 0;
    this.emit();
  }

  playIndex(i: number): void {
    if (i < 0 || i >= this.queue.length) {
      return;
    }
    this.index = i;
    if (this.onSessionDirty) {
      this.onSessionDirty(true);
    }
    this.loadCurrent();
  }

  toggle(): void {
    if (this.index < 0) {
      if (this.queue.length > 0) {
        this.playIndex(0);
      }
      return;
    }
    const av = this.av;
    if (!av) {
      // 恢复的队列尚未加载物理文件：首次点播时加载并在 prepared 后 seek 到断点
      this.loadCurrent();
      return;
    }
    if (this.playing) {
      av.pause();
    } else {
      av.play();
    }
  }

  seek(ms: number): void {
    const av = this.av;
    if (!av) {
      return;
    }
    const clamped = Math.max(0, Math.min(ms, this.durationMs > 0 ? this.durationMs - 50 : ms));
    this.positionMs = clamped;
    av.seek(clamped, media.SeekMode.SEEK_PREV_SYNC);
    this.emit();
    if (this.onSessionDirty) {
      this.onSessionDirty(true);
    }
  }

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(1, v));
    if (this.av) {
      this.av.setVolume(this.volume);
    }
    if (this.onSessionDirty) {
      this.onSessionDirty(true);
    }
  }

  setMode(m: PlayMode): void {
    this.mode = m;
    if (this.onSessionDirty) {
      this.onSessionDirty(true);
    }
  }

  private randomIndex(): number {
    if (this.queue.length <= 1) {
      return this.index;
    }
    let r = this.index;
    while (r == this.index) {
      r = Math.floor(Math.random() * this.queue.length);
    }
    return r;
  }

  nextByUser(): void {
    if (this.queue.length == 0) {
      return;
    }
    if (this.mode == PlayMode.RANDOM) {
      this.playIndex(this.randomIndex());
    } else {
      this.playIndex((this.index + 1) % this.queue.length);
    }
  }

  prevByUser(): void {
    if (this.queue.length == 0) {
      return;
    }
    if (this.mode == PlayMode.RANDOM) {
      this.playIndex(this.randomIndex());
    } else {
      this.playIndex((this.index - 1 + this.queue.length) % this.queue.length);
    }
  }

  private onTrackEnd(): void {
    if (this.mode == PlayMode.SINGLE) {
      this.playIndex(this.index);
    } else if (this.mode == PlayMode.RANDOM) {
      this.playIndex(this.randomIndex());
    } else if (this.mode == PlayMode.LOOP) {
      this.playIndex((this.index + 1) % this.queue.length);
    } else {
      // 顺序：播完即止
      this.playing = false;
      this.emit();
    }
  }

  stop(): void {
    if (this.av) {
      try {
        this.av.stop();
      } catch (e) {
        // ignore
      }
    }
    this.closeFd();
    this.playing = false;
    this.emit();
  }
}
