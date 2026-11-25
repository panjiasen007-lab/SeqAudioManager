import { router } from '@kit.ArkUI';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { LyricsService, LyricsResult, LyricLine } from '../lyrics/LyricsService';

const ACCENT = '#E8B04B';

@Entry
@Component
struct LyricsPage {
  @State track: Track | undefined = undefined;
  @State result: LyricsResult = new LyricsResult();
  @State currentIndex: number = 0;
  @State message: string = '';
  @State busy: boolean = false;
  @StorageLink('npPos') npPos: number = 0;
  private listScroller: Scroller = new Scroller();

  aboutToAppear(): void {
    this.initAsync();
  }

  private async initAsync(): Promise<void> {
    const stored = AppStorage.get<string>('seqaudio_lyrics_path');
    const raw = stored != undefined ? stored : '';
    let paths: string[] = [];
    try {
      paths = JSON.parse(raw) as string[];
    } catch (e) {
      paths = [];
    }
    if (paths.length == 0) {
      return;
    }
    const list = await Db.tracksByPaths(paths);
    if (list.length == 0) {
      return;
    }
    this.track = list[0];
    await this.load(false);
  }

  private async load(force: boolean): Promise<void> {
    if (!this.track || this.busy) {
      return;
    }
    this.busy = true;
    this.message = force ? '正在联网获取歌词…' : '正在载入歌词…';
    this.result = await LyricsService.load(this.track, force);
    this.message = `来源：${this.result.source} · ${this.result.lines.length} 行`;
    this.updateIndex();
    this.busy = false;
  }

  private updateIndex(): void {
    const lines = this.result.lines;
    if (lines.length == 0) {
      this.currentIndex = 0;
      return;
    }
    let idx = 0;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].timeMs <= this.npPos) {
        idx = i;
      } else {
        break;
      }
    }
    if (idx != this.currentIndex) {
      this.currentIndex = idx;
      this.listScroller.scrollToIndex(idx, false, ScrollAlign.CENTER);
    }
  }

  build() {
    Column() {
      Row() {
        Text('歌词').fontSize(20).fontWeight(FontWeight.Bold).fontColor('#F5F5F7')
        Blank()
        if (this.track) {
          Button('联网刷新').fontSize(11).height(28).backgroundColor('#26221A').fontColor(ACCENT)
            .enabled(!this.busy).onClick(() => { this.load(true); })
        }
        Button('返回').fontSize(12).height(28).backgroundColor('#2A2A30').fontColor('#F0F0F2')
          .onClick(() => { router.back(); })
      }
        .width('100%')

      if (this.track) {
        Text(`${this.track.title}｜${this.track.artist}`).fontSize(12).fontColor('#8A8A92')
          .maxLines(1).margin({ top: 6 })
      }
      Text(this.message).fontSize(10).fontColor(ACCENT).margin({ top: 4 })
        .visibility(this.message == '' ? Visibility.None : Visibility.Visible)

      if (this.result.lines.length > 0) {
        List({ scroller: this.listScroller, space: 10 }) {
          ForEach(this.result.lines, (line: LyricLine, i: number) => {
            ListItem() {
              Text(line.text)
                .fontSize(i == this.currentIndex ? 18 : 14)
                .fontColor(i == this.currentIndex ? ACCENT : '#8A8A92')
                .width('100%').textAlign(TextAlign.Center)
                .padding({ top: 6, bottom: 6 })
            }
          }, (line: LyricLine, i: number) => `${line.timeMs}-${i}`)
        }
          .width('100%').layoutWeight(1).margin({ top: 12 })
          .scrollBar(BarState.Off)
      } else {
        Column() {
          Text('暂无歌词').fontSize(16).fontColor('#8A8A92')
          Text('可点「联网刷新」从 lrclib 获取；同目录 .lrc 会自动作为离线降级')
            .fontSize(11).fontColor('#6E6E76').textAlign(TextAlign.Center).margin({ top: 8 })
        }
          .width('100%').layoutWeight(1).justifyContent(FlexAlign.Center)
      }
    }
      .width('100%').height('100%')
      .padding({ left: 24, right: 24, top: 18, bottom: 12 })
      .backgroundColor('#111114').alignItems(HorizontalAlign.Start)
      .onAreaChange((oldArea: Area, newArea: Area) => { this.updateIndex(); })
  }
}
