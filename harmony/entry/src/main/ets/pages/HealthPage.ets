// 曲库体检：桌面式统计卡 + 筛选胶囊 + 明细表。
import { router } from '@kit.ArkUI';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { HealthCheck, HealthReport } from '../health/HealthCheck';
import { T } from '../theme/Tokens';
import { SectionHeader, StatCard } from '../components/DesktopUI';
import { AppLog } from '../utils/AppLog';
import { fmtSpec } from '../utils/Util';

@Entry
@Component
struct HealthPage {
  @State summary: HealthReport = new HealthReport();
  @State items: Track[] = [];
  @State filter: string = 'all';
  @State running: boolean = false;
  @State progressText: string = '';

  aboutToAppear(): void {
    this.load();
  }

  private async load(): Promise<void> {
    this.summary = await HealthCheck.refreshSummary();
    const all = await Db.listTracks('');
    this.items = all.filter((t: Track) => {
      if (t.isCueChild == 1) {
        return false;
      }
      const s = t.healthStatus || 'ok';
      return this.filter == 'all' || s == this.filter;
    });
  }

  private async run(): Promise<void> {
    if (this.running) {
      return;
    }
    this.running = true;
    AppLog.info('开始曲库体检');
    const rep = await HealthCheck.checkAll((done: number, total: number) => {
      this.progressText = `${done}/${total}`;
    });
    await this.load();
    this.running = false;
    this.progressText = `检查完成：正常 ${rep.ok} · 警告 ${rep.warned} · 损坏 ${rep.bad}`;
    AppLog.info(this.progressText);
  }

  private label(s: string): string {
    if (s == 'bad') {
      return '损坏';
    }
    if (s == 'warn') {
      return '警告';
    }
    return '正常';
  }

  @Builder
  chip(label: string, key: string) {
    Button(label)
      .height(28)
      .fontSize(10)
      .fontColor(this.filter == key ? T.ACCENT : T.FG_DIM)
      .backgroundColor(this.filter == key ? T.CHIP_ON_BG : T.GHOST_BG)
      .border({
        width: 1,
        color: this.filter == key ? T.ACCENT_DIM : T.LINE_SOFT
      })
      .borderRadius(14)
      .padding({ left: 12, right: 12 })
      .onClick(() => {
        this.filter = key;
        this.load();
      })
  }

  build() {
    Column() {
      Row({ space: 10 }) {
        Column({ space: 2 }) {
          Text('曲库体检')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text('可解码性 / 时长 / 标签一致性 / 截断与体积异常；只报告，不自动删除')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Button(this.running ? '检查中…' : '重新检查')
          .height(30)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .enabled(!this.running)
          .onClick(() => {
            this.run();
          })
        Button('返回')
          .height(30)
          .fontSize(11)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(8)
          .onClick(() => {
            router.back();
          })
      }
      .width('100%')

      Row({ space: 8 }) {
        StatCard({ label: '已检查', value: this.summary.checked, color: T.FG })
        StatCard({ label: '正常', value: this.summary.ok, color: T.OK })
        StatCard({ label: '警告', value: this.summary.warned, color: T.WARN })
        StatCard({ label: '损坏', value: this.summary.bad, color: T.BAD })
      }
      .width('100%')
      .margin({ top: 10 })

      Row({ space: 8 }) {
        this.chip('全部', 'all')
        this.chip('警告', 'warn')
        this.chip('损坏', 'bad')
        Blank()
        Text(this.progressText)
          .fontSize(10)
          .fontColor(T.ACCENT)
          .maxLines(1)
      }
      .width('100%')
      .margin({ top: 10 })

      Column() {
        Row() {
          Text('状态').width(86).fontSize(10).fontColor(T.FG_MUTE).padding({ left: 10 })
          Text('曲目').layoutWeight(1).fontSize(10).fontColor(T.FG_MUTE)
          Text('规格').width(150).fontSize(10).fontColor(T.FG_MUTE)
          Text('说明').width(260).fontSize(10).fontColor(T.FG_MUTE)
        }
        .width('100%')
        .height(30)
        .backgroundColor(T.TABLE_HEAD_BG)
        .border({ width: { bottom: 1 }, color: { bottom: T.LINE_SOFT } })

        if (this.items.length == 0) {
          Column({ space: 6 }) {
            Text('✓')
              .fontSize(26)
              .fontColor(T.OK)
            Text(this.filter == 'all' ? '还没有检查结果' : '该筛选下没有曲目')
              .fontSize(12)
              .fontColor(T.FG_DIM)
            Text('点右上角「重新检查」开始扫描曲库')
              .fontSize(10)
              .fontColor(T.FG_MUTE)
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        } else {
          List() {
            ForEach(this.items, (t: Track) => {
              ListItem() {
                Row() {
                  Row({ space: 6 }) {
                    Text(this.label(t.healthStatus))
                      .fontSize(9)
                      .fontColor(T.healthColor(t.healthStatus))
                      .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                      .borderRadius(8)
                      .backgroundColor(T.alpha(T.healthColor(t.healthStatus), '1F'))
                      .border({ width: 1, color: T.alpha(T.healthColor(t.healthStatus), '55') })
                  }
                  .width(86)
                  .padding({ left: 10 })
                  Text(t.title == '' ? t.fileName : t.title)
                    .layoutWeight(1)
                    .fontSize(11)
                    .fontColor(T.FG)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                  Text(fmtSpec(t))
                    .width(150)
                    .fontSize(10)
                    .fontColor(T.CYAN)
                    .maxLines(1)
                  Text(t.healthDetail == '' ? '—' : t.healthDetail)
                    .width(260)
                    .fontSize(10)
                    .fontColor(T.FG_MUTE)
                    .maxLines(2)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                }
                .width('100%')
                .height(38)
                .border({ width: { bottom: 1 }, color: { bottom: T.ROW_LINE } })
              }
            }, (t: Track) => `${t.id}-${t.healthStatus}-${t.healthDetail}`)
          }
          .width('100%')
          .layoutWeight(1)
          .scrollBar(BarState.Auto)
        }
      }
      .width('100%')
      .layoutWeight(1)
      .margin({ top: 8 })
      .backgroundColor(T.TABLE_BG)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .clip(true)
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 12, bottom: 10 })
    .backgroundColor(T.BASE)
    .alignItems(HorizontalAlign.Start)
  }
}
