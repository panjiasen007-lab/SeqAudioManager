// 主窗口与迷你播放器 UIAbility 之间的进程内事件桥。
// 两个 UIAbility 同进程，用 emitter 传递播放状态与操作命令，避免依赖各自的 AppStorage。
import { emitter } from '@kit.BasicServicesKit';

export class MiniState {
  playing: boolean = false;
  pos: number = 0;
  dur: number = 0;
  title: string = '';
  artist: string = '';
  cover: string = '';
  // 三行歌词：上一行 / 当前行（高亮） / 下一行
  lyricPrev: string = '';
  lyricCur: string = '';
  lyricNext: string = '';
  // 播放参数
  volume: number = 80;
  muted: boolean = false;
  mode: number = 1;
  // 与主界面共享的不透明度（0~100）
  opacity: number = 95;

  toJson(): string {
    return JSON.stringify(this);
  }

  static parse(text: string): MiniState {
    const s = new MiniState();
    if (text == '') {
      return s;
    }
    try {
      const obj = JSON.parse(text) as MiniState;
      s.playing = obj.playing == true;
      s.pos = obj.pos != undefined ? obj.pos : 0;
      s.dur = obj.dur != undefined ? obj.dur : 0;
      s.title = obj.title != undefined ? obj.title : '';
      s.artist = obj.artist != undefined ? obj.artist : '';
      s.cover = obj.cover != undefined ? obj.cover : '';
      s.lyricPrev = obj.lyricPrev != undefined ? obj.lyricPrev : '';
      s.lyricCur = obj.lyricCur != undefined ? obj.lyricCur : '';
      s.lyricNext = obj.lyricNext != undefined ? obj.lyricNext : '';
      s.volume = obj.volume != undefined ? obj.volume : 80;
      s.muted = obj.muted == true;
      s.mode = obj.mode != undefined ? obj.mode : 1;
      s.opacity = obj.opacity != undefined ? obj.opacity : 80;
    } catch (e) {
      // 坏消息按空状态处理
    }
    return s;
  }
}

export class MiniEnvelope {
  json: string = '';
  cmd: string = '';
  value: number = 0;
}

export class MiniBridge {
  static readonly EV_STATE = 'seqaudio.mini.state';
  static readonly EV_CMD = 'seqaudio.mini.cmd';
  static readonly EV_READY = 'seqaudio.mini.ready';

  // 统一解包 emitter 的 data，避开 ArkTS 对 any 索引访问的限制
  static readEnvelope(data: emitter.EventData): MiniEnvelope {
    const env = new MiniEnvelope();
    if (data == undefined || data.data == undefined) {
      return env;
    }
    try {
      const obj = JSON.parse(JSON.stringify(data.data)) as MiniEnvelope;
      env.json = obj.json != undefined ? obj.json : '';
      env.cmd = obj.cmd != undefined ? obj.cmd : '';
      env.value = obj.value != undefined ? obj.value : 0;
    } catch (e) {
      // 坏消息按空处理
    }
    return env;
  }

  static emitState(s: MiniState): void {
    emitter.emit(MiniBridge.EV_STATE, { data: { json: s.toJson() } });
  }

  static sendCommand(cmd: string, value: number): void {
    emitter.emit(MiniBridge.EV_CMD, { data: { cmd: cmd, value: value } });
  }
}
