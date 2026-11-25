import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { PlayerEngine, MODE_NAMES, PlayMode } from '../player/PlayerEngine';
import { NativePlayer } from '../player/NativePlayer';
import { fmtDuration, fmtSpec, fmtSize } from '../utils/Util';
import { Db } from '../db/Db';
import { CoverService } from '../cover/CoverService';

const ACCENT = '#E8B04B';

@Entry
@Component
struct PlayerPage {
  @StorageLink('npPlaying') npPlaying: boolean = false;
  @StorageLink('npPos') npPos: number = 0;
  @StorageLink('npDur') npDur: number = 0;
  @State dragValue: number = 0;
  @State dragging: boolean = false;
  @State modeIdx: number = 1;
  @State vol: number = 100;
  @State toast: string = '';
  @State eqOn: boolean = false;
  @State coverPath: string = '';
  private engine: PlayerEngine = PlayerEngine.get();
  private probedTrackId: number = -1;
  // 跟随曲目切换刷新封面（引擎每次 tick 都会回调，值不变时不触发重绘）
  private onEngineTick = (): void => {
    const next = this.resolveCover();
    if (next != this.coverPath) {
      this.coverPath = next;
    }
    const cur = this.engine.current();
    if (cur && next == '' && cur.id != this.probedTrackId) {
      // 换了曲子且没有缓存封面：试着从文件内嵌图里补一份
      this.probedTrackId = cur.id;
      this.probeEmbeddedCover();
    }
  };

  private async probeEmbeddedCover(): Promise<void> {
    const t = this.engine.current();
    if (!t || t.coverBlobPath != '') {
      return;
    }
    const ctx = getContext(this) as common.UIAbilityContext;
    const path = await CoverService.ensureCover(ctx, t);
    if (path == '') {
      return;
    }
    // 同步内存里的队列对象：切回来也不会再显示占位图
    t.coverBlobPath = path;
    const cur = this.engine.current();
    if (cur && cur.path == t.path) {
      this.coverPath = `file://${path}`;
    }
  }

  private resolveCover(): string {
    const t = this.engine.current();
    if (!t || t.coverBlobPath == '') {
      return '';
    }
    return `file://${t.coverBlobPath}`;
  }

  aboutToAppear(): void {
    this.modeIdx = this.engine.mode as number;
    this.vol = Math.round(this.engine.volume * 100);
    this.eqOn = NativePlayer.eqEnabled();
    this.coverPath = this.resolveCover();
    this.engine.addListener(this.onEngineTick);
    if (this.coverPath == '') {
      const cur = this.engine.current();
      if (cur) {
        this.probedTrackId = cur.id;
      }
      this.probeEmbeddedCover();
    }
  }

  aboutToDisappear(): void {
    this.engine.removeListener(this.onEngineTick);
  }

  onPageShow(): void {
    this.eqOn = NativePlayer.eqEnabled();
    this.coverPath = this.resolveCover();
  }

  private toastMsg(msg: string): void {
    this.toast = msg;
    setTimeout(() => {
      if (this.toast == msg) {
        this.toast = '';
      }
    }, 4000);
  }

  private cycleMode(): void {
    const next = ((this.engine.mode as number) + 1) % 4;
    this.engine.setMode(next as PlayMode);
    this.modeIdx = next;
    Db.setSetting('play_mode', `${next}`);
    this.toastMsg(`播放模式：${MODE_NAMES[next]}`);
  }

  private sliderValue(): number {
    if (this.dragging) {
      return this.dragValue;
    }
    return this.npDur > 0 ? this.npPos : 0;
  }

  private sliderMax(): number {
    return this.npDur > 0 ? this.npDur : 1;
  }

