// CUE 分轨导出对话框：格式选择 + 进度 + 结果。
import { common } from '@kit.AbilityKit';
import { T } from '../theme/Tokens';
import { Track } from '../model/Track';
import { CueSplitOptions, CueSplitReport, CueSplitService } from '../pipeline/CueSplitService';

@CustomDialog
export struct CueSplitDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  onDone: () => void = () => {
  };
  tracks: Track[] = [];
  trimPregap: boolean = false;
  @State private formatIndex: number = 0;
  @State private busy: boolean = false;
  @State private cancelled: boolean = false;
  @State private progressText: string = '';
  @State private reportText: string = '';
  @State private doneCount: number = 0;
  @State private total: number = 0;

  aboutToAppear(): void {
    // SACD 子轨默认位完美 DSF；普通 CUE 保持 FLAC
    if (this.allSacd()) {
      this.formatIndex = 2;
    }
  }

  private allSacd(): boolean {
    if (this.tracks.length == 0) {
      return false;
    }
    for (const t of this.tracks) {
      if (t.ext.toLowerCase() != '.iso') {
        return false;
      }
    }
    return true;
  }

  private async start(): Promise<void> {
    if (this.busy || this.tracks.length == 0) {
      return;
    }
    this.busy = true;
    this.cancelled = false;
    this.total = this.tracks.length;
    this.doneCount = 0;
    this.reportText = '';
    this.progressText = '准备导出…';
    const opts = new CueSplitOptions();
    opts.format = this.formatIndex == 2 ? 'dsf' : (this.formatIndex == 1 ? 'wav' : 'flac');
    opts.fetchLyrics = true;
    opts.trimPregap = this.trimPregap;
    const ctx = getContext(this) as common.UIAbilityContext;
    const rep: CueSplitReport = await CueSplitService.split(ctx, this.tracks, opts,
      (done: number, total: number, cur: string) => {
        this.doneCount = done;
        this.total = total;
        this.progressText = `${done}/${total} · ${cur}`;
      },
      () => this.cancelled);
    this.busy = false;
    this.progressText = '';
    this.reportText = `成功 ${rep.created}/${rep.total} · 预间隙裁剪 ${rep.trimmed} 轨 · 失败 ${rep.failed} · 输出 ${rep.outputDir}`;
    if (rep.errors.length > 0) {
      this.reportText += `\n${rep.errors.slice(0, 4).join('\n')}`;
    }
    this.onDone();
  }

  build() {
    Column({ space: 10 }) {
      Row() {
        Column({ space: 2 }) {
          Text('分轨导出')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(`共 ${this.tracks.length} 个逻辑轨 · 输出到应用沙箱 Exports`)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text(this.formatIndex == 2 ? 'DSF：1bit/2.8224MHz 位完美 DSD' : 'FLAC 体积约 60%，WAV 无压缩')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')

      Row({ space: 8 }) {
        Text('输出格式')
          .fontSize(11)
          .fontColor(T.FG_DIM)
        Button('FLAC')
          .height(28)
          .fontSize(10)
          .fontColor(this.formatIndex == 0 ? T.ACCENT : T.FG_DIM)
          .backgroundColor(this.formatIndex == 0 ? T.CHIP_ON_BG : T.GHOST_BG)
          .borderRadius(6)
          .onClick(() => {
            this.formatIndex = 0;
          })
        Button('WAV')
          .height(28)
          .fontSize(10)
          .fontColor(this.formatIndex == 1 ? T.ACCENT : T.FG_DIM)
          .backgroundColor(this.formatIndex == 1 ? T.CHIP_ON_BG : T.GHOST_BG)
          .borderRadius(6)
          .onClick(() => {
            this.formatIndex = 1;
          })
        Button('DSF')
          .height(28)
          .fontSize(10)
          .fontColor(this.formatIndex == 2 ? T.ACCENT : T.FG_DIM)
          .backgroundColor(this.formatIndex == 2 ? T.CHIP_ON_BG : T.GHOST_BG)
          .borderRadius(6)
          .enabled(this.allSacd())
          .opacity(this.allSacd() ? 1 : 0.38)
          .onClick(() => {
            this.formatIndex = 2;
          })
        Blank()
        Text(this.formatIndex == 2 ? 'DSF 位完美：仅 SACD 子轨可用' :
          `预间隙裁剪：${this.trimPregap ? '开' : '关'}（参数 → 写入）`)
          .fontSize(9)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')

      Column({ space: 6 }) {
        Text(this.progressText == '' ? (this.busy ? '处理中…' : '等待开始') : this.progressText)
          .fontSize(11)
          .fontColor(T.ACCENT)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Progress({
          value: this.doneCount,
          total: this.total > 0 ? this.total : 1,
          type: ProgressType.Linear
        })
          .width('100%')
          .height(4)
          .color(T.ACCENT)
        Scroll() {
          Text(this.reportText)
            .fontSize(10)
            .fontColor(T.FG_DIM)
            .width('100%')
        }
        .width('100%')
        .height(90)
        .scrollBar(BarState.Auto)
        .backgroundColor(T.DOCK_BG)
        .borderRadius(8)
        .padding(8)
      }
      .width('100%')
      .layoutWeight(1)
      .alignItems(HorizontalAlign.Start)

      Row({ space: 8 }) {
        Text('标签取自 CUE / SACD TOC；同目录 .lrc 与导出歌词会一起写入')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .layoutWeight(1)
        Button(this.busy ? '停止' : '关闭')
          .height(30)
          .fontSize(11)
          .fontColor(this.busy ? T.BAD : T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(8)
          .onClick(() => {
            if (this.busy) {
              this.cancelled = true;
            } else {
              this.onClose();
            }
          })
        Button(this.busy ? '导出中…' : '开始导出')
          .height(30)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 16, right: 16 })
          .enabled(!this.busy && this.tracks.length > 0)
          .onClick(() => {
            this.start();
          })
      }
      .width('100%')
    }
    .width(720)
    .height(420)
    .padding(14)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}
