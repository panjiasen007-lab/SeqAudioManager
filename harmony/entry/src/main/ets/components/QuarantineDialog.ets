// 隔离区管理：列出 files/Quarantine 下的条目，支持恢复、删除与清空。
import { common } from '@kit.AbilityKit';
import { T } from '../theme/Tokens';
import { QuarantineEntry, QuarantineReport, QuarantineService } from '../core/QuarantineService';
import { AppLog } from '../utils/AppLog';

function fmtSize(n: number): string {
  if (n >= 1048576) {
    return `${(n / 1048576).toFixed(1)} MB`;
  }
  if (n >= 1024) {
    return `${Math.round(n / 1024)} KB`;
  }
  return `${n} B`;
}

@CustomDialog
export struct QuarantineDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  onChanged: () => void = () => {
  };
  @State private entries: QuarantineEntry[] = [];
  @State private busy: boolean = false;
  @State private hint: string = '';

  aboutToAppear(): void {
    this.reload();
  }

  private async reload(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    this.entries = await QuarantineService.list(ctx);
  }

  private async restore(e: QuarantineEntry): Promise<void> {
    if (this.busy) {
      return;
    }
    this.busy = true;
    this.hint = `正在恢复 ${e.fileName}…`;
    const ctx = getContext(this) as common.UIAbilityContext;
    const rep: QuarantineReport = await QuarantineService.restore(ctx, e);
    this.busy = false;
    if (rep.ok) {
      this.hint = `已恢复：${e.fileName}`;
      AppLog.info(`隔离区恢复成功：${e.fileName}`);
      this.onChanged();
    } else {
      this.hint = rep.message;
    }
    await this.reload();
  }

  private remove(e: QuarantineEntry): void {
    AlertDialog.show({
      message: `永久删除隔离文件？\n${e.fileName}\n此操作不可恢复。`,
      primaryButton: { value: '取消', action: () => {
      } },
      secondaryButton: {
        value: '删除',
        fontColor: T.BAD,
        action: () => {
          this.doRemove(e);
        }
      }
    });
  }

  private async doRemove(e: QuarantineEntry): Promise<void> {
    const ok = QuarantineService.deleteOne(e);
    this.hint = ok ? `已删除：${e.fileName}` : `删除失败：${e.fileName}`;
    this.onChanged();
    await this.reload();
  }

  private clearAll(): void {
    if (this.entries.length == 0) {
      return;
    }
    AlertDialog.show({
      message: `清空隔离区？共 ${this.entries.length} 个文件将被永久删除，不可恢复。`,
      primaryButton: { value: '取消', action: () => {
      } },
      secondaryButton: {
        value: '清空',
        fontColor: T.BAD,
        action: () => {
          this.doClear();
        }
      }
    });
  }

  private async doClear(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    const n = await QuarantineService.empty(ctx);
    this.hint = `已清空 ${n} 个隔离文件`;
    this.onChanged();
    await this.reload();
  }

  build() {
    Column({ space: 10 }) {
      Row() {
        Column({ space: 2 }) {
          Text('隔离区管理')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(`files/Quarantine · 共 ${this.entries.length} 个文件`)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Button('刷新')
          .height(26)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(6)
          .onClick(() => {
            this.reload();
          })
      }
      .width('100%')

      if (this.entries.length == 0) {
        Column({ space: 6 }) {
          Text('隔离区为空')
            .fontSize(13)
            .fontColor(T.FG_DIM)
          Text('「清理假无损」或右键「移入隔离区」的文件会出现在这里，可随时恢复。')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .textAlign(TextAlign.Center)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        Scroll() {
          Column({ space: 6 }) {
            ForEach(this.entries, (e: QuarantineEntry) => {
              Row({ space: 8 }) {
                Column({ space: 2 }) {
                  Text(e.fileName)
                    .fontSize(11)
                    .fontColor(T.FG)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                  Text(`${e.verdict == '' ? '未判定' : e.verdict} · ${fmtSize(e.sizeBytes)} · ` +
                    `${e.originalPath == '' ? '原路径未知' : e.originalPath}`)
                    .fontSize(9)
                    .fontColor(T.FG_MUTE)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                }
                .layoutWeight(1)
                .alignItems(HorizontalAlign.Start)
                Button('恢复')
                  .height(26)
                  .fontSize(10)
                  .fontColor(T.ACCENT)
                  .backgroundColor(T.GHOST_BG)
                  .borderRadius(6)
                  .enabled(!this.busy)
                  .onClick(() => {
                    this.restore(e);
                  })
                Button('删除')
                  .height(26)
                  .fontSize(10)
                  .fontColor(T.BAD)
                  .backgroundColor(T.DANGER_BG)
                  .border({ width: 1, color: T.DANGER_BORDER })
                  .borderRadius(6)
                  .enabled(!this.busy)
                  .onClick(() => {
                    this.remove(e);
                  })
              }
              .width('100%')
              .padding({ left: 10, right: 10, top: 7, bottom: 7 })
              .backgroundColor(T.CARD)
              .borderRadius(8)
              .border({ width: 1, color: T.LINE_SOFT })
            }, (e: QuarantineEntry) => `${e.stamp}-${e.filePath}`)
          }
          .width('100%')
        }
        .width('100%')
        .layoutWeight(1)
        .scrollBar(BarState.Auto)
      }

      Text(this.hint)
        .fontSize(10)
        .fontColor(this.busy ? T.ACCENT : T.FG_DIM)
        .width('100%')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })

      Row({ space: 8 }) {
        Button('清空隔离区')
          .height(28)
          .fontSize(10)
          .fontColor(T.BAD)
          .backgroundColor(T.DANGER_BG)
          .border({ width: 1, color: T.DANGER_BORDER })
          .borderRadius(7)
          .enabled(this.entries.length > 0 && !this.busy)
          .onClick(() => {
            this.clearAll();
          })
        Blank()
        Button('关闭')
          .height(28)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(7)
          .onClick(() => {
            this.onClose();
          })
      }
      .width('100%')
    }
    .width(760)
    .height(520)
    .padding(14)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}
