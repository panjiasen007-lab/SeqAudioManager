// 迷你播放器：1:1 对齐 Windows 版小窗布局（横向紧凑面板）。
// 顶部：圆形旋转封面 + 歌曲标题 + 最小化/最大化/关闭；
// 进度条（右侧时间）→ 播放控制 + 音量 → 三行歌词高亮 → 底部透明度。
import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { window } from '@kit.ArkUI';
import { T } from '../theme/Tokens';
import { MiniBridge, MiniState } from '../mini/MiniBridge';
import { WindowUtil } from '../utils/WindowUtil';
import { fmtDuration } from '../utils/Util';

const MODE_GLYPHS: string[] = ['→', '①', '↻', '⤨'];

@Entry
@Component
struct MiniPlayer {
  @State private playing: boolean = false;
  @State private pos: number = 0;
  @State private dur: number = 0;
  @State private title: string = '';
  @State private artist: string = '';
  @State private cover: string = '';
  @State private lyricPrev: string = '';
  @State private lyricCur: string = '';
  @State private lyricNext: string = '';
  @State private volume: number = 80;
  @State private muted: boolean = false;
  @State private mode: number = 1;
  @State private uiOpacity: number = 95;
  @State private glow: number = 0.35;
  @State private angle: number = 0;
  @State private dragging: boolean = false;
  @State private dragValue: number = 0;
  private volumeDragging: boolean = false;
  private opacityDragging: boolean = false;
  private spinTimer: number = -1;

  private stateListener = (data: emitter.EventData): void => {
    const env = MiniBridge.readEnvelope(data);
    const s: MiniState = MiniState.parse(env.json);
    this.playing = s.playing;
    if (!this.dragging) {
      this.pos = s.pos;
    }
    this.dur = s.dur;
    this.title = s.title;
    this.artist = s.artist;
    this.cover = s.cover;
    this.lyricPrev = s.lyricPrev;
    this.lyricCur = s.lyricCur;
    this.lyricNext = s.lyricNext;
    if (!this.volumeDragging) {
      this.volume = s.volume;
    }
    this.muted = s.muted;
    this.mode = s.mode;
    if (!this.opacityDragging) {
      this.uiOpacity = s.opacity < 25 ? 25 : (s.opacity > 100 ? 100 : s.opacity);
    }
    this.syncSpin();
  };

  // 主窗口恢复时会发 close，迷你窗自行退出
  private cmdListener = (data: emitter.EventData): void => {
    const env = MiniBridge.readEnvelope(data);
    if (env.cmd == 'close') {
      this.terminateSelfNow();
    }
  };

  aboutToAppear(): void {
    emitter.on(MiniBridge.EV_STATE, this.stateListener);
    emitter.on(MiniBridge.EV_CMD, this.cmdListener);
    emitter.emit(MiniBridge.EV_READY);
    setTimeout(() => {
      animateTo({
        duration: 2200,
        curve: Curve.EaseInOut,
        iterations: -1,
        playMode: PlayMode.Alternate
      }, () => {
        this.glow = 1;
      });
    }, 120);
  }

  aboutToDisappear(): void {
    emitter.off(MiniBridge.EV_STATE, this.stateListener);
    emitter.off(MiniBridge.EV_CMD, this.cmdListener);
    if (this.spinTimer >= 0) {
      clearInterval(this.spinTimer);
      this.spinTimer = -1;
    }
  }

  private ctx(): common.UIAbilityContext {
    return getContext(this) as common.UIAbilityContext;
  }

  private terminateSelfNow(): void {
    try {
      this.ctx().terminateSelf();
    } catch (e) {
    }
  }

  private close(): void {
    MiniBridge.sendCommand('restore', 0);
    setTimeout(() => {
      this.terminateSelfNow();
    }, 120);
  }

  private doMinimize(): void {
    window.getLastWindow(this.ctx()).then((w: window.Window) => {
      w.minimize().catch((e: Error) => {
      });
    }).catch((e: Error) => {
    });
  }

  private syncSpin(): void {
    if (this.playing) {
      if (this.spinTimer < 0) {
        this.spinTimer = setInterval(() => {
          this.angle = (this.angle + 1.6) % 360;
        }, 40);
      }
    } else if (this.spinTimer >= 0) {
      clearInterval(this.spinTimer);
      this.spinTimer = -1;
    }
  }

  private glowBorder(): string {
    const a = Math.round((0.22 + this.glow * 0.40) * 255);
    const hex = a.toString(16).toUpperCase();
    return T.alpha(T.ACCENT, hex.length < 2 ? `0${hex}` : hex);
  }

