import { router } from '@kit.ArkUI';
import { EqPreset, EqPresets, EqState } from '../eq/EqPresets';
import { NativePlayer } from '../player/NativePlayer';

const ACCENT = '#E8B04B';
const BAR_H = 200;

interface DspSelfTest {
  band: number;
  gainDb: number;
  peak: number;
  passthrough: boolean;
  flat: boolean;
  bands: number;
}

// 31 段图形均衡器：拖动柱体调增益，改动即下发原生 DSP（≤1 个音频回调生效）
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

  private applyNow(): void {
    NativePlayer.setEq(this.eqOn, this.gains);
  }

  // 拖动时 60ms 合并下发；落盘再慢一拍，避免频繁写 settings
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
  }

  private pickPreset(p: EqPreset): void {
    this.presetName = p.name;
    this.gains = p.gains.slice();
    this.applyNow();
    EqPresets.save(this.currentState());
    this.message = `已切换到预设「${p.name}」`;
  }

  private resetFlat(): void {
    this.presetName = '平坦';
    this.gains = EqPresets.flat().gains.slice();
    this.applyNow();
    EqPresets.save(this.currentState());
    this.message = '已重置为平坦';
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
    this.scheduleApply(true);
  }

  @Builder
  bandColumn(i: number) {
    Column() {
      Text(this.gainText(i))
        .fontSize(9)
        .fontColor(Math.abs(this.bandDb(i)) > 0.05 ? ACCENT : '#6E6E76')
        .height(12)
      Stack({ alignContent: Alignment.Center }) {
        Column()
          .width(16)
          .height(BAR_H)
          .borderRadius(8)
          .backgroundColor('#1B1B21')
        Divider()
          .width(26)
          .strokeWidth(1)
          .color('#3A3A44')
        if (Math.abs(this.bandDb(i)) > 0.05) {
          Column()
            .width(16)
            .height(this.fillHeight(i))
            .borderRadius(8)
            .backgroundColor(ACCENT)
            .offset({ y: this.fillOffset(i) })
        }
      }
      .width(30)
      .height(BAR_H)
      .onTouch((e: TouchEvent) => {
        this.onBandTouch(i, e, BAR_H);
      })
      Text(this.freqLabel(i))
        .fontSize(8)
        .fontColor('#7A7A84')
        .height(11)
        .margin({ top: 4 })
    }
    .width(38)
  }

  build() {
    Column() {
      Row() {
        Text('均衡器')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .fontColor('#F5F5F7')
        Blank()
        Text(this.nativeReady ? '31 段 · Q=1.41 · ±12dB' : 'AVPlayer 降级')
          .fontSize(11)
          .fontColor(this.nativeReady ? '#8A8A92' : '#C86A5A')
        Button('返回')
          .fontSize(12)
          .height(28)
          .margin({ left: 12 })
          .backgroundColor('#2A2A30')
          .fontColor('#F0F0F2')
          .onClick(() => {
            this.flushTimers();
            router.back();
          })
      }
      .width('100%')

      Row() {
        Text('启用均衡器')
          .fontSize(14)
          .fontColor('#D8D8DC')
        Toggle({ type: ToggleType.Switch, isOn: this.eqOn })
          .margin({ left: 10 })
          .selectedColor(ACCENT)
          .enabled(this.nativeReady)
          .onChange((on: boolean) => {
            this.toggleOn(on);
          })
        Blank()
        Text(this.message)
          .fontSize(11)
          .fontColor(ACCENT)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      .margin({ top: 10 })

      Flex({ wrap: FlexWrap.Wrap }) {
        ForEach(this.presets, (p: EqPreset) => {
          Button(p.name)
            .fontSize(11)
            .height(26)
            .margin({ right: 8, top: 8 })
            .backgroundColor(this.presetName == p.name ? '#3A2F1C' : '#22222A')
            .fontColor(this.presetName == p.name ? ACCENT : '#B8B8C0')
            .onClick(() => {
              this.pickPreset(p);
            })
        }, (p: EqPreset) => p.name)
        Button('重置平坦')
          .fontSize(11)
          .height(26)
          .margin({ right: 8, top: 8 })
          .backgroundColor('#22222A')
          .fontColor('#B8B8C0')
          .onClick(() => {
            this.resetFlat();
          })
        Button('存为自定义')
          .fontSize(11)
          .height(26)
          .margin({ top: 8 })
          .backgroundColor('#26221A')
          .fontColor(ACCENT)
          .enabled(this.nativeReady)
          .onClick(() => {
            this.saveCustom();
          })
      }
      .width('100%')

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
      .width('100%')
      .height(BAR_H + 44)
      .margin({ top: 4 })

      Text('拖动柱体上下移动调节增益；关闭开关后 PCM 直通不经过 DSP')
        .fontSize(10)
        .fontColor('#6E6E76')
        .margin({ top: 8 })
      Text(this.selfTestText)
        .fontSize(10)
        .fontColor('#7F8A7A')
        .margin({ top: 4 })
      Blank()
    }
    .width('100%')
    .height('100%')
    .padding({ left: 24, right: 24, top: 18, bottom: 12 })
    .backgroundColor('#111114')
    .alignItems(HorizontalAlign.Start)
  }
}
