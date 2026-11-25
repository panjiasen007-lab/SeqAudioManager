// 桌面版主工作台的鸿蒙 1:1 组件集：品牌条 / 操作栏 / 统计卡 / 筛选 / 表格 /
// 详情 / 播放条 / 底部日志歌词信号 / 状态栏。
// 全部组件只吃 props + 回调，状态由 pages/Index 统一持有。
import { Track, emptyTrack } from '../model/Track';
import { T } from '../theme/Tokens';
import { fmtDuration, fmtSize, fmtSpec } from '../utils/Util';
import { LogLine } from '../utils/AppLog';
import { LyricLine } from '../lyrics/LyricsService';
import { verdictColor } from '../core/AuthenticityService';

// ---------------------------------------------------------------- 基础块

@Component
export struct SectionHeader {
  title: string = '';
  hint: string = '';
  accent: string = T.ACCENT;

  build() {
    Row({ space: 8 }) {
      Column()
        .width(3)
        .height(13)
        .borderRadius(2)
        .backgroundColor(this.accent)
      Text(this.title)
        .fontSize(12)
        .fontWeight(FontWeight.Medium)
        .fontColor(T.FG)
      if (this.hint != '') {
        Text(this.hint)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
    }
    .width('100%')
    .alignItems(VerticalAlign.Center)
  }
}

@Component
export struct StatCard {
  @Prop label: string = '';
  @Prop value: number = 0;
  @Prop color: string = T.FG;
  @Prop suffix: string = '';
  onTap: () => void = () => {
  };

  build() {
    Column() {
      Row()
        .width('100%')
        .height(2)
        .backgroundColor(this.value > 0 ? this.color : T.LINE_SOFT)
      Column() {
        Text(this.label)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .maxLines(1)
        Text(`${this.value}${this.suffix}`)
          .fontSize(22)
          .fontWeight(FontWeight.Bold)
          .fontColor(this.value > 0 ? this.color : T.FG_MUTE)
          .margin({ top: 2 })
      }
      .alignItems(HorizontalAlign.Start)
      .padding({ left: 12, right: 10, top: 8 })

      Row({ space: 3 }) {
        Column().width(3).height(7).borderRadius(2).backgroundColor(this.color).opacity(0.32)
        Column().width(3).height(13).borderRadius(2).backgroundColor(this.color).opacity(0.38)
        Column().width(3).height(9).borderRadius(2).backgroundColor(this.color).opacity(0.30)
        Column().width(3).height(17).borderRadius(2).backgroundColor(this.color).opacity(0.42)
        Column().width(3).height(11).borderRadius(2).backgroundColor(this.color).opacity(0.34)
      }
      .width('100%')
      .justifyContent(FlexAlign.End)
      .padding({ right: 12, bottom: 7 })
    }
    .height(70)
    .layoutWeight(1)
    .backgroundColor(T.PANEL)
    .borderRadius(T.R_CARD)
    .border({ width: 1, color: T.LINE_SOFT })
    .onClick(() => {
      this.onTap();
    })
  }
}

@Component
export struct BrandBar {
  version: string = '';
  @Prop statusText: string = '就绪';
  @Prop uiOpacity: number = 95;
  onOpacityChange: (v: number) => void = () => {
  };

  build() {
    Row({ space: 11 }) {
      Image($r('app.media.startIcon'))
        .width(30)
        .height(30)
        .borderRadius(8)
        .objectFit(ImageFit.Cover)
      Column({ space: 1 }) {
        Text('音序管家')
          .fontSize(17)
          .fontWeight(FontWeight.Bold)
          .fontColor(T.FG)
        Text(`SEQAUDIO MANAGER  ·  v${this.version}`)
          .fontSize(8)
          .letterSpacing(0.6)
          .fontColor(T.FG_MUTE)
      }
      .alignItems(HorizontalAlign.Start)
      Blank()
      Row() {
        Text(this.statusText)
          .fontSize(10)
          .fontColor(T.FG_DIM)
      }
      .height(22)
      .padding({ left: 10, right: 10 })
      .backgroundColor(T.BADGE_BG)
      .borderRadius(9)
      .border({ width: 1, color: T.BADGE_BORDER })

      Blank()
      Text('界面不透明度')
        .fontSize(10)
        .fontColor(T.FG_MUTE)
      Slider({
        value: this.uiOpacity,
        min: 10,
        max: 100,
        step: 1,
        style: SliderStyle.OutSet
      })
        .width(96)
        .selectedColor(T.ACCENT)
        .trackColor(T.LINE)
        .blockColor(T.CYAN)
        .onChange((v: number) => {
          this.onOpacityChange(Math.round(v));
        })
      Text(`${Math.round(this.uiOpacity)}%`)
        .width(38)
        .fontSize(10)
        .fontColor(T.FG_DIM)
        .textAlign(TextAlign.End)
    }
    .width('100%')
    .height(56)
    .padding({ left: 16, right: 16 })
    .alignItems(VerticalAlign.Center)
    .backgroundColor(T.BRAND_BG)
    .border({ width: { bottom: 1 }, color: { bottom: T.LINE_SOFT } })
  }
}

@Component
export struct ActionBar {
  @Prop busy: boolean = false;
  @Prop autoBusy: boolean = false;
  @Prop authBusy: boolean = false;
  @Prop hasPending: boolean = false;
  onAddFiles: () => void = () => {
  };
  onAddFolder: () => void = () => {
  };
  onRemove: () => void = () => {
  };
  onClear: () => void = () => {
  };
  onPlaylists: () => void = () => {
  };
  onScan: () => void = () => {
  };
  onStop: () => void = () => {
  };
  onAuto: () => void = () => {
  };
  onSaveAll: () => void = () => {
  };
  onAuth: () => void = () => {
  };
  onCleanup: () => void = () => {
  };
  onDeleteSelected: () => void = () => {
  };
  onHealth: () => void = () => {
  };
  onTools: () => void = () => {
  };
  onCover: () => void = () => {
  };
  onLyrics: () => void = () => {
  };
  onEq: () => void = () => {
  };
  onCueSplit: () => void = () => {
  };
  onTogglePanel: () => void = () => {
  };
  onMore: (key: string) => void = () => {
  };
  @State private moreOpen: boolean = false;

  @Builder
  moreItem(label: string, key: string) {
    Row() {
      Text(label)
        .fontSize(11)
        .fontColor(T.FG_DIM)
        .maxLines(1)
    }
    .width('100%')
    .height(30)
    .padding({ left: 10, right: 10 })
    .borderRadius(6)
    .onClick(() => {
      this.moreOpen = false;
      this.onMore(key);
    })
  }

  @Builder
  moreMenu() {
    Column() {
      this.moreItem('重命名预览…', 'rename')
      this.moreItem('曲库体检…', 'health')
      this.moreItem('批量整理…', 'tools')
      this.moreItem('批量编辑标签…', 'batchedit')
      this.moreItem('撤销标签编辑（.bak）', 'undoedit')
      this.moreItem('去除标题序号', 'stripno')
      this.moreItem('导出处理报表…', 'report')
      this.moreItem('批量导出封面…', 'exportcovers')
      this.moreItem('生成文件夹封面', 'foldercovers')
      this.moreItem('隔离区管理…', 'quarantine')
      this.moreItem('下一首播放选中', 'playnext')
      this.moreItem('加入队列', 'queue')
      this.moreItem('复制完整路径', 'copypath')
      Divider().color(T.LINE_SOFT).margin({ top: 3, bottom: 3 })
      this.moreItem('CUE 分轨导出…', 'cuesplit')
      this.moreItem('查看频谱…', 'spectrum')
      this.moreItem('播放列表…', 'playlists')
      this.moreItem('封面搜索…', 'cover')
      this.moreItem('歌词…', 'lyrics')
      this.moreItem('均衡器…', 'eq')
      Divider().color(T.LINE_SOFT).margin({ top: 3, bottom: 3 })
      this.moreItem('关于音序管家…', 'about')
    }
    .width(200)
    .padding(6)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(10)
    .border({ width: 1, color: T.LINE })
    .shadow({ radius: 16, color: '#77000000', offsetY: 6 })
  }

