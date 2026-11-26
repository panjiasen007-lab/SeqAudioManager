// 右侧「参数」页：按桌面版 SettingsPanel 的卡片结构复刻，只保留鸿蒙可落地的设置。
import { T } from '../theme/Tokens';
import { ThemeStore } from '../theme/ThemeStore';
import { AppSettingsData } from '../settings/AppSettings';
import { SectionHeader } from './DesktopUI';

@Component
export struct SettingsPanel {
  @Prop settings: AppSettingsData = new AppSettingsData();
  onSave: (d: AppSettingsData) => void = () => {
  };
  onPreviewOpacity: (v: number) => void = () => {
  };
  onManageRoots: () => void = () => {
  };
  onOpenEq: () => void = () => {
  };
  @State private tab: number = 0;
  @State private backup: boolean = true;
  @State private autoLyrics: boolean = false;
  @State private uiOpacity: number = 95;
  @State private volume: number = 80;
  @State private playMode: number = 1;
  @State private useNetmusic: boolean = true;
  @State private useQqmusic: boolean = true;
  @State private useMusicBrainz: boolean = true;
  @State private useItunes: boolean = true;
  @State private useLrclib: boolean = true;
  @State private useCoverart: boolean = true;
  @State private onlineEnabled: boolean = true;
  @State private conflictMode: number = 2;
  @State private minDurationSec: number = 0;
  @State private threshold: number = 62;
  @State private autoCover: boolean = true;
  @State private autoRename: boolean = false;
  @State private renameTemplate: string = '{artist} - {title}';
  @State private cleanupIncludeSuspect: boolean = true;
  @State private theme: string = '深空';
  @State private trimPregap: boolean = false;
  @State private showAlbum: boolean = true;
  @State private showFormat: boolean = true;
  @State private showSpec: boolean = true;
  @State private showConfidence: boolean = true;
  @State private showDuration: boolean = true;
  @State private showFileName: boolean = true;
  @State private showYear: boolean = false;
  @State private showKind: boolean = false;
  @State private showSource: boolean = false;
  @State private dirty: boolean = false;
  @State private savedHint: string = '';

  aboutToAppear(): void {
    this.resetFromProps();
  }

  private resetFromProps(): void {
    this.backup = this.settings.backup;
    this.autoLyrics = this.settings.autoLyrics;
    this.uiOpacity = this.settings.opacity;
    this.volume = this.settings.volume;
    this.playMode = this.settings.playMode;
    this.useNetmusic = this.settings.useNetmusic;
    this.useQqmusic = this.settings.useQqmusic;
    this.useMusicBrainz = this.settings.useMusicBrainz;
    this.useItunes = this.settings.useItunes;
    this.useLrclib = this.settings.useLrclib;
    this.useCoverart = this.settings.useCoverart;
    this.onlineEnabled = this.settings.onlineEnabled;
    this.conflictMode = this.settings.conflictMode;
    this.minDurationSec = this.settings.minDurationSec;
    this.threshold = this.settings.threshold;
    this.autoCover = this.settings.autoCover;
    this.autoRename = this.settings.autoRename;
    this.renameTemplate = this.settings.renameTemplate;
    this.cleanupIncludeSuspect = this.settings.cleanupIncludeSuspect;
    this.theme = this.settings.theme;
    this.trimPregap = this.settings.cueSplitTrimPregap;
    this.showAlbum = this.settings.showAlbum;
    this.showFormat = this.settings.showFormat;
    this.showSpec = this.settings.showSpec;
    this.showConfidence = this.settings.showConfidence;
    this.showDuration = this.settings.showDuration;
    this.showFileName = this.settings.showFileName;
    this.showYear = this.settings.showYear;
    this.showKind = this.settings.showKind;
    this.showSource = this.settings.showSource;
    this.dirty = false;
    this.savedHint = '';
  }

  private markDirty(): void {
    this.dirty = true;
    this.savedHint = '';
  }

