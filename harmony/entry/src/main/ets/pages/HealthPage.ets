import { router } from '@kit.ArkUI';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { HealthCheck, HealthReport } from '../health/HealthCheck';

const ACCENT = '#E8B04B';

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
    const rep = await HealthCheck.checkAll((done: number, total: number) => {
      this.progressText = `${done}/${total}`;
    });
    await this.load();
    this.running = false;
    this.progressText = `检查完成：正常 ${rep.ok} · 警告 ${rep.warned} · 损坏 ${rep.bad}`;
  }

  private colorOf(s: string): string {
    if (s == 'bad') return '#E06C6C';
    if (s == 'warn') return '#E8B04B';
    return '#7FB77E';
  }

  build() {
    Column() {
      Row() {
        Text('健康检查').fontSize(20).fontWeight(FontWeight.Bold).fontColor('#F5F5F7')
        Blank()
        Button('返回').fontSize(12).height(30).backgroundColor('#2A2A30').fontColor('#F0F0F2')
          .onClick(() => { router.back(); })
      }
        .width('100%')

      Text(`共 ${this.summary.checked} 首 · 正常 ${this.summary.ok} · 警告 ${this.summary.warned} · 损坏 ${this.summary.bad}`)
        .fontSize(12).fontColor('#8A8A92').margin({ top: 8 })

      Row({ space: 8 }) {
        Button('全部').fontSize(11).height(28).backgroundColor(this.filter == 'all' ? '#26221A' : '#2A2A30')
          .fontColor(this.filter == 'all' ? ACCENT : '#F0F0F2')
          .onClick(() => { this.filter = 'all'; this.load(); })
        Button('警告').fontSize(11).height(28).backgroundColor(this.filter == 'warn' ? '#26221A' : '#2A2A30')
          .fontColor(this.filter == 'warn' ? ACCENT : '#F0F0F2')
          .onClick(() => { this.filter = 'warn'; this.load(); })
        Button('损坏').fontSize(11).height(28).backgroundColor(this.filter == 'bad' ? '#26221A' : '#2A2A30')
          .fontColor(this.filter == 'bad' ? ACCENT : '#F0F0F2')
          .onClick(() => { this.filter = 'bad'; this.load(); })
        Blank()
        Button(this.running ? '检查中…' : '重新检查').fontSize(11).height(28)
          .backgroundColor(ACCENT).fontColor('#1A1A1C').enabled(!this.running)
          .onClick(() => { this.run(); })
      }
        .width('100%').margin({ top: 10 })

      Text(this.progressText).fontSize(11).fontColor(ACCENT).margin({ top: 6 })
        .visibility(this.progressText == '' ? Visibility.None : Visibility.Visible)

      List({ space: 2 }) {
        ForEach(this.items, (t: Track) => {
          ListItem() {
            Column({ space: 3 }) {
              Row({ space: 8 }) {
                Text(t.healthStatus == 'bad' ? '✕' : (t.healthStatus == 'warn' ? '!' : '✓'))
                  .fontSize(13).fontColor(this.colorOf(t.healthStatus)).width(16)
                Text(t.title).fontSize(13).fontColor('#F0F0F2').layoutWeight(1).maxLines(1)
                Text(t.healthStatus.toUpperCase()).fontSize(10).fontColor(this.colorOf(t.healthStatus))
              }
                .width('100%')
              Text(t.healthDetail == '' ? t.path : `${t.fileName}｜${t.healthDetail}`)
                .fontSize(10).fontColor('#8A8A92').maxLines(2).width('100%')
            }
              .width('100%').padding({ top: 8, bottom: 8, left: 6, right: 6 })
          }
        }, (t: Track) => `${t.id}`)
      }
        .width('100%').layoutWeight(1).margin({ top: 10 }).scrollBar(BarState.Auto)
    }
      .width('100%').height('100%')
      .padding({ left: 24, right: 24, top: 18, bottom: 12 })
      .backgroundColor('#111114').alignItems(HorizontalAlign.Start)
  }
}