  @Builder
  actionButton(text: string, kind: string, enabled: boolean, cb: () => void) {
    Button(text)
      .height(34)
      .fontSize(12)
      .fontWeight(kind == 'primary' ? FontWeight.Medium : FontWeight.Normal)
      .fontColor(kind == 'primary' ? T.ON_ACCENT : kind == 'success' ? T.ON_OK :
        kind == 'danger' ? T.BAD : T.FG)
      .backgroundColor(kind == 'primary' ? T.ACCENT : kind == 'success' ? T.OK :
        kind == 'danger' ? T.DANGER_BG : T.GHOST_BG)
      .border({
        width: 1,
        color: kind == 'primary' ? T.ACCENT_HOVER : kind == 'danger' ? T.DANGER_BORDER : T.LINE_SOFT
      })
      .borderRadius(T.R_BTN)
      .padding({ left: 12, right: 12 })
      .enabled(enabled)
      .opacity(enabled ? 1 : 0.42)
      .onClick(() => {
        cb();
      })
  }

  @Builder
  vline() {
    Column()
      .width(1)
      .height(20)
      .backgroundColor(T.LINE)
  }

  build() {
    Column({ space: 8 }) {
      Row({ space: 8 }) {
        this.actionButton('添加文件', 'ghost', true, () => this.onAddFiles())
        this.actionButton('添加文件夹', 'ghost', true, () => this.onAddFolder())
        this.actionButton('移除', 'ghost', true, () => this.onRemove())
        this.vline()
        this.actionButton('清空列表', 'ghost', true, () => this.onClear())
        this.actionButton('播放列表', 'ghost', true, () => this.onPlaylists())
        Button(this.autoBusy ? '匹配中…' : '一键全自动')
          .height(34)
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .border({ width: 1, color: T.ACCENT_HOVER })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .enabled(!this.autoBusy && !this.busy)
          .opacity(this.autoBusy || this.busy ? 0.5 : 1)
          .onClick(() => {
            this.onAuto();
          })
        Button('全部确认保存')
          .height(34)
          .fontSize(12)
          .fontColor(T.ON_OK)
          .backgroundColor(T.OK)
          .border({ width: 1, color: T.OK_BORDER })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .enabled(this.hasPending)
          .opacity(this.hasPending ? 1 : 0.42)
          .onClick(() => {
            this.onSaveAll();
          })
        Blank()
      }
      .width('100%')

      Row({ space: 8 }) {
        Button(this.busy ? '处理中…' : '扫描曲库')
          .height(34)
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
          .fontColor(T.ACCENT)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.ACCENT_DIM })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .enabled(!this.busy && !this.autoBusy && !this.authBusy)
          .opacity(this.busy ? 0.5 : 1)
          .onClick(() => {
            this.onScan();
          })
        Button('停止')
          .height(34)
          .fontSize(12)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .enabled(this.busy)
          .opacity(this.busy ? 1 : 0.42)
          .onClick(() => {
            this.onStop();
          })
        this.vline()
        Button(this.authBusy ? '鉴定中…' : '真假鉴定')
          .height(34)
          .fontSize(12)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .enabled(!this.authBusy && !this.autoBusy)
          .opacity(this.authBusy ? 0.5 : 1)
          .onClick(() => {
            this.onAuth();
          })
        Button('清理假无损')
          .height(34)
          .fontSize(12)
          .fontColor(T.BAD)
          .backgroundColor(T.DANGER_BG)
          .border({ width: 1, color: T.DANGER_BORDER })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .enabled(!this.authBusy && !this.autoBusy)
          .onClick(() => {
            this.onCleanup();
          })
        Button('删除选中')
          .height(34)
          .fontSize(12)
          .fontColor(T.BAD)
          .backgroundColor(T.DANGER_BG)
          .border({ width: 1, color: T.DANGER_BORDER })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .onClick(() => {
            this.onDeleteSelected();
          })
        Blank()
        Button('更多')
          .height(34)
          .fontSize(12)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(T.R_BTN)
          .padding({ left: 12, right: 12 })
          .onClick(() => {
            this.moreOpen = !this.moreOpen;
          })
          .bindPopup(this.moreOpen, {
            builder: this.moreMenu,
            placement: Placement.BottomRight,
            popupColor: Color.Transparent,
            enableArrow: false,
            autoCancel: true,
            onStateChange: (e: PopupStateChangeParam) => {
              this.moreOpen = e.isVisible;
            }
          })
        this.actionButton('侧栏', 'ghost', true, () => this.onTogglePanel())
      }
      .width('100%')
    }
    .width('100%')
  }
}

@Component
export struct VerdictBar {
  @Prop countAll: number = 0;
  @Prop countTrue: number = 0;
  @Prop countFake: number = 0;
  @Prop countSuspect: number = 0;
  @Prop countUnknown: number = 0;
  @Prop filter: string = 'all';
  onFilter: (key: string) => void = () => {
  };

  // 注意：带参数的 @Builder 不能可靠跟随 @Prop 刷新，这里全部内联，避免计数定格在首帧
  build() {
    Row({ space: 6 }) {
      Button(`全部判定 ${this.countAll}`)
        .height(26)
        .fontSize(10)
        .fontColor(this.filter == 'all' ? T.ACCENT : T.FG_DIM)
        .backgroundColor(this.filter == 'all' ? T.CHIP_ON_BG : T.CHIP_OFF_BG)
        .border({ width: 1, color: this.filter == 'all' ? T.ACCENT : T.LINE_SOFT })
        .borderRadius(13)
        .padding({ left: 10, right: 10 })
        .onClick(() => {
          this.onFilter('all');
        })
      Button(`真无损 ${this.countTrue}`)
        .height(26)
        .fontSize(10)
        .fontColor(this.filter == 'true' ? T.OK : T.FG_DIM)
        .backgroundColor(this.filter == 'true' ? T.CHIP_ON_BG : T.CHIP_OFF_BG)
        .border({ width: 1, color: this.filter == 'true' ? T.OK : T.LINE_SOFT })
        .borderRadius(13)
        .padding({ left: 10, right: 10 })
        .onClick(() => {
          this.onFilter('true');
        })
      Button(`假无损 ${this.countFake}`)
        .height(26)
        .fontSize(10)
        .fontColor(this.filter == 'fake' ? T.BAD : T.FG_DIM)
        .backgroundColor(this.filter == 'fake' ? T.CHIP_ON_BG : T.CHIP_OFF_BG)
        .border({ width: 1, color: this.filter == 'fake' ? T.BAD : T.LINE_SOFT })
        .borderRadius(13)
        .padding({ left: 10, right: 10 })
        .onClick(() => {
          this.onFilter('fake');
        })
      Button(`疑似 ${this.countSuspect}`)
        .height(26)
        .fontSize(10)
        .fontColor(this.filter == 'suspect' ? T.WARN : T.FG_DIM)
        .backgroundColor(this.filter == 'suspect' ? T.CHIP_ON_BG : T.CHIP_OFF_BG)
        .border({ width: 1, color: this.filter == 'suspect' ? T.WARN : T.LINE_SOFT })
        .borderRadius(13)
        .padding({ left: 10, right: 10 })
        .onClick(() => {
          this.onFilter('suspect');
        })
      Button(`无法判定 ${this.countUnknown}`)
        .height(26)
        .fontSize(10)
        .fontColor(this.filter == 'unknown' ? T.FG_MUTE : T.FG_DIM)
        .backgroundColor(this.filter == 'unknown' ? T.CHIP_ON_BG : T.CHIP_OFF_BG)
        .border({ width: 1, color: this.filter == 'unknown' ? T.FG_MUTE : T.LINE_SOFT })
        .borderRadius(13)
        .padding({ left: 10, right: 10 })
        .onClick(() => {
          this.onFilter('unknown');
        })
      Blank()
    }
    .width('100%')
  }
}

