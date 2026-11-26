import { router } from '@kit.ArkUI';
import { T } from '../theme/Tokens';
import { EqPreset, EqPresets, EqState } from '../eq/EqPresets';
import { NativePlayer } from '../player/NativePlayer';
import { AppLog } from '../utils/AppLog';
import { WindowUtil } from '../utils/WindowUtil';

const BAR_H = 190;

interface DspSelfTest {
  band: number;
  gainDb: number;
  peak: number;
  passthrough: boolean;
  flat: boolean;
  bands: number;
}

@Entry
@Component
struct EqPage {
  @State eqOn: boolean = false;
  @State gains: number[] = [];
  @State presetName: string = '平坦';
  @State presets: EqPreset[] = [];
  @State message: string = '';
  @State selfTestText: string = '';
  @State nativeReady: boolean = true;
  private applyTimer: number = -1;
  private saveTimer: number = -1;
  private curveSettings: RenderingContextSettings = new RenderingContextSettings(true);
  private curveCtx: CanvasRenderingContext2D = new CanvasRenderingContext2D(this.curveSettings);

  aboutToAppear(): void {
    this.initAsync();
  }

  aboutToDisappear(): void {
    this.flushTimers();
  }

  private async initAsync(): Promise<void> {
    const state = await EqPresets.load();
    this.eqOn = state.enabled;
    this.gains = state.gains.slice();
    this.presetName = state.currentName;
    this.presets = await EqPresets.allPresets();
    this.runSelfTest();
  }

  private runSelfTest(): void {
    const raw = NativePlayer.selfTest();
    if (raw == '') {
      this.nativeReady = false;
      this.selfTestText = '原生内核未加载：当前为 AVPlayer 降级路径，EQ 不可用';
      return;
    }
    this.nativeReady = true;
    try {
      const r = JSON.parse(raw) as DspSelfTest;
      const ok = r.gainDb > 11.0 && r.gainDb < 13.0 && r.peak <= 1.0 && r.passthrough && r.flat;
      this.selfTestText = `内核自检：1kHz +12dB → ${r.gainDb.toFixed(2)}dB，限幅峰值 ${r.peak.toFixed(4)}，` +
        `关闭/全平直通${r.passthrough && r.flat ? '正常' : '异常'} ${ok ? '✓' : '✗'}`;
    } catch (e) {
      this.selfTestText = `内核自检返回异常：${raw}`;
    }
  }

  private drawCurve(): void {
    const w = this.curveCtx.width;
    const h = this.curveCtx.height;
    if (w <= 0 || h <= 0) {
      return;
    }
    this.curveCtx.clearRect(0, 0, w, h);
    const padX = 10;
    const padY = 10;
    const mid = h / 2;
    this.curveCtx.lineWidth = 1;
    this.curveCtx.strokeStyle = 'rgba(38, 52, 74, 0.75)';
    for (let i = 0; i <= 4; i++) {
      const y = padY + i * (h - padY * 2) / 4;
      this.curveCtx.beginPath();
      this.curveCtx.moveTo(padX, y);
      this.curveCtx.lineTo(w - padX, y);
      this.curveCtx.stroke();
    }
    this.curveCtx.strokeStyle = 'rgba(110, 168, 232, 0.55)';
    this.curveCtx.beginPath();
    this.curveCtx.moveTo(padX, mid);
    this.curveCtx.lineTo(w - padX, mid);
    this.curveCtx.stroke();

    const amp = (h / 2 - padY) / EqPresets.MAX_DB;
    this.curveCtx.beginPath();
    for (let i = 0; i < this.gains.length; i++) {
      const x = padX + i * (w - padX * 2) / (this.gains.length - 1);
      const y = mid - this.gains[i] * amp;
      if (i == 0) {
        this.curveCtx.moveTo(x, y);
      } else {
        this.curveCtx.lineTo(x, y);
      }
    }
    this.curveCtx.strokeStyle = T.CYAN;
    this.curveCtx.lineWidth = 2;
    this.curveCtx.stroke();

    this.curveCtx.lineTo(w - padX, mid);
    this.curveCtx.lineTo(padX, mid);
    this.curveCtx.closePath();
    this.curveCtx.fillStyle = 'rgba(78, 201, 190, 0.10)';
    this.curveCtx.fill();
  }

  private applyNow(): void {
    NativePlayer.setEq(this.eqOn, this.gains);
  }