  build() {
    Column() {
      // 顶栏
      Row() {
        Text('‹ 返回')
          .fontSize(15)
          .fontColor('#C8C8CE')
          .onClick(() => {
            router.back();
          })
        Blank()
        Text('正在播放')
          .fontSize(14)
          .fontColor('#8A8A92')
        Blank()
        Text('歌词')
          .fontSize(13)
          .fontColor(ACCENT)
          .onClick(() => {
            const cur = this.engine.current();
            if (cur) {
              AppStorage.setOrCreate('seqaudio_lyrics_path', JSON.stringify([cur.path]));
              router.pushUrl({ url: 'pages/LyricsPage' });
            }
          })
        Text('EQ')
          .fontSize(13)
          .fontColor(this.eqOn ? ACCENT : '#8A8A92')
          .margin({ left: 14 })
          .onClick(() => {
            router.pushUrl({ url: 'pages/EqPage' });
          })
        Text(MODE_NAMES[this.modeIdx])
          .fontSize(13)
          .fontColor(ACCENT)
          .margin({ left: 14 })
          .onClick(() => {
            this.cycleMode();
          })
      }
        .width('100%')
        .height(44)

      // 封面：有缓存图就用缓存，否则保留占位
      Stack() {
        Column()
          .width(240)
          .height(240)
          .borderRadius(16)
          .linearGradient({ angle: 135, colors: [['#3A3A46', 0.0], ['#1C1C22', 1.0]] })
        if (this.coverPath != '') {
          Image(this.coverPath)
            .width(240)
            .height(240)
            .borderRadius(16)
            .objectFit(ImageFit.Cover)
            .onError(() => {
              console.error(`cover image load fail: ${this.coverPath}`);
              this.coverPath = '';
            })
        } else {
          Text('♪')
            .fontSize(84)
            .fontColor(ACCENT)
        }
      }
        .margin({ top: 28, bottom: 28 })

      // 标题区
      Text(this.curTitle())
        .fontSize(20)
        .fontWeight(FontWeight.Medium)
        .fontColor('#F5F5F7')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
        .textAlign(TextAlign.Center)
      Text(this.curSub())
        .fontSize(13)
        .fontColor('#9A9AA0')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
        .textAlign(TextAlign.Center)
        .margin({ top: 6 })

      // 进度条
      Slider({
        value: this.sliderValue(),
        min: 0,
        max: this.sliderMax(),
        step: 500,
        style: SliderStyle.OutSet
      })
        .width('100%')
        .margin({ top: 30 })
        .selectedColor(ACCENT)
        .trackColor('#2E2E36')
        .onChange((v: number, mode: SliderChangeMode) => {
          if (mode == SliderChangeMode.Begin) {
            this.dragging = true;
          }
          this.dragValue = v;
          if (mode == SliderChangeMode.End || mode == SliderChangeMode.Click) {
            this.engine.seek(v);
            this.dragging = false;
          }
        })
      Row() {
        Text(fmtDuration(this.dragging ? this.dragValue : this.npPos)).fontSize(11).fontColor('#8A8A92')
        Blank()
        Text(fmtDuration(this.npDur)).fontSize(11).fontColor('#8A8A92')
      }
        .width('100%')

      // 控制区
      Row({ space: 28 }) {
        Button() {
          Image($r('app.media.ic_prev')).width(22).height(22)
        }
          .width(52)
          .height(52)
          .type(ButtonType.Circle)
          .backgroundColor('#2A2A30')
          .onClick(() => {
            this.engine.prevByUser();
          })

        Button() {
          if (this.npPlaying) {
            Row({ space: 5 }) {
              Column().width(5).height(20).backgroundColor('#1A1A1C')
              Column().width(5).height(20).backgroundColor('#1A1A1C')
            }
          } else {
            Image($r('app.media.ic_play')).width(26).height(26).fillColor('#1A1A1C')
          }
        }
          .width(68)
          .height(68)
          .type(ButtonType.Circle)
          .backgroundColor(ACCENT)
          .onClick(() => {
            this.engine.toggle();
          })

        Button() {
          Image($r('app.media.ic_next')).width(22).height(22)
        }
          .width(52)
          .height(52)
          .type(ButtonType.Circle)
          .backgroundColor('#2A2A30')
          .onClick(() => {
            this.engine.nextByUser();
          })
      }
        .margin({ top: 26 })

      // 音量
      Row({ space: 10 }) {
        Text('音量').fontSize(12).fontColor('#8A8A92')
        Slider({
          value: this.vol,
          min: 0,
          max: 100,
          step: 1,
          style: SliderStyle.OutSet
        })
          .layoutWeight(1)
          .selectedColor(ACCENT)
          .trackColor('#2E2E36')
          .onChange((v: number, mode: SliderChangeMode) => {
            if (mode == SliderChangeMode.End || mode == SliderChangeMode.Click) {
              this.vol = v;
              this.engine.setVolume(v / 100);
              Db.setSetting('volume', (v / 100).toFixed(2));
            }
          })
        Text(`${this.vol}`).fontSize(12).fontColor('#8A8A92').width(28).textAlign(TextAlign.End)
      }
        .width('100%')
        .margin({ top: 26, left: 8, right: 8 })

      // 规格信息
      Text(this.specLine())
        .fontSize(11)
        .fontColor('#6E6E76')
        .margin({ top: 18 })
        .maxLines(1)

      Blank()

      Text(this.toast)
        .fontSize(12)
        .fontColor(ACCENT)
        .height(this.toast == '' ? 0 : 20)
        .margin({ bottom: 12 })
    }
      .width('100%')
      .height('100%')
      .padding({ left: 28, right: 28, top: 10, bottom: 16 })
      .backgroundColor('#111114')
      .alignItems(HorizontalAlign.Center)
  }

  private specLine(): string {
    const t = this.engine.current();
    if (!t) {
      return '';
    }
    return `${fmtSpec(t)} · ${t.channels > 0 ? `${t.channels}ch` : '--'} · ${fmtSize(t.sizeBytes)} · ${t.fileName}`;
  }

  private curTitle(): string {
    const t = this.engine.current();
    return t ? t.title : '未在播放';
  }

  private curSub(): string {
    const t = this.engine.current();
    if (!t) {
      return '';
    }
    if (t.artist == '' && t.album == '') {
      return t.fileName;
    }
    if (t.album == '') {
      return t.artist;
    }
    if (t.artist == '') {
      return t.album;
    }
    return `${t.artist} · ${t.album}`;
  }
}