@Component
export struct FilterBar {
  @Prop keyword: string = '';
  @Prop losslessOnly: boolean = false;
  @Prop problemsOnly: boolean = false;
  @Prop specIndex: number = 0;
  @Prop countAll: number = 0;
  @Prop countLossless: number = 0;
  @Prop countLossy: number = 0;
  @Prop countHiRes: number = 0;
  @Prop countCue: number = 0;
  @Prop verdictIndex: number = 0;
  onKeyword: (v: string) => void = () => {
  };
  onToggle: (key: string) => void = () => {
  };
  onSpec: (index: number) => void = () => {
  };
  onVerdict: (index: number) => void = () => {
  };

  private countOf(index: number): number {
    if (index == 0) {
      return this.countAll;
    }
    if (index == 1) {
      return this.countLossless;
    }
    if (index == 2) {
      return this.countLossy;
    }
    if (index == 3) {
      return this.countHiRes;
    }
    return this.countCue;
  }

  @Builder
  chip(label: string, index: number) {
    Button(`${label} ${this.countOf(index)}`)
      .height(28)
      .fontSize(10)
      .fontColor(this.verdictIndex == index ? T.ACCENT : T.FG_DIM)
      .backgroundColor(this.verdictIndex == index ? T.CHIP_ON_BG : T.CHIP_OFF_BG)
      .border({
        width: 1,
        color: this.verdictIndex == index ? T.ACCENT_DIM : T.LINE_SOFT
      })
      .borderRadius(14)
      .padding({ left: 11, right: 11 })
      .onClick(() => {
        this.onVerdict(index);
      })
  }

  build() {
    Column({ space: 8 }) {
      Row({ space: 10 }) {
        Row({ space: 7 }) {
          Text('⌕')
            .fontSize(16)
            .fontColor(T.FG_MUTE)
          TextInput({ text: this.keyword, placeholder: '搜索歌名、歌手、专辑或文件名…' })
            .layoutWeight(1)
            .height(34)
            .fontSize(12)
            .fontColor(T.FG)
            .placeholderColor(T.FG_MUTE)
            .backgroundColor(Color.Transparent)
            .padding({ left: 0, right: 0 })
            .onChange((v: string) => {
              this.onKeyword(v);
            })
        }
        .layoutWeight(1)
        .height(34)
        .padding({ left: 11, right: 10 })
        .backgroundColor(T.INPUT)
        .borderRadius(T.R_INPUT)
        .border({ width: 1, color: T.LINE_SOFT })

        Row({ space: 6 }) {
          Toggle({ type: ToggleType.Checkbox, isOn: this.losslessOnly })
            .width(18)
            .height(18)
            .selectedColor(T.ACCENT)
            .onChange(() => {
              this.onToggle('lossless');
            })
          Text('只看无损')
            .fontSize(11)
            .fontColor(T.FG_DIM)
        }
        .onClick(() => {
          this.onToggle('lossless');
        })

        Row({ space: 6 }) {
          Toggle({ type: ToggleType.Checkbox, isOn: this.problemsOnly })
            .width(18)
            .height(18)
            .selectedColor(T.ACCENT)
            .onChange(() => {
              this.onToggle('problems');
            })
          Text('只看问题')
            .fontSize(11)
            .fontColor(T.FG_DIM)
        }
        .onClick(() => {
          this.onToggle('problems');
        })

        Select([
          { value: '全部规格' },
          { value: '44.1 kHz' },
          { value: '48 kHz' },
          { value: '96 kHz' },
          { value: '176.4k+' },
          { value: 'DSD' },
          { value: '16 bit' },
          { value: '24 bit' }
        ])
          .selected(this.specIndex)
          .value('全部规格')
          .width(112)
          .height(34)
          .font({ size: 11 })
          .fontColor(T.FG_DIM)
          .backgroundColor(T.INPUT)
          .borderRadius(T.R_INPUT)
          .onSelect((index: number) => {
            this.onSpec(index);
          })
      }
      .width('100%')

      Row({ space: 6 }) {
        this.chip('全部', 0)
        this.chip('无损', 1)
        this.chip('有损', 2)
        this.chip('高解析', 3)
        this.chip('CUE', 4)
        Blank()
      }
      .width('100%')
    }
    .width('100%')
  }
}

// ---------------------------------------------------------------- 曲库表格

@Component
export struct TrackTable {
  @Prop tracks: Track[] = [];
  @Prop selectedId: number = -1;
  @Prop selectedIds: number[] = [];
  @Prop playingPath: string = '';
  @Prop compact: boolean = false;
  onSelect: (t: Track, i: number, ctrl: boolean, shift: boolean) => void = () => {
  };
  onPlay: (t: Track, i: number) => void = () => {
  };
  onPlayNext: (t: Track) => void = () => {
  };
  onQueue: (t: Track) => void = () => {
  };
  onCopyPath: (t: Track) => void = () => {
  };
  onEdit: (t: Track) => void = () => {
  };
  onCover: (t: Track) => void = () => {
  };
  onLyrics: (t: Track) => void = () => {
  };
  onRemove: (t: Track) => void = () => {
  };
  onBatchEdit: (t: Track) => void = () => {
  };
  onUndoEdit: (t: Track) => void = () => {
  };
  onQuarantine: (t: Track) => void = () => {
  };
  onMatch: (t: Track) => void = () => {
  };
  onAuth: (t: Track) => void = () => {
  };
  onSpectrum: (t: Track) => void = () => {
  };
  onCueSplit: (t: Track) => void = () => {
  };
  @State private hoverIndex: number = -1;
  @State private menuTrack: Track = emptyTrack();
  @State private menuVisible: boolean = false;
  private lastClickId: number = -1;
  private lastClickTime: number = 0;

  private isPlaying(t: Track): boolean {
    return this.playingPath != '' && t.path == this.playingPath;
  }

  private isRowSelected(t: Track): boolean {
    return this.selectedId == t.id || this.selectedIds.indexOf(t.id) >= 0;
  }

  private handleRowClick(t: Track, index: number, ctrl: boolean, shift: boolean): void {
    if (!ctrl && !shift) {
      const now = Date.now();
      if (this.lastClickId == t.id && now - this.lastClickTime <= 450) {
        this.lastClickId = -1;
        this.lastClickTime = 0;
        this.onPlay(t, index);
        return;
      }
      this.lastClickId = t.id;
      this.lastClickTime = now;
    } else {
      this.lastClickId = -1;
      this.lastClickTime = 0;
    }
    this.onSelect(t, index, ctrl, shift);
  }