  private scheduleApply(persist: boolean): void {
    if (this.applyTimer >= 0) {
      clearTimeout(this.applyTimer);
      this.applyTimer = -1;
    }
    this.applyTimer = setTimeout(() => {
      this.applyTimer = -1;
      this.applyNow();
    }, 60);
    if (this.saveTimer >= 0) {
      clearTimeout(this.saveTimer);
      this.saveTimer = -1;
    }
    if (persist) {
      this.saveTimer = setTimeout(() => {
        this.saveTimer = -1;
        EqPresets.save(this.currentState());
      }, 600);
    }
  }

  private flushTimers(): void {
    if (this.applyTimer >= 0) {
      clearTimeout(this.applyTimer);
      this.applyTimer = -1;
    }
    if (this.saveTimer >= 0) {
      clearTimeout(this.saveTimer);
      this.saveTimer = -1;
    }
    this.applyNow();
    EqPresets.save(this.currentState());
  }

  private currentState(): EqState {
    const s = new EqState();
    s.enabled = this.eqOn;
    s.currentName = this.presetName;
    s.gains = this.gains.slice();
    return s;
  }

  private toggleOn(on: boolean): void {
    this.eqOn = on;
    this.applyNow();
    EqPresets.save(this.currentState());
    this.message = on ? '均衡器已启用' : '均衡器已关闭（PCM 直通）';
    AppLog.info(this.message);
  }

  private pickPreset(p: EqPreset): void {
    this.presetName = p.name;
    this.gains = p.gains.slice();
    this.applyNow();
    EqPresets.save(this.currentState());
    this.message = `已切换到预设「${p.name}」`;
    this.drawCurve();
    AppLog.info(this.message);
  }

  private resetFlat(): void {
    this.presetName = '平坦';
    this.gains = EqPresets.flat().gains.slice();
    this.applyNow();
    EqPresets.save(this.currentState());
    this.message = '已重置为平坦';
    this.drawCurve();
  }

  private async saveCustom(): Promise<void> {
    await EqPresets.saveCustom(this.gains);
    this.presetName = EqPresets.CUSTOM_NAME;
    this.presets = await EqPresets.allPresets();
    EqPresets.save(this.currentState());
    this.message = '已保存为「自定义」预设';
  }

  private bandDb(i: number): number {
    return i >= 0 && i < this.gains.length ? this.gains[i] : 0;
  }

  private fillHeight(i: number): number {
    return Math.abs(this.bandDb(i)) / EqPresets.MAX_DB * (BAR_H / 2);
  }

  private fillOffset(i: number): number {
    const h = this.fillHeight(i);
    return this.bandDb(i) >= 0 ? -h / 2 : h / 2;
  }

  private gainText(i: number): string {
    const v = this.bandDb(i);
    if (Math.abs(v) < 0.05) {
      return '0';
    }
    const s = Math.abs(v) % 1 == 0 ? Math.abs(v).toFixed(0) : Math.abs(v).toFixed(1);
    return (v > 0 ? '+' : '-') + s;
  }

  private freqLabel(i: number): string {
    const hz = EqPresets.BANDS_HZ[i];
    if (hz >= 1000) {
      const k = hz / 1000;
      return (k % 1 == 0 ? k.toFixed(0) : k.toFixed(1)) + 'k';
    }
    return `${hz}`;
  }

  private onBandTouch(i: number, e: TouchEvent, height: number): void {
    if (e.type != TouchType.Down && e.type != TouchType.Move) {
      return;
    }
    if (e.touches.length == 0) {
      return;
    }
    const ratio = 1 - Math.max(0, Math.min(1, e.touches[0].y / height));
    const db = Math.round((ratio * 2 * EqPresets.MAX_DB - EqPresets.MAX_DB) * 2) / 2;
    if (db == this.bandDb(i)) {
      return;
    }
    this.gains[i] = EqPresets.clamp(db);
    if (this.presetName != EqPresets.CUSTOM_NAME) {
      this.presetName = EqPresets.CUSTOM_NAME;
    }
    this.drawCurve();
    this.scheduleApply(true);
  }

  @Builder
  bandColumn(i: number) {
    Column() {
      Text(this.gainText(i))
        .fontSize(9)
        .fontColor(Math.abs(this.bandDb(i)) > 0.05 ? T.CYAN : T.FG_MUTE)
        .height(12)
      Stack({ alignContent: Alignment.Center }) {
        Column()
          .width(14)
          .height(BAR_H)
          .borderRadius(7)
          .backgroundColor(T.INPUT)
        Divider()
          .width(24)
          .strokeWidth(1)
          .color(T.LINE)
        if (Math.abs(this.bandDb(i)) > 0.05) {
          Column()
            .width(14)
            .height(this.fillHeight(i))
            .borderRadius(7)
            .backgroundColor(T.CYAN)
            .offset({ y: this.fillOffset(i) })
        }
      }
      .width(28)
      .height(BAR_H)
      .onTouch((e: TouchEvent) => {
        this.onBandTouch(i, e, BAR_H);
      })
      Text(this.freqLabel(i))
        .fontSize(8)
        .fontColor(T.FG_MUTE)
        .height(11)
        .margin({ top: 4 })
    }
    .width(36)
  }

