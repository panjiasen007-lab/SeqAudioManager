// 联网候选选择：来源、标题/歌手/专辑、时长、置信度；默认选中第一条。
import { T } from '../theme/Tokens';
import { MatchCandidate } from '../net/MetadataTypes';
import { similarityLabel } from '../net/TextMatch';
import { verdictColor } from '../core/AuthenticityService';

@CustomDialog
export struct MatchDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  trackTitle: string = '';
  threshold: number = 0.62;
  candidates: MatchCandidate[] = [];
  onApply: (candidate: MatchCandidate) => void = () => {
  };
  @State private selectedIndex: number = 0;

  private confidenceColor(c: number): string {
    if (c >= 0.8) {
      return T.OK;
    }
    if (c >= 0.62) {
      return T.ACCENT;
    }
    if (c >= 0.45) {
      return T.WARN;
    }
    return T.BAD;
  }

  build() {
    Column({ space: 10 }) {
      Row() {
        Column({ space: 2 }) {
          Text('联网匹配候选')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(`${this.trackTitle} · 阈值 ${Math.round(this.threshold * 100)}%`)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text(`${this.candidates.length} 条`)
          .fontSize(10)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')

      if (this.candidates.length == 0) {
        Column({ space: 6 }) {
          Text('没有候选')
            .fontSize(13)
            .fontColor(T.FG_DIM)
          Text('可以检查网络、歌手名或稍后重试')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        List({ space: 4 }) {
          ForEach(this.candidates, (c: MatchCandidate, i: number) => {
            ListItem() {
              Row({ space: 10 }) {
                Text(c.source)
                  .width(74)
                  .fontSize(9)
                  .fontColor(T.CYAN)
                  .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                  .borderRadius(7)
                  .backgroundColor(T.CUE_BG)
                  .border({ width: 1, color: T.alpha(T.CYAN, '33') })
                Column({ space: 2 }) {
                  Text(c.title)
                    .fontSize(12)
                    .fontColor(this.selectedIndex == i ? T.ACCENT : T.FG)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                  Text(`${c.artist} · ${c.album}`)
                    .fontSize(9)
                    .fontColor(T.FG_DIM)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                }
                .layoutWeight(1)
                .alignItems(HorizontalAlign.Start)
                Text(c.duration > 0 ? `${Math.floor(c.duration / 60)}:${this.pad(c.duration)}` : '—')
                  .width(42)
                  .fontSize(9)
                  .fontColor(T.FG_MUTE)
                Row({ space: 4 }) {
                  Text(similarityLabel(c.confidence))
                    .fontSize(10)
                    .fontColor(this.confidenceColor(c.confidence))
                  Progress({ value: c.confidence * 100, total: 100, type: ProgressType.Linear })
                    .width(54)
                    .height(3)
                    .color(this.confidenceColor(c.confidence))
                }
                .width(90)
              }
              .width('100%')
              .height(52)
              .padding({ left: 8, right: 8 })
              .backgroundColor(this.selectedIndex == i ? T.CHIP_ON_BG : T.TABLE_HEAD_BG)
              .borderRadius(8)
              .border({
                width: 1,
                color: this.selectedIndex == i ? T.ACCENT_DIM : T.LINE_SOFT
              })
              .onClick(() => {
                this.selectedIndex = i;
              })
            }
          }, (c: MatchCandidate, i: number) => `${c.source}-${c.recordingId}-${i}`)
        }
        .width('100%')
        .layoutWeight(1)
        .scrollBar(BarState.Auto)
      }

      Row({ space: 8 }) {
        Text('应用会写回标签并尽量补封面/歌词')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .layoutWeight(1)
        Button('取消')
          .height(30)
          .fontSize(11)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(8)
          .onClick(() => {
            this.onClose();
          })
        Button('应用选中')
          .height(30)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 16, right: 16 })
          .enabled(this.candidates.length > 0)
          .onClick(() => {
            if (this.candidates.length > 0) {
              this.onApply(this.candidates[this.selectedIndex]);
              this.onClose();
            }
          })
      }
      .width('100%')
    }
    .width(760)
    .height(480)
    .padding(14)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }

  private pad(sec: number): string {
    const s = Math.floor(sec % 60);
    return s < 10 ? `0${s}` : `${s}`;
  }
}