  @Builder
  popupItem(label: string, key: string) {
    Row() {
      Text(label)
        .fontSize(11)
        .fontColor(key == 'remove' ? T.BAD : T.FG_DIM)
        .maxLines(1)
    }
    .width('100%')
    .height(30)
    .padding({ left: 10, right: 10 })
    .borderRadius(6)
    .onClick(() => {
      this.menuVisible = false;
      if (key == 'play') {
        this.onPlay(this.menuTrack, -1);
      } else if (key == 'playnext') {
        this.onPlayNext(this.menuTrack);
      } else if (key == 'queue') {
        this.onQueue(this.menuTrack);
      } else if (key == 'copypath') {
        this.onCopyPath(this.menuTrack);
      } else if (key == 'edit') {
        this.onEdit(this.menuTrack);
      } else if (key == 'batchedit') {
        this.onBatchEdit(this.menuTrack);
      } else if (key == 'undoedit') {
        this.onUndoEdit(this.menuTrack);
      } else if (key == 'quarantine') {
        this.onQuarantine(this.menuTrack);
      } else if (key == 'cover') {
        this.onCover(this.menuTrack);
      } else if (key == 'lyrics') {
        this.onLyrics(this.menuTrack);
      } else if (key == 'match') {
        this.onMatch(this.menuTrack);
      } else if (key == 'auth') {
        this.onAuth(this.menuTrack);
      } else if (key == 'spectrum') {
        this.onSpectrum(this.menuTrack);
      } else if (key == 'cuesplit') {
        this.onCueSplit(this.menuTrack);
      } else {
        this.onRemove(this.menuTrack);
      }
    })
  }

  @Builder
  rowMenu() {
    Column() {
      this.popupItem('播放', 'play')
      this.popupItem('下一首播放', 'playnext')
      this.popupItem('加入队列', 'queue')
      Divider().color(T.LINE_SOFT).margin({ top: 3, bottom: 3 })
      this.popupItem('联网匹配', 'match')
      this.popupItem('真假鉴定', 'auth')
      this.popupItem('查看频谱', 'spectrum')
      Divider().color(T.LINE_SOFT).margin({ top: 3, bottom: 3 })
      this.popupItem('编辑标签', 'edit')
      this.popupItem('批量编辑标签…', 'batchedit')
      this.popupItem('撤销标签编辑（.bak）', 'undoedit')
      this.popupItem('封面搜索', 'cover')
      this.popupItem('歌词', 'lyrics')
      this.popupItem('CUE 分轨导出', 'cuesplit')
      this.popupItem('移入隔离区', 'quarantine')
      this.popupItem('复制完整路径', 'copypath')
      Divider().color(T.LINE_SOFT).margin({ top: 3, bottom: 3 })
      this.popupItem('从曲库移除（不删文件）', 'remove')
    }
    .width(190)
    .padding(6)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(10)
    .border({ width: 1, color: T.LINE })
    .shadow({ radius: 16, color: '#77000000', offsetY: 6 })
  }

  @Builder
  headerCell(text: string, width: number, align: TextAlign) {
    Text(text)
      .width(width)
      .fontSize(10)
      .fontColor(T.FG_MUTE)
      .textAlign(align)
      .maxLines(1)
  }

  @Builder
  trackRow(t: Track, index: number) {
    Row({ space: 0 }) {
      Column()
        .width(3)
        .height('100%')
        .backgroundColor(this.isPlaying(t) ? T.ACCENT :
          this.isRowSelected(t) ? T.ACCENT_DIM : Color.Transparent)
      Row({ space: 6 }) {
        Text(this.isPlaying(t) ? '▶' : '')
          .width(12)
          .fontSize(9)
          .fontColor(T.ACCENT)
        Row() {
          Text(T.statusLabel(t))
            .fontSize(9)
            .fontColor(T.statusColor(t))
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(8)
            .backgroundColor(T.alpha(T.statusColor(t), '1F'))
            .border({ width: 1, color: T.alpha(T.statusColor(t), '55') })
        }
        .width(76)
      }
      .width(94)
      .padding({ left: 4 })

      Text(t.title)
        .layoutWeight(1)
        .fontSize(12)
        .fontColor(this.isPlaying(t) ? T.ACCENT : T.FG)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Text(t.artist == '' ? '—' : t.artist)
        .width(96)
        .fontSize(11)
        .fontColor(T.FG_DIM)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      if (!this.compact) {
        Text(t.album == '' ? '—' : t.album)
          .width(118)
          .fontSize(11)
          .fontColor(T.FG_DIM)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      Text(t.ext.replace('.', '').toUpperCase())
        .width(52)
        .fontSize(10)
        .fontColor(T.CYAN)
        .maxLines(1)
      Text(fmtSpec(t).replace(`${t.ext.replace('.', '').toUpperCase()} · `, ''))
        .width(112)
        .fontSize(10)
        .fontColor(T.CYAN)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      if (!this.compact) {
        Text(t.matchConfidence > 0 ? `${Math.round(t.matchConfidence * 100)}%` : '—')
          .width(52)
          .fontSize(9)
          .fontColor(t.matchConfidence >= 0.62 ? T.OK : T.FG_MUTE)
          .textAlign(TextAlign.End)
      }
      Text(t.verdict == '' ? '未鉴定' : t.verdict)
        .width(88)
        .fontSize(9)
        .fontColor(t.verdict == '' ? T.FG_MUTE : verdictColor(t.verdict))
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .margin({ left: 6 })
      Text(fmtDuration(t.durationMs))
        .width(54)
        .fontSize(10)
        .fontColor(T.FG_MUTE)
        .textAlign(TextAlign.End)
      if (!this.compact) {
        Text(t.fileName)
          .width(150)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .margin({ left: 12 })
      }
    }
    .width('100%')
    .height(36)
    .padding({ right: 8 })
    .backgroundColor(this.isPlaying(t) ? T.ROW_PLAY_BG :
      this.selectedId == t.id ? T.ROW_SELECT_BG :
        this.isRowSelected(t) ? T.ROW_MULTI_BG :
          this.hoverIndex == index ? T.ROW_HOVER_BG : Color.Transparent)
    .border({ width: { bottom: 1 }, color: { bottom: T.ROW_LINE } })
    .onHover((isHover: boolean) => {
      this.hoverIndex = isHover ? index : -1;
    })
    .onMouse((e: MouseEvent) => {
      if (e.button == MouseButton.Right) {
        this.menuTrack = t;
        this.menuVisible = true;
      }
    })
    .onClick((e: ClickEvent) => {
      const ctrl: boolean = e.getModifierKeyState?.(['Ctrl']) == true;
      const shift: boolean = e.getModifierKeyState?.(['Shift']) == true;
      this.handleRowClick(t, index, ctrl, shift);
    })
    .bindPopup(this.menuVisible && this.menuTrack.id == t.id, {
      builder: this.rowMenu,
      placement: Placement.BottomLeft,
      popupColor: Color.Transparent,
      enableArrow: false,
      autoCancel: true,
      onStateChange: (e: PopupStateChangeParam) => {
        if (!e.isVisible) {
          this.menuVisible = false;
        }
      }
    })
  }

  @Builder
  emptyState() {
    Column({ space: 10 }) {
      Column() {
        Row({ space: 4 }) {
          Column().width(3).height(10).borderRadius(2).backgroundColor(T.FG_MUTE)
          Column().width(3).height(20).borderRadius(2).backgroundColor(T.FG_MUTE)
          Column().width(3).height(28).borderRadius(2).backgroundColor(T.FG_MUTE)
          Column().width(3).height(16).borderRadius(2).backgroundColor(T.FG_MUTE)
          Column().width(3).height(22).borderRadius(2).backgroundColor(T.FG_MUTE)
        }
        .justifyContent(FlexAlign.Center)
        .width(68)
        .height(68)
      }
      .justifyContent(FlexAlign.Center)
      .border({ width: 1, color: T.LINE, style: BorderStyle.Dashed })
      .borderRadius(14)

      Text('把音频文件拖到这里')
        .fontSize(14)
        .fontWeight(FontWeight.Medium)
        .fontColor(T.FG_DIM)
      Text('支持 FLAC / MP3 / WAV / M4A / OGG / AAC · 无损优先')
        .fontSize(10)
        .fontColor(T.FG_MUTE)
    }
    .width('100%')
    .layoutWeight(1)
    .justifyContent(FlexAlign.Center)
  }

  build() {
    Column() {
      Row({ space: 0 }) {
        Text('状态')
          .width(94)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .padding({ left: 7 })
        Text('歌名')
          .layoutWeight(1)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
        Text('歌手').width(96).fontSize(10).fontColor(T.FG_MUTE)
        if (!this.compact) {
          Text('专辑').width(118).fontSize(10).fontColor(T.FG_MUTE)
        }
        Text('格式').width(52).fontSize(10).fontColor(T.FG_MUTE)
        Text('规格').width(112).fontSize(10).fontColor(T.FG_MUTE)
        if (!this.compact) {
          Text('置信度')
            .width(52)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .textAlign(TextAlign.End)
        }
        Text('鉴定结果')
          .width(88)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .margin({ left: 6 })
        Text('时长')
          .width(54)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .textAlign(TextAlign.End)
        if (!this.compact) {
          Text('文件名')
            .width(150)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .margin({ left: 12 })
        }
      }
      .width('100%')
      .height(32)
      .padding({ right: 8 })
      .backgroundColor(T.TABLE_HEAD_BG)
      .border({ width: { bottom: 1 }, color: { bottom: T.LINE_SOFT } })

      if (this.tracks.length == 0) {
        this.emptyState()
      } else {
        List() {
          ForEach(this.tracks, (t: Track, i: number) => {
            ListItem() {
              this.trackRow(t, i)
            }
          }, (t: Track) => `${t.id}-${t.path}-${t.verdict}-${t.verdictConfidence}-${t.matchConfidence}` +
            `-${this.selectedId == t.id ? 1 : 0}-${this.selectedIds.indexOf(t.id) >= 0 ? 1 : 0}`)
        }
        .width('100%')
        .layoutWeight(1)
        .scrollBar(BarState.Auto)
        .edgeEffect(EdgeEffect.Spring)
      }
    }
    .width('100%')
    .layoutWeight(1)
    .backgroundColor(T.TABLE_BG)
    .borderRadius(T.R_CARD)
    .border({ width: 1, color: T.LINE_SOFT })
    .clip(true)
  }
}

// ---------------------------------------------------------------- 右侧详情

@Component
export struct Inspector {
  @Prop track: Track = emptyTrack();
  @Prop coverPath: string = '';
  onEdit: () => void = () => {
  };
  onCover: () => void = () => {
  };
  onLyrics: () => void = () => {
  };
  onRemove: () => void = () => {
  };
  onAuth: () => void = () => {
  };
  onSpectrum: () => void = () => {
  };
  onMatch: () => void = () => {
  };