  build() {
    Column() {
      Row({ space: 12 }) {
        Column({ space: 2 }) {
          Text('均衡器')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text('31 段 · 峰值 ±12 dB · Q=1.41')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text(this.nativeReady ? '原生直通' : 'AVPlayer 降级')
          .fontSize(10)
          .fontColor(this.nativeReady ? T.OK : T.WARN)
        Button('返回')
          .height(30)
          .fontSize(11)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(8)
          .onClick(() => {
            this.flushTimers();
            WindowUtil.closeSelf('pages/EqPage');
          })
      }
      .width('100%')

      Column() {
        Column({ space: 10 }) {
        Row() {
          Text('启用均衡器')
            .fontSize(12)
            .fontColor(T.FG_DIM)
          Toggle({ type: ToggleType.Switch, isOn: this.eqOn })
            .margin({ left: 10 })
            .selectedColor(T.ACCENT)
            .enabled(this.nativeReady)
            .onChange((on: boolean) => {
              this.toggleOn(on);
            })
          Blank()
          Text(this.message)
            .fontSize(10)
            .fontColor(T.ACCENT)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
        .width('100%')

        Flex({ wrap: FlexWrap.Wrap }) {
          ForEach(this.presets, (p: EqPreset) => {
            Button(p.name)
              .fontSize(10)
              .height(26)
              .margin({ right: 8, top: 6 })
              .backgroundColor(this.presetName == p.name ? T.CHIP_ON_BG : T.GHOST_BG)
              .fontColor(this.presetName == p.name ? T.ACCENT : T.FG_DIM)
              .border({
                width: 1,
                color: this.presetName == p.name ? T.ACCENT_DIM : T.LINE_SOFT
              })
              .borderRadius(13)
              .onClick(() => {
                this.pickPreset(p);
              })
          }, (p: EqPreset) => p.name)
          Button('重置平坦')
            .fontSize(10)
            .height(26)
            .margin({ right: 8, top: 6 })
            .backgroundColor(T.GHOST_BG)
            .fontColor(T.FG_DIM)
            .borderRadius(13)
            .onClick(() => {
              this.resetFlat();
            })
          Button('存为自定义')
            .fontSize(10)
            .height(26)
            .margin({ top: 6 })
            .backgroundColor(T.CHIP_ON_BG)
            .fontColor(T.ACCENT)
            .borderRadius(13)
            .enabled(this.nativeReady)
            .onClick(() => {
              this.saveCustom();
            })
        }
        .width('100%')

        Row() {
          Text('频率响应')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
          Blank()
          Text('±12 dB')
            .fontSize(9)
            .fontColor(T.FG_MUTE)
        }
        .width('100%')

        Canvas(this.curveCtx)
          .width('100%')
          .height(96)
          .backgroundColor(T.DOCK_BG)
          .borderRadius(8)
          .border({ width: 1, color: T.LINE_SOFT })
          .onReady(() => {
            this.drawCurve();
          })

        Row() {
          Scroll() {
            Row() {
              ForEach(EqPresets.BANDS_HZ, (hz: number, i: number) => {
                this.bandColumn(i);
              }, (hz: number, i: number) => `${hz}-${i}`)
            }
            .alignItems(VerticalAlign.Top)
            .padding({ left: 4, right: 4, top: 8, bottom: 6 })
          }
          .scrollable(ScrollDirection.Horizontal)
          .scrollBar(BarState.Off)
          .layoutWeight(1)
          .height(BAR_H + 44)
        }
        .width('100%')
        .padding({ top: 4, bottom: 4 })
        .backgroundColor(T.DOCK_BG)
        .borderRadius(T.R_CARD)
        .border({ width: 1, color: T.LINE_SOFT })

        Text('拖动柱体上下移动调节增益；关闭开关后 PCM 直通不经过 DSP。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
        Text(this.selfTestText)
          .fontSize(9)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')
      .padding(14)
      .backgroundColor(T.PANEL_B3)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .margin({ top: 12 })
      .alignItems(HorizontalAlign.Start)
      }
      .width('100%')
      .layoutWeight(1)
      .justifyContent(FlexAlign.Center)
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 12, bottom: 10 })
    .backgroundColor(T.BASE)
    .alignItems(HorizontalAlign.Start)
  }
}