  @Builder
  winButton(glyph: string, danger: boolean, cb: () => void) {
    Button(glyph)
      .width(24)
      .height(24)
      .padding(0)
      .borderRadius(5)
      .fontSize(12)
      .fontColor(danger ? T.FG_MUTE : T.FG_DIM)
      .backgroundColor(Color.Transparent)
      .hoverEffect(HoverEffect.Scale)
      .onClick(() => {
        cb();
      })
  }

  @Builder
  flatButton(glyph: string, size: number, color: string, cb: () => void) {
    Button(glyph)
      .width(size)
      .height(size)
      .padding(0)
      .borderRadius(7)
      .fontSize(13)
      .fontColor(color)
      .backgroundColor(Color.Transparent)
      .hoverEffect(HoverEffect.Scale)
      .onClick(() => {
        cb();
      })
  }

  build() {
    Stack({ alignContent: Alignment.Center }) {
      Column() {
        // 标题栏：圆形旋转封面 + 歌曲标题 + 窗口按钮（可拖动）
        Row({ space: 9 }) {
          Stack({ alignContent: Alignment.Center }) {
            Column()
              .width(40)
              .height(40)
              .borderRadius(20)
              .backgroundColor(T.INPUT)
              .border({ width: 1, color: this.playing ? T.ACCENT_DIM : T.LINE_SOFT })
            if (this.cover != '') {
              Image(this.cover)
                .width(36)
                .height(36)
                .borderRadius(18)
                .objectFit(ImageFit.Cover)
                .rotate({ angle: this.angle })
                .clip(true)
            } else {
              Text('♪')
                .fontSize(16)
                .fontColor(T.FG_MUTE)
            }
          }
          .width(40)
          .height(40)

          Text(this.title == '' ? '未在播放' : this.title)
            .layoutWeight(1)
            .fontSize(13)
            .fontWeight(FontWeight.Medium)
            .fontColor(T.FG)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })

          Row({ space: 3 }) {
            this.winButton('—', false, () => {
              this.doMinimize();
            })
            this.winButton('□', false, () => {
              // 小窗不提供放大：最大化按钮直接回到主界面
              this.close();
            })
            this.winButton('×', true, () => {
              this.close();
            })
          }
          .alignItems(VerticalAlign.Center)
        }
        .width('100%')
        .height(46)
        .alignItems(VerticalAlign.Center)
        .gesture(
          PanGesture({ direction: PanDirection.All, distance: 5 })
            .onActionStart(() => {
              WindowUtil.beginDrag(this.ctx());
            })
            .onActionUpdate((event: GestureEvent) => {
              WindowUtil.dragBy(this.ctx(), event.offsetX, event.offsetY);
            })
            .onActionEnd(() => {
              WindowUtil.endDrag();
            })
        )

        // 进度条 + 时间（与 Windows 版一致：时间在右侧）
        Row({ space: 8 }) {
          Slider({
            value: this.dragging ? this.dragValue : this.pos,
            min: 0,
            max: this.dur > 0 ? this.dur : 1,
            step: 500,
            style: SliderStyle.OutSet
          })
            .layoutWeight(1)
            .selectedColor(T.CYAN)
            .trackColor(T.LINE)
            .blockColor(T.CYAN)
            .onChange((v: number, mode: SliderChangeMode) => {
              if (mode == SliderChangeMode.Begin) {
                this.dragging = true;
              }
              this.dragValue = v;
              if (mode == SliderChangeMode.End || mode == SliderChangeMode.Click) {
                MiniBridge.sendCommand('seek', v);
                this.dragging = false;
              }
            })
          Text(`${fmtDuration(this.dragging ? this.dragValue : this.pos)} / ${fmtDuration(this.dur)}`)
            .width(82)
            .fontSize(9)
            .fontColor(T.FG_MUTE)
            .textAlign(TextAlign.End)
        }
        .width('100%')
        .height(22)

        // 播放控制 + 音量（单行，横向与 Windows 版一致）
        Row() {
          Row({ space: 4 }) {
            Button() {
              Text(MODE_GLYPHS[this.mode >= 0 && this.mode < 4 ? this.mode : 0])
                .fontSize(13)
                .fontColor(T.FG_DIM)
            }
            .width(26)
            .height(26)
            .padding(0)
            .borderRadius(7)
            .backgroundColor(Color.Transparent)
            .hoverEffect(HoverEffect.Scale)
            .onClick(() => {
              MiniBridge.sendCommand('mode', 0);
            })
            this.flatButton('◁', 28, T.FG_DIM, () => {
              MiniBridge.sendCommand('prev', 0);
            })
            Button() {
              if (this.playing) {
                Row({ space: 4 }) {
                  Column().width(3.5).height(14).borderRadius(1).backgroundColor(T.ON_ACCENT)
                  Column().width(3.5).height(14).borderRadius(1).backgroundColor(T.ON_ACCENT)
                }
              } else {
                Text('▷')
                  .fontSize(16)
                  .fontColor(T.ON_ACCENT)
              }
            }
            .width(38)
            .height(38)
            .padding(0)
            .borderRadius(19)
            .backgroundColor(T.ACCENT)
            .border({ width: 1, color: T.ACCENT_HOVER })
            .shadow({ radius: 10, color: T.ACCENT_SHADOW, offsetY: 1 })
            .hoverEffect(HoverEffect.Scale)
            .onClick(() => {
              MiniBridge.sendCommand('toggle', 0);
            })
            this.flatButton('■', 28, T.FG_DIM, () => {
              MiniBridge.sendCommand('stop', 0);
            })
            this.flatButton('▷', 28, T.FG_DIM, () => {
              MiniBridge.sendCommand('next', 0);
            })
          }

          Blank()

          Row({ space: 5 }) {
            Button(this.muted ? '静' : '音')
              .width(22)
              .height(20)
              .padding(0)
              .borderRadius(6)
              .fontSize(8)
              .fontColor(this.muted ? T.BAD : T.FG_MUTE)
              .backgroundColor(Color.Transparent)
              .border({ width: 1, color: this.muted ? T.DANGER_BORDER : T.LINE_SOFT })
              .hoverEffect(HoverEffect.Scale)
              .onClick(() => {
                MiniBridge.sendCommand('mute', 0);
              })
            Slider({
              value: this.volume,
              min: 0,
              max: 100,
              step: 1,
              style: SliderStyle.OutSet
            })
              .width(104)
              .selectedColor(T.ACCENT)
              .trackColor(T.LINE_SOFT)
              .blockColor(T.FG_DIM)
              .onChange((v: number, mode: SliderChangeMode) => {
                if (mode == SliderChangeMode.Begin) {
                  this.volumeDragging = true;
                }
                this.volume = Math.round(v);
                MiniBridge.sendCommand('volume', this.volume);
                if (mode == SliderChangeMode.End || mode == SliderChangeMode.Click) {
                  this.volumeDragging = false;
                }
              })
          }
        }
        .width('100%')
        .height(42)

        // 三行歌词，当前行高亮发光
        Column({ space: 5 }) {
          Text(this.lyricPrev == '' ? ' ' : this.lyricPrev)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .width('100%')
            .textAlign(TextAlign.Center)
          Text(this.lyricCur == '' ? '♪ 暂无歌词' : this.lyricCur)
            .fontSize(13)
            .fontWeight(FontWeight.Medium)
            .fontColor(this.lyricCur == '' ? T.FG_MUTE : T.ACCENT)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .width('100%')
            .textAlign(TextAlign.Center)
            .textShadow({
              radius: this.lyricCur == '' ? 0 : 10,
              color: T.ACCENT_SHADOW,
              offsetX: 0,
              offsetY: 0
            })
          Text(this.lyricNext == '' ? ' ' : this.lyricNext)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .width('100%')
            .textAlign(TextAlign.Center)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)

        // 底部透明度（Windows 版位于左下角）
        Row({ space: 8 }) {
          Text('透明度')
            .width(40)
            .fontSize(9)
            .fontColor(T.FG_MUTE)
          Slider({
            value: this.uiOpacity,
            min: 25,
            max: 100,
            step: 1,
            style: SliderStyle.OutSet
          })
            .width(170)
            .selectedColor(T.CYAN)
            .trackColor(T.LINE_SOFT)
            .blockColor(T.CYAN)
            .onChange((v: number, mode: SliderChangeMode) => {
              if (mode == SliderChangeMode.Begin) {
                this.opacityDragging = true;
              }
              this.uiOpacity = Math.round(v);
              MiniBridge.sendCommand('opacity', this.uiOpacity);
              if (mode == SliderChangeMode.End || mode == SliderChangeMode.Click) {
                this.opacityDragging = false;
              }
            })
          Text(`${Math.round(this.uiOpacity)}%`)
            .width(34)
            .fontSize(9)
            .fontColor(T.FG_MUTE)
          Blank()
        }
        .width('100%')
        .height(24)
      }
      .width('100%')
      .height('100%')
      .padding({ left: 14, right: 12, top: 6, bottom: 8 })
      .backgroundColor(T.alpha(T.PANEL, 'F2'))
      .borderRadius(14)
      .border({ width: 1.2, color: this.glowBorder() })
      .shadow({
        radius: 14 + this.glow * 16,
        color: T.alpha(T.ACCENT, '44'),
        offsetX: 0,
        offsetY: 0
      })
    }
    .width('100%')
    .height('100%')
    .padding(8)
    .backgroundColor(Color.Transparent)
    .opacity(this.uiOpacity / 100)
    .animation({ duration: 140, curve: Curve.EaseOut })
  }
}