  private infoValue(key: string): string {
    if (key == 'albumartist') {
      return this.track.albumartist;
    }
    if (key == 'disc') {
      return this.track.discNo > 0 ? `${this.track.discNo}` : '';
    }
    if (key == 'format') {
      return this.track.ext.replace('.', '').toUpperCase();
    }
    if (key == 'spec') {
      return fmtSpec(this.track).replace(
        `${this.track.ext.replace('.', '').toUpperCase()} · `, '');
    }
    if (key == 'duration') {
      return fmtDuration(this.track.durationMs);
    }
    if (key == 'bitrate') {
      return this.track.bitrateKbps > 0 ? `${this.track.bitrateKbps} kbps` : '—';
    }
    if (key == 'size') {
      return fmtSize(this.track.sizeBytes);
    }
    return this.track.path;
  }

  @Builder
  infoRow(label: string, key: string, multiline: boolean) {
    Row({ space: 8 }) {
      Text(label)
        .width(48)
        .fontSize(10)
        .fontColor(T.FG_MUTE)
        .textAlign(TextAlign.End)
      Text(this.infoValue(key) == '' ? '—' : this.infoValue(key))
        .layoutWeight(1)
        .fontSize(11)
        .fontColor(T.FG_DIM)
        .maxLines(multiline ? 2 : 1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
    }
    .width('100%')
    .alignItems(VerticalAlign.Top)
  }

  build() {
    Column() {
      Text('曲目详情')
        .fontSize(12)
        .fontWeight(FontWeight.Medium)
        .fontColor(T.FG)
        .width('100%')
        .padding({ left: 2, bottom: 8 })

      if (this.track.id == 0) {
        Column({ space: 8 }) {
          Text('♪')
            .fontSize(34)
            .fontColor(T.FG_MUTE)
          Text('选中一首曲目，查看封面、标签与规格')
            .fontSize(11)
            .fontColor(T.FG_MUTE)
            .textAlign(TextAlign.Center)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        Scroll() {
          Column({ space: 9 }) {
            Row() {
              if (this.coverPath != '') {
                Image(this.coverPath)
                  .width(168)
                  .height(168)
                  .borderRadius(10)
                  .objectFit(ImageFit.Cover)
                  .border({ width: 1, color: T.LINE })
              } else {
                Column() {
                  Text('无封面')
                    .fontSize(10)
                    .fontColor(T.FG_MUTE)
                }
                .width(168)
                .height(168)
                .justifyContent(FlexAlign.Center)
                .backgroundColor(T.CARD)
                .borderRadius(10)
                .border({ width: 1, color: T.LINE, style: BorderStyle.Dashed })
              }
            }
            .width('100%')
            .justifyContent(FlexAlign.Center)

            Text(this.track.title == '' ? this.track.fileName : this.track.title)
              .fontSize(15)
              .fontWeight(FontWeight.Bold)
              .fontColor(T.FG)
              .maxLines(2)
              .textAlign(TextAlign.Center)
              .width('100%')
            Text(this.track.artist == '' ? '—' : this.track.artist)
              .fontSize(11)
              .fontColor(T.FG_DIM)
              .width('100%')
              .textAlign(TextAlign.Center)
            Text(this.track.album)
              .fontSize(10)
              .fontColor(T.FG_MUTE)
              .width('100%')
              .textAlign(TextAlign.Center)

            Divider().color(T.LINE_SOFT).margin({ top: 2, bottom: 2 })

            this.infoRow('专辑艺术家', 'albumartist', false)
            this.infoRow('碟号', 'disc', false)
            this.infoRow('格式', 'format', false)
            this.infoRow('规格', 'spec', false)
            this.infoRow('时长', 'duration', false)
            this.infoRow('码率', 'bitrate', false)
            this.infoRow('大小', 'size', false)
            this.infoRow('路径', 'path', true)

            Row({ space: 8 }) {
              Text(this.track.healthStatus == 'bad' ? '文件损坏' :
                this.track.healthStatus == 'warn' ? '需要复查' : '健康检查通过')
                .fontSize(11)
                .fontColor(T.healthColor(this.track.healthStatus))
                .layoutWeight(1)
              Text(this.track.isCueChild == 1 ? 'CUE 子轨' : '独立文件')
                .fontSize(10)
                .fontColor(T.FG_MUTE)
            }
            .width('100%')
            .padding(9)
            .backgroundColor(T.CARD)
            .borderRadius(8)
            .border({ width: 1, color: T.LINE_SOFT })

            Column({ space: 4 }) {
              Row() {
                Text(this.track.verdict == '' ? '未鉴定' : this.track.verdict)
                  .fontSize(12)
                  .fontWeight(FontWeight.Medium)
                  .fontColor(this.track.verdict == '' ? T.FG_MUTE : verdictColor(this.track.verdict))
                Blank()
                if (this.track.verdict != '') {
                  Text(`${Math.round(this.track.verdictConfidence * 100)}%`)
                    .fontSize(10)
                    .fontColor(T.FG_MUTE)
                }
              }
              .width('100%')
              Text(this.track.verdictReason == '' ? '真假无损鉴定会解码抽样频谱，判断转码、升频与升位深' :
                this.track.verdictReason)
                .fontSize(9)
                .fontColor(T.FG_MUTE)
                .maxLines(4)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .width('100%')
              Row({ space: 6 }) {
                Button(this.track.verdict == '' ? '开始鉴定' : '重新鉴定')
                  .height(26)
                  .fontSize(9)
                  .fontColor(T.ACCENT)
                  .backgroundColor(T.GHOST_BG)
                  .borderRadius(6)
                  .onClick(() => {
                    this.onAuth();
                  })
                if (this.track.verdict != '') {
                  Button('查看频谱')
                    .height(26)
                    .fontSize(9)
                    .fontColor(T.CYAN)
                    .backgroundColor(T.SIGNAL_BG)
                    .borderRadius(6)
                    .onClick(() => {
                      this.onSpectrum();
                    })
                }
                Blank()
                Text('联网匹配')
                  .fontSize(9)
                  .fontColor(T.ACCENT)
                  .onClick(() => {
                    this.onMatch();
                  })
              }
              .width('100%')
            }
            .width('100%')
            .padding(9)
            .backgroundColor(T.CARD)
            .borderRadius(8)
            .border({ width: 1, color: T.LINE_SOFT })
            .alignItems(HorizontalAlign.Start)

            Row({ space: 8 }) {
              Button('编辑标签')
                .layoutWeight(1)
                .height(32)
                .fontSize(11)
                .fontColor(T.ON_ACCENT)
                .backgroundColor(T.ACCENT)
                .borderRadius(T.R_BTN)
                .onClick(() => {
                  this.onEdit();
                })
              Button('封面')
                .height(32)
                .fontSize(11)
                .fontColor(T.FG)
                .backgroundColor(T.GHOST_BG)
                .border({ width: 1, color: T.LINE_SOFT })
                .borderRadius(T.R_BTN)
                .padding({ left: 12, right: 12 })
                .onClick(() => {
                  this.onCover();
                })
              Button('歌词')
                .height(32)
                .fontSize(11)
                .fontColor(T.FG)
                .backgroundColor(T.GHOST_BG)
                .border({ width: 1, color: T.LINE_SOFT })
                .borderRadius(T.R_BTN)
                .padding({ left: 12, right: 12 })
                .onClick(() => {
                  this.onLyrics();
                })
            }
            .width('100%')
            .margin({ top: 2 })

            Button('从曲库移除（不删文件）')
              .width('100%')
              .height(30)
              .fontSize(10)
              .fontColor(T.BAD)
              .backgroundColor(T.DANGER_SOFT_BG)
              .border({ width: 1, color: T.DANGER_BORDER })
              .borderRadius(T.R_BTN)
              .onClick(() => {
                this.onRemove();
              })
          }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
        }
        .layoutWeight(1)
        .scrollBar(BarState.Off)
      }
    }
    .width('100%')
    .height('100%')
    .padding({ left: 12, right: 12, top: 10, bottom: 10 })
    .alignItems(HorizontalAlign.Start)
  }
}

// ---------------------------------------------------------------- 播放条

@Component
export struct PlayerBar {
  @Prop track: Track = emptyTrack();
  @Prop coverPath: string = '';
  @Prop playing: boolean = false;
  @Prop playPosition: number = 0;
  @Prop duration: number = 0;
  @Prop volume: number = 80;
  @Prop modeGlyph: string = '→';
  @Prop eqOn: boolean = false;
  @Prop signalOn: boolean = false;
  @Prop miniOn: boolean = false;
  onToggle: () => void = () => {
  };
  onPrev: () => void = () => {
  };
  onNext: () => void = () => {
  };
  onStop: () => void = () => {
  };
  onSeek: (ms: number) => void = () => {
  };
  onVolume: (v: number) => void = () => {
  };
  onMode: () => void = () => {
  };
  onSignal: () => void = () => {
  };
  onEq: () => void = () => {
  };
  onMini: () => void = () => {
  };
  @State private dragging: boolean = false;
  @State private dragValue: number = 0;

  @Builder
  iconButton(glyph: string, size: number, color: string, active: boolean, cb: () => void) {
    Button(glyph)
      .width(size)
      .height(size)
      .type(ButtonType.Circle)
      .fontSize(size <= 26 ? 11 : 14)
      .fontColor(active ? T.ACCENT : color)
      .backgroundColor(active ? T.CHIP_ON_BG : Color.Transparent)
      .onClick(() => {
        cb();
      })
  }

  build() {
    Row({ space: 10 }) {
      Row() {
        if (this.coverPath != '') {
          Image(this.coverPath)
            .width(40)
            .height(40)
            .borderRadius(8)
            .objectFit(ImageFit.Cover)
        } else {
          Column() {
            Text('♪')
              .fontSize(16)
              .fontColor(T.FG_DIM)
          }
          .width(40)
          .height(40)
          .justifyContent(FlexAlign.Center)
          .backgroundColor(T.INPUT)
          .borderRadius(8)
          .border({ width: 1, color: T.LINE_SOFT })
        }
      }
      .width(40)

      Column({ space: 1 }) {
        Text(this.track.id == 0 ? '未在播放' : this.track.title)
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
          .fontColor(T.FG)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Text(this.track.id == 0 ? '' : this.track.artist)
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width(170)
      .alignItems(HorizontalAlign.Start)

      Button('▣')
        .width(28)
        .height(28)
        .type(ButtonType.Circle)
        .fontSize(12)
        .fontColor(this.miniOn ? T.ACCENT : T.FG_DIM)
        .backgroundColor(this.miniOn ? T.CHIP_ON_BG : Color.Transparent)
        .onClick(() => {
          this.onMini();
        })
      this.iconButton('◀', 28, T.FG_DIM, false, () => this.onPrev())
      Button() {
        if (this.playing) {
          Row({ space: 4 }) {
            Column().width(4).height(15).borderRadius(1).backgroundColor(T.ON_ACCENT)
            Column().width(4).height(15).borderRadius(1).backgroundColor(T.ON_ACCENT)
          }
        } else {
          Image($r('app.media.ic_play'))
            .width(16)
            .height(16)
            .fillColor(T.ON_ACCENT)
        }
      }
      .width(38)
      .height(38)
      .type(ButtonType.Circle)
      .backgroundColor(T.ACCENT)
      .shadow({ radius: 12, color: T.ACCENT_SHADOW, offsetY: 2 })
      .onClick(() => {
        this.onToggle();
      })
      this.iconButton('■', 28, T.FG_DIM, false, () => this.onStop())
      this.iconButton('▶', 28, T.FG_DIM, false, () => this.onNext())

      Slider({
        value: this.dragging ? this.dragValue : this.playPosition,
        min: 0,
        max: this.duration > 0 ? this.duration : 1,
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
            this.onSeek(v);
            this.dragging = false;
          }
        })

      Text(`${fmtDuration(this.dragging ? this.dragValue : this.playPosition)} / ${fmtDuration(this.duration)}`)
        .width(84)
        .fontSize(9)
        .fontColor(T.FG_MUTE)
        .textAlign(TextAlign.End)

      Slider({
        value: this.volume,
        min: 0,
        max: 100,
        step: 1,
        style: SliderStyle.OutSet
      })
        .width(84)
        .selectedColor(T.ACCENT)
        .trackColor(T.LINE_SOFT)
        .blockColor(T.FG_DIM)
        .onChange((v: number) => {
          this.onVolume(v);
        })
      Text(`${Math.round(this.volume)}%`)
        .width(32)
        .fontSize(9)
        .fontColor(T.FG_MUTE)

      Button(this.modeGlyph)
        .width(28)
        .height(28)
        .type(ButtonType.Circle)
        .fontSize(11)
        .fontColor(T.FG_DIM)
        .backgroundColor(Color.Transparent)
        .onClick(() => {
          this.onMode();
        })
      Button('≈')
        .width(28)
        .height(28)
        .type(ButtonType.Circle)
        .fontSize(12)
        .fontColor(this.signalOn ? T.ACCENT : T.FG_DIM)
        .backgroundColor(this.signalOn ? T.CHIP_ON_BG : Color.Transparent)
        .onClick(() => {
          this.onSignal();
        })
      Button('EQ')
        .width(30)
        .height(30)
        .type(ButtonType.Circle)
        .fontSize(9)
        .fontColor(this.eqOn ? T.ACCENT : T.FG_DIM)
        .backgroundColor(this.eqOn ? T.CHIP_ON_BG : Color.Transparent)
        .onClick(() => {
          this.onEq();
        })
    }
    .width('100%')
    .height(56)
    .padding({ left: 14, right: 14 })
    .backgroundColor(T.PLAYER_BG)
    .borderRadius(T.R_BAR)
    .border({ width: 1, color: T.PLAYER_BORDER })
    .linearGradient({
      angle: 90,
      colors: [[T.PLAYER_GRAD_TOP, 0.0], [T.PLAYER_GRAD_BOTTOM, 1.0]]
    })
    .shadow({ radius: 18, color: '#55000000', offsetY: 6 })
  }
}

// ---------------------------------------------------------------- 底部三栏

export class SignalStage {
  name: string = '';
  detail: string = '';
  status: string = '';
  color: string = T.FG_DIM;

  constructor(name: string, detail: string, status: string, color: string) {
    this.name = name;
    this.detail = detail;
    this.status = status;
    this.color = color;
  }
}

@Component
export struct BottomDock {
  @Prop @Watch('onLogsChanged') logs: LogLine[] = [];
  @Prop lyrics: LyricLine[] = [];
  @Prop lyricIndex: number = 0;
  @Prop lyricSource: string = '';
  @Prop signalVisible: boolean = false;
  @Prop stages: SignalStage[] = [];
  @Prop collapsed: boolean = false;
  onToggleCollapse: () => void = () => {
  };
  onClearLog: () => void = () => {
  };
  onReloadLyrics: () => void = () => {
  };
  private logScroller: Scroller = new Scroller();

  private onLogsChanged(): void {
    try {
      this.logScroller.scrollEdge(Edge.Bottom);
    } catch (e) {
      // 首帧 scroller 尚未挂载
    }
  }

  @Builder
  lyricsPanel() {
    Column() {
      SectionHeader({ title: '歌词', hint: this.lyricSource == '' ? '可联网匹配或读同目录 .lrc' : this.lyricSource })
      Row() {
        if (this.lyrics.length == 0) {
          Column({ space: 4 }) {
            Text('未找到歌词')
              .fontSize(11)
              .fontColor(T.FG_MUTE)
            Text('点此联网匹配')
              .fontSize(10)
              .fontColor(T.ACCENT_DIM)
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        } else {
          Column({ space: 6 }) {
            ForEach([this.lyricIndex - 1, this.lyricIndex, this.lyricIndex + 1], (idx: number) => {
              if (idx >= 0 && idx < this.lyrics.length) {
                Text(this.lyrics[idx].text == '' ? '♪' : this.lyrics[idx].text)
                  .fontSize(idx == this.lyricIndex ? 13 : 11)
                  .fontWeight(idx == this.lyricIndex ? FontWeight.Medium : FontWeight.Normal)
                  .fontColor(idx == this.lyricIndex ? T.ACCENT : T.FG_MUTE)
                  .maxLines(1)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                  .width('100%')
                  .textAlign(TextAlign.Center)
              }
            }, (idx: number) => `${idx}-${this.lyrics.length}`)
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        }
      }
      .width('100%')
      .layoutWeight(1)
      .onClick(() => {
        this.onReloadLyrics();
      })
    }
    .layoutWeight(1)
    .height('100%')
    .padding({ left: 12, right: 12, top: 8 })
    .backgroundColor(T.DOCK_BG)
    .borderRadius(T.R_CARD)
    .border({ width: 1, color: T.LINE_SOFT })
  }

  @Builder
  signalPanel() {
    Column() {
      SectionHeader({ title: '信号路径', hint: '实时', accent: T.CYAN })
      Scroll() {
        Column({ space: 5 }) {
          ForEach(this.stages, (s: SignalStage) => {
            Row({ space: 6 }) {
              Text(s.name)
                .width(38)
                .fontSize(9)
                .fontColor(T.FG_MUTE)
              Text(s.detail)
                .layoutWeight(1)
                .fontSize(10)
                .fontColor(T.FG_DIM)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Text(s.status)
                .fontSize(9)
                .fontColor(s.color)
            }
            .width('100%')
          }, (s: SignalStage) => `${s.name}-${s.detail}-${s.status}`)
        }
        .width('100%')
      }
      .layoutWeight(1)
      .scrollBar(BarState.Off)
      .margin({ top: 6 })
    }
    .layoutWeight(1)
    .height('100%')
    .padding({ left: 12, right: 12, top: 8 })
    .backgroundColor(T.DOCK_BG)
    .borderRadius(T.R_CARD)
    .border({ width: 1, color: T.LINE_SOFT })
  }

  build() {
    Column() {
      Row({ space: 8 }) {
        Row() {
          SectionHeader({ title: '运行日志', hint: '识别失败的具体原因都在这里' })
        }
        .layoutWeight(1)
        Button(this.collapsed ? '展开' : '收起')
          .height(22)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(6)
          .padding({ left: 9, right: 9 })
          .onClick(() => {
            this.onToggleCollapse();
          })
        Button('清空')
          .height(22)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(6)
          .padding({ left: 9, right: 9 })
          .onClick(() => {
            this.onClearLog();
          })
      }
      .width('100%')
      .height(30)

      if (!this.collapsed) {
        Row({ space: 8 }) {
          Column() {
            Scroll(this.logScroller) {
              Column({ space: 2 }) {
                ForEach(this.logs, (line: LogLine) => {
                  Row({ space: 8 }) {
                    Text(line.time)
                      .width(54)
                      .fontSize(9)
                      .fontColor(T.FG_MUTE)
                    Text(line.message)
                      .layoutWeight(1)
                      .fontSize(10)
                      .fontColor(line.level == 'ERROR' ? T.BAD :
                        line.level == 'WARN' ? T.WARN : T.FG_DIM)
                      .maxLines(1)
                      .textOverflow({ overflow: TextOverflow.Ellipsis })
                  }
                  .width('100%')
                }, (line: LogLine) => `${line.seq}`)
              }
              .width('100%')
              .alignItems(HorizontalAlign.Start)
            }
            .width('100%')
            .height(128)
            .scrollBar(BarState.Auto)
          }
          .layoutWeight(1)
          .height('100%')
          .padding({ left: 12, right: 12, top: 8 })
          .backgroundColor(T.DOCK_BG)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)

          this.lyricsPanel()
          if (this.signalVisible) {
            this.signalPanel()
          }
        }
        .width('100%')
        .layoutWeight(1)
      }
    }
    .width('100%')
    .height(this.collapsed ? 34 : 170)
    .alignItems(HorizontalAlign.Start)
  }
}

// ---------------------------------------------------------------- 状态栏

@Component
export struct StatusBar {
  @Prop hint: string = '拖入音频文件，或点击「添加文件」';
  @Prop right: string = '';
  @Prop kernel: string = '';
  @Prop badge: string = '';

  build() {
    Row({ space: 12 }) {
      Text(this.hint)
        .fontSize(10)
        .fontColor(T.FG_MUTE)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Blank()
      if (this.kernel != '') {
        Text(this.kernel)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .maxLines(1)
      }
      if (this.badge != '') {
        Text(this.badge)
          .fontSize(9)
          .fontColor(T.CYAN)
          .padding({ left: 6, right: 6, top: 1, bottom: 1 })
          .borderRadius(6)
          .backgroundColor(T.CUE_BG)
      }
      if (this.right != '') {
        Text(this.right)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
          .maxLines(1)
      }
    }
    .width('100%')
    .height(30)
    .padding({ left: 14, right: 14 })
    .backgroundColor(T.DOCK_ALPHA)
    .border({ width: { top: 1 }, color: { top: T.LINE_SOFT } })
  }
}

// ---------------------------------------------------------------- 迷你浮层

@Component
export struct MiniOverlay {
  @Prop track: Track = emptyTrack();
  @Prop coverPath: string = '';
  @Prop playing: boolean = false;
  @Prop playPosition: number = 0;
  @Prop duration: number = 0;
  @Prop uiOpacity: number = 80;
  @Prop lyrics: LyricLine[] = [];
  @Prop lyricIndex: number = 0;
  onToggle: () => void = () => {
  };
  onPrev: () => void = () => {
  };
  onNext: () => void = () => {
  };
  onSeek: (ms: number) => void = () => {
  };
  onOpacity: (v: number) => void = () => {
  };
  onExpand: () => void = () => {
  };
  onClose: () => void = () => {
  };

  build() {
    Column({ space: 8 }) {
      Row() {
        Text(this.track.id == 0 ? '未在播放' : this.track.title)
          .fontSize(13)
          .fontWeight(FontWeight.Medium)
          .fontColor(T.FG)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1)
        Text('—')
          .fontSize(12)
          .fontColor(T.FG_DIM)
        Text('□')
          .fontSize(12)
          .fontColor(T.FG_DIM)
          .margin({ left: 12 })
          .onClick(() => {
            this.onExpand();
          })
        Text('✕')
          .fontSize(12)
          .fontColor(T.FG_DIM)
          .margin({ left: 12 })
          .onClick(() => {
            this.onClose();
          })
      }
      .width('100%')

      Row() {
        if (this.coverPath != '') {
          Image(this.coverPath)
            .width(86)
            .height(86)
            .borderRadius(12)
            .objectFit(ImageFit.Cover)
        } else {
          Column() {
            Text('♪')
              .fontSize(34)
              .fontColor(T.FG_MUTE)
          }
          .width(86)
          .height(86)
          .justifyContent(FlexAlign.Center)
          .backgroundColor(T.INPUT)
          .borderRadius(12)
        }
        Column({ space: 5 }) {
          Blank()
          Row({ space: 10 }) {
            Text('◀')
              .fontSize(13)
              .fontColor(T.FG_DIM)
              .onClick(() => {
                this.onPrev();
              })
            Button() {
              if (this.playing) {
                Row({ space: 4 }) {
                  Column().width(4).height(14).borderRadius(1).backgroundColor(T.ON_ACCENT)
                  Column().width(4).height(14).borderRadius(1).backgroundColor(T.ON_ACCENT)
                }
              } else {
                Image($r('app.media.ic_play'))
                  .width(16)
                  .height(16)
                  .fillColor(T.ON_ACCENT)
              }
            }
            .width(36)
            .height(36)
            .type(ButtonType.Circle)
            .backgroundColor(T.ACCENT)
            .onClick(() => {
              this.onToggle();
            })
            Text('▶')
              .fontSize(13)
              .fontColor(T.FG_DIM)
              .onClick(() => {
                this.onNext();
              })
          }
          .justifyContent(FlexAlign.Center)
          .width('100%')
          Blank()
        }
        .layoutWeight(1)
        .height(86)
      }
      .width('100%')

      Slider({
        value: this.playPosition,
        min: 0,
        max: this.duration > 0 ? this.duration : 1,
        step: 500,
        style: SliderStyle.OutSet
      })
        .width('100%')
        .selectedColor(T.CYAN)
        .trackColor(T.LINE)
        .blockColor(T.CYAN)
        .onChange((v: number, mode: SliderChangeMode) => {
          if (mode == SliderChangeMode.End || mode == SliderChangeMode.Click) {
            this.onSeek(v);
          }
        })

      Column({ space: 3 }) {
        ForEach([this.lyricIndex - 1, this.lyricIndex, this.lyricIndex + 1], (idx: number) => {
          if (idx >= 0 && idx < this.lyrics.length) {
            Text(this.lyrics[idx].text == '' ? '♪' : this.lyrics[idx].text)
              .fontSize(idx == this.lyricIndex ? 12 : 10)
              .fontColor(idx == this.lyricIndex ? T.ACCENT : T.FG_MUTE)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
              .textAlign(TextAlign.Center)
          }
        }, (idx: number) => `m-${idx}-${this.lyrics.length}`)
      }
      .width('100%')
      .height(34)

      Row({ space: 8 }) {
        Text('透明度')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
        Slider({
          value: this.uiOpacity,
          min: 10,
          max: 100,
          step: 1,
          style: SliderStyle.OutSet
        })
          .layoutWeight(1)
          .selectedColor(T.ACCENT)
          .trackColor(T.LINE_SOFT)
          .blockColor(T.FG_DIM)
          .onChange((v: number) => {
            this.onOpacity(Math.round(v));
          })
        Text(`${Math.round(this.uiOpacity)}%`)
          .width(34)
          .fontSize(9)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')
    }
    .width(420)
    .padding({ left: 16, right: 16, top: 12, bottom: 12 })
    .backgroundColor(T.POPUP_BG)
    .borderRadius(18)
    .border({ width: 1, color: T.DIALOG_BORDER })
    .shadow({ radius: 26, color: '#77000000', offsetY: 8 })
  }
}