  private save(): void {
    const d = new AppSettingsData();
    d.backup = this.backup;
    d.autoLyrics = this.autoLyrics;
    d.opacity = this.uiOpacity;
    d.volume = this.volume;
    d.playMode = this.playMode;
    d.useNetmusic = this.useNetmusic;
    d.useQqmusic = this.useQqmusic;
    d.useMusicBrainz = this.useMusicBrainz;
    d.useItunes = this.useItunes;
    d.useLrclib = this.useLrclib;
    d.useCoverart = this.useCoverart;
    d.onlineEnabled = this.onlineEnabled;
    d.conflictMode = this.conflictMode;
    d.minDurationSec = this.minDurationSec;
    d.threshold = this.threshold;
    d.autoCover = this.autoCover;
    d.autoRename = this.autoRename;
    d.renameTemplate = this.renameTemplate;
    d.cleanupIncludeSuspect = this.cleanupIncludeSuspect;
    d.theme = this.theme;
    d.cueSplitTrimPregap = this.trimPregap;
    d.showAlbum = this.showAlbum;
    d.showFormat = this.showFormat;
    d.showSpec = this.showSpec;
    d.showConfidence = this.showConfidence;
    d.showDuration = this.showDuration;
    d.showFileName = this.showFileName;
    d.showYear = this.showYear;
    d.showKind = this.showKind;
    d.showSource = this.showSource;
    this.onSave(d);
    this.dirty = false;
    this.savedHint = '参数已保存并生效';
  }

  @Builder
  tabButton(label: string, index: number) {
    Text(label)
      .fontSize(11)
      .fontColor(this.tab == index ? T.ACCENT : T.FG_DIM)
      .padding({ left: 8, right: 8, top: 5, bottom: 5 })
      .border({
        width: { bottom: 2 },
        color: { bottom: this.tab == index ? T.ACCENT : Color.Transparent }
      })
      .onClick(() => {
        this.tab = index;
      })
  }

  @Builder
  cardTitle(title: string, hint: string, accent: string) {
    SectionHeader({ title: title, hint: hint, accent: accent })
  }

  private toggleValue(key: string): boolean {
    if (key == 'onlineEnabled') {
      return this.onlineEnabled;
    }
    if (key == 'backup') {
      return this.backup;
    }
    if (key == 'netmusic') {
      return this.useNetmusic;
    }
    if (key == 'qqmusic') {
      return this.useQqmusic;
    }
    if (key == 'musicbrainz') {
      return this.useMusicBrainz;
    }
    if (key == 'itunes') {
      return this.useItunes;
    }
    if (key == 'lrclib') {
      return this.useLrclib;
    }
    if (key == 'coverart') {
      return this.useCoverart;
    }
    if (key == 'autoCover') {
      return this.autoCover;
    }
    if (key == 'cleanupSuspect') {
      return this.cleanupIncludeSuspect;
    }
    if (key == 'autoRename') {
      return this.autoRename;
    }
    if (key == 'trimPregap') {
      return this.trimPregap;
    }
    if (key == 'colAlbum') {
      return this.showAlbum;
    }
    if (key == 'colFormat') {
      return this.showFormat;
    }
    if (key == 'colSpec') {
      return this.showSpec;
    }
    if (key == 'colConfidence') {
      return this.showConfidence;
    }
    if (key == 'colDuration') {
      return this.showDuration;
    }
    if (key == 'colFileName') {
      return this.showFileName;
    }
    if (key == 'colYear') {
      return this.showYear;
    }
    if (key == 'colKind') {
      return this.showKind;
    }
    if (key == 'colSource') {
      return this.showSource;
    }
    return this.autoLyrics;
  }

  private setToggle(key: string, v: boolean): void {
    if (key == 'onlineEnabled') {
      this.onlineEnabled = v;
    } else if (key == 'backup') {
      this.backup = v;
    } else if (key == 'netmusic') {
      this.useNetmusic = v;
    } else if (key == 'qqmusic') {
      this.useQqmusic = v;
    } else if (key == 'musicbrainz') {
      this.useMusicBrainz = v;
    } else if (key == 'itunes') {
      this.useItunes = v;
    } else if (key == 'lrclib') {
      this.useLrclib = v;
    } else if (key == 'coverart') {
      this.useCoverart = v;
    } else if (key == 'autoCover') {
      this.autoCover = v;
    } else if (key == 'cleanupSuspect') {
      this.cleanupIncludeSuspect = v;
    } else if (key == 'autoRename') {
      this.autoRename = v;
    } else if (key == 'trimPregap') {
      this.trimPregap = v;
    } else if (key == 'colAlbum') {
      this.showAlbum = v;
    } else if (key == 'colFormat') {
      this.showFormat = v;
    } else if (key == 'colSpec') {
      this.showSpec = v;
    } else if (key == 'colConfidence') {
      this.showConfidence = v;
    } else if (key == 'colDuration') {
      this.showDuration = v;
    } else if (key == 'colFileName') {
      this.showFileName = v;
    } else if (key == 'colYear') {
      this.showYear = v;
    } else if (key == 'colKind') {
      this.showKind = v;
    } else if (key == 'colSource') {
      this.showSource = v;
    } else {
      this.autoLyrics = v;
    }
    this.markDirty();
  }

  private numberValue(key: string): number {
    if (key == 'opacity') {
      return this.uiOpacity;
    }
    if (key == 'threshold') {
      return this.threshold;
    }
    if (key == 'minDuration') {
      return this.minDurationSec;
    }
    return this.volume;
  }

  private setNumber(key: string, v: number): void {
    if (key == 'opacity') {
      this.uiOpacity = v;
      this.onPreviewOpacity(v);
    } else if (key == 'threshold') {
      this.threshold = v;
    } else if (key == 'minDuration') {
      this.minDurationSec = v;
    } else {
      this.volume = v;
    }
    this.markDirty();
  }

  @Builder
  switchRow(label: string, key: string) {
    Row() {
      Text(label)
        .fontSize(11)
        .fontColor(T.FG_DIM)
        .layoutWeight(1)
      Toggle({ type: ToggleType.Switch, isOn: this.toggleValue(key) })
        .selectedColor(T.ACCENT)
        .onChange((v: boolean) => {
          this.setToggle(key, v);
        })
    }
    .width('100%')
    .height(30)
  }

  @Builder
  switchCell(label: string, key: string) {
    Row() {
      Text(label)
        .fontSize(10)
        .fontColor(T.FG_DIM)
        .maxLines(1)
        .layoutWeight(1)
      Toggle({ type: ToggleType.Switch, isOn: this.toggleValue(key) })
        .selectedColor(T.ACCENT)
        .onChange((v: boolean) => {
          this.setToggle(key, v);
        })
    }
    .width('50%')
    .height(30)
    .padding({ right: 8 })
  }

  @Builder
  sliderRow(label: string, key: string, min: number, max: number, suffix: string) {
    Column({ space: 2 }) {
      Row() {
        Text(label)
          .fontSize(11)
          .fontColor(T.FG_DIM)
          .layoutWeight(1)
        Text(`${Math.round(this.numberValue(key))}${suffix}`)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')
      Slider({
        value: this.numberValue(key),
        min: min,
        max: max,
        step: 1,
        style: SliderStyle.OutSet
      })
        .width('100%')
        .selectedColor(T.ACCENT)
        .trackColor(T.LINE_SOFT)
        .blockColor(T.CYAN)
        .onChange((v: number) => {
          this.setNumber(key, Math.round(v));
        })
    }
    .width('100%')
    .padding({ top: 4, bottom: 4 })
  }

  @Builder
  tabContent() {
    if (this.tab == 0) {
      Column({ space: 6 }) {
        this.cardTitle('联网匹配', '多源候选 + 置信度', T.CYAN)
        this.switchRow('启用联网匹配与歌词下载', 'onlineEnabled')
        Row() {
          Text('已有标签')
            .fontSize(11)
            .fontColor(T.FG_DIM)
            .layoutWeight(1)
          Select([
            { value: '不覆盖已有信息' },
            { value: '强制覆盖' },
            { value: '仅空白才填充' }
          ])
            .selected(this.conflictMode)
            .value('仅空白才填充')
            .width(180)
            .height(30)
            .font({ size: 10 })
            .fontColor(T.FG_DIM)
            .backgroundColor(T.INPUT)
            .borderRadius(T.R_INPUT)
            .onSelect((index: number) => {
              this.conflictMode = index;
              this.markDirty();
            })
        }
        .width('100%')
        .height(32)
        this.switchRow('网易云（中文覆盖优先）', 'netmusic')
        this.switchRow('QQ 音乐（兜底）', 'qqmusic')
        this.switchRow('MusicBrainz（海外/冷门）', 'musicbrainz')
        this.switchRow('iTunes（封面/欧美）', 'itunes')
        this.switchRow('lrclib 歌词', 'lrclib')
        this.switchRow('自动补封面', 'autoCover')
        this.sliderRow('自动应用阈值', 'threshold', 30, 95, '%')
        Text('低于阈值的候选进入「待确认」，不自动覆盖原标签；应用候选会写回标签并尽量补封面/歌词。')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .lineHeight(15)
        Divider().color(T.LINE_SOFT).margin({ top: 2, bottom: 2 })
        Button('管理扫描根目录…')
          .width('100%')
          .height(28)
          .fontSize(10)
          .fontColor(T.ACCENT)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(7)
          .onClick(() => {
            this.onManageRoots();
          })
      }
      .width('100%')
      .padding(12)
      .backgroundColor(T.CARD)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .alignItems(HorizontalAlign.Start)
    } else if (this.tab == 1) {
      Column({ space: 9 }) {
        this.cardTitle('歌词', '读取顺序', T.CYAN)
        this.switchRow('进入曲目时自动加载歌词', 'autoLyrics')
        Text('优先同目录 .lrc；其次曲库缓存；最后请求 lrclib。自动加载关闭时，仍可在底部歌词栏点击联网匹配。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
      }
      .width('100%')
      .padding(12)
      .backgroundColor(T.CARD)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .alignItems(HorizontalAlign.Start)
    } else if (this.tab == 2) {
      Column({ space: 9 }) {
        this.cardTitle('写入与备份', '失败自动回滚', T.OK)
        this.switchRow('写入前保留最近 1 份 .bak', 'backup')
        Text('标签写回采用「临时文件 → 校验 → 原子替换」；失败时保留原文件。超过 500 MB 的文件拒绝写入。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
        Divider().color(T.LINE_SOFT)
        this.switchRow('清理假无损时连同「疑似」一起隔离', 'cleanupSuspect')
        Text('关闭后只隔离判定为「假无损」的曲目；「疑似升频/假 DSD」会保留在库中。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
        Divider().color(T.LINE_SOFT)
        this.switchRow('全自动写回后按模板重命名', 'autoRename')
        Row({ space: 8 }) {
          Text('命名模板')
            .fontSize(11)
            .fontColor(T.FG_DIM)
            .width(70)
          TextInput({ text: this.renameTemplate, placeholder: '{artist} - {title}' })
            .layoutWeight(1)
            .height(28)
            .fontSize(10)
            .fontColor(T.FG)
            .backgroundColor(T.INPUT)
            .borderRadius(7)
            .onChange((v: string) => {
              this.renameTemplate = v;
              this.markDirty();
            })
        }
        .width('100%')
        Text('变量：{artist}/{title}/{album}/{year}/{track}，也支持中文 {歌手} {歌名} {专辑} {年份} {曲目}。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
        Divider().color(T.LINE_SOFT)
        this.switchRow('分轨在下一轨 INDEX 00 处截止', 'trimPregap')
        Text('开启后每轨输出到下一轨预间隙起点，去掉轨尾约 2 秒静音；若预间隙里检测到真实音频（隐藏曲/尾奏），会自动保留不裁。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
        Divider().color(T.LINE_SOFT)
        this.sliderRow('最短时长过滤', 'minDuration', 0, 60, ' 秒')
        Text('扫描时跳过短于该时长的音频（0 = 不过滤），用于过滤提示音/碎片文件。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
        Divider().color(T.LINE_SOFT)
        Text('支持写回：FLAC Vorbis Comment / MP3 ID3v2 / M4A ilst / WAV RIFF ID3；CUE 母文件只读。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
      }
      .width('100%')
      .padding(12)
      .backgroundColor(T.CARD)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .alignItems(HorizontalAlign.Start)
    } else if (this.tab == 3) {
      Column({ space: 9 }) {
        this.cardTitle('外观', '表格列与主题', T.PURPLE)
        this.cardTitle('表格列', '保存后生效', T.CYAN)
        Flex({ wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
          this.switchCell('显示专辑', 'colAlbum')
          this.switchCell('显示格式', 'colFormat')
          this.switchCell('显示规格', 'colSpec')
          this.switchCell('显示匹配置信度', 'colConfidence')
          this.switchCell('显示时长', 'colDuration')
          this.switchCell('显示文件名', 'colFileName')
          this.switchCell('显示年份', 'colYear')
          this.switchCell('显示无损/有损', 'colKind')
          this.switchCell('显示来源', 'colSource')
        }
        .width('100%')
        Text('窄窗口下「专辑 / 置信度 / 文件名」仍会自动隐藏；歌名、歌手、鉴定结果始终显示。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
        Divider().color(T.LINE_SOFT)
        Row() {
          Text('主题')
            .fontSize(11)
            .fontColor(T.FG_DIM)
            .layoutWeight(1)
          Text(this.theme)
            .fontSize(11)
            .fontColor(T.ACCENT)
        }
        .width('100%')
        .height(24)
        Flex({ wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
          ForEach(ThemeStore.NAMES, (name: string) => {
            Row({ space: 6 }) {
              Column()
                .width(10)
                .height(10)
                .borderRadius(5)
                .backgroundColor(ThemeStore.palette(name).accent)
                .border({ width: 1, color: T.alpha(ThemeStore.palette(name).onAccent, '55') })
              Text(name)
                .fontSize(10)
                .fontColor(this.theme == name ? ThemeStore.palette(name).onAccent : T.FG_DIM)
            }
            .padding({ left: 8, right: 8, top: 5, bottom: 5 })
            .margin({ right: 6, bottom: 6 })
            .backgroundColor(this.theme == name ? ThemeStore.palette(name).accent : T.GHOST_BG)
            .borderRadius(6)
            .border({
              width: 1,
              color: this.theme == name ? ThemeStore.palette(name).accentHover : T.LINE_SOFT
            })
            .onClick(() => {
              this.theme = name;
              this.markDirty();
            })
          }, (name: string) => name)
        }
        .width('100%')
        this.sliderRow('界面不透明度', 'opacity', 25, 100, '%')
        Text('保存参数后应用主题并重建工作台；浅色主题（纸白 / 黑白极简）使用同色系浅底。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
      }
      .width('100%')
      .padding(12)
      .backgroundColor(T.CARD)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .alignItems(HorizontalAlign.Start)
    } else {
      Column({ space: 9 }) {
        this.cardTitle('播放行为', '即时生效', T.OK)
        this.sliderRow('默认音量', 'volume', 0, 100, '%')
        Row() {
          Text('播放模式')
            .fontSize(11)
            .fontColor(T.FG_DIM)
            .layoutWeight(1)
          Select([
            { value: '顺序播放' },
            { value: '列表循环' },
            { value: '单曲循环' },
            { value: '随机' }
          ])
            .selected(this.playMode)
            .value('列表循环')
            .width(120)
            .height(30)
            .font({ size: 10 })
            .fontColor(T.FG_DIM)
            .backgroundColor(T.INPUT)
            .onSelect((index: number) => {
              this.playMode = index;
              this.markDirty();
            })
        }
        .width('100%')
        .height(32)
        Divider().color(T.LINE_SOFT)
        Row() {
          Text('31 段均衡器')
            .fontSize(11)
            .fontColor(T.FG_DIM)
            .layoutWeight(1)
          Button('打开')
            .height(26)
            .fontSize(10)
            .fontColor(T.ACCENT)
            .backgroundColor(T.GHOST_BG)
            .borderRadius(6)
            .onClick(() => {
              this.onOpenEq();
            })
        }
        .width('100%')
        Text('原生 FFmpeg + OH_AudioRenderer 直通；EQ 关闭或全 0 dB 时逐字节旁路。')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .lineHeight(16)
      }
      .width('100%')
      .padding(12)
      .backgroundColor(T.CARD)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .alignItems(HorizontalAlign.Start)
    }
  }

  build() {
    Column() {
      Row({ space: 2 }) {
        this.tabButton('匹配', 0)
        this.tabButton('歌词', 1)
        this.tabButton('写入', 2)
        this.tabButton('外观', 3)
        this.tabButton('播放', 4)
        Blank()
      }
      .width('100%')
      .height(32)

      Scroll() {
        Column() {
          this.tabContent()
        }
        .width('100%')
        .padding({ top: 8 })
      }
      .layoutWeight(1)
      .scrollBar(BarState.Auto)

      Row({ space: 8 }) {
        Row({ space: 5 }) {
          Column()
            .width(5)
            .height(5)
            .borderRadius(3)
            .backgroundColor(this.dirty ? T.WARN : T.OK)
          Text(this.savedHint != '' ? this.savedHint : this.dirty ? '有未保存的修改' : '参数已同步')
            .fontSize(9)
            .fontColor(this.dirty ? T.WARN : T.FG_MUTE)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
        .layoutWeight(1)
        Button('还原')
          .height(30)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(8)
          .padding({ left: 12, right: 12 })
          .onClick(() => {
            this.resetFromProps();
            this.onPreviewOpacity(this.uiOpacity);
          })
        Button('保存参数')
          .height(30)
          .fontSize(10)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 14, right: 14 })
          .onClick(() => {
            this.save();
          })
      }
      .width('100%')
      .height(38)
    }
    .width('100%')
    .height('100%')
    .padding({ left: 10, right: 10, top: 6, bottom: 6 })
  }
}
