// 歌词：深空底 + 居中滚动，当前行强调色加粗（桌面底部歌词栏的全屏形态）。
import { router } from '@kit.ArkUI';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { LyricsService, LyricsResult, LyricLine } from '../lyrics/LyricsService';
import { T } from '../theme/Tokens';
import { AppLog } from '../utils/AppLog';
import { WindowUtil } from '../utils/WindowUtil';

@Entry
@Component
struct LyricsPage {
  @State private track: Track | undefined = undefined;
  @State private result: LyricsResult = new LyricsResult();
  @State private currentIndex: number = 0;
  @State private message: string = '';
  @State private busy: boolean = false;
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
    AppLog.info(`歌词页：${this.message}`);
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
      Row({ space: 12 }) {
        Column({ space: 2 }) {
          Text('歌词')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(this.track ? `${this.track.title} · ${this.track.artist}` : '未找到曲目')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text(this.message)
          .fontSize(10)
          .fontColor(T.ACCENT)
          .maxLines(1)
        if (this.track) {
          Button('联网刷新')
            .height(30)
            .fontSize(10)
            .fontColor(T.ACCENT)
            .backgroundColor(T.CHIP_ON_BG)
            .border({ width: 1, color: T.ACCENT_DIM })
            .borderRadius(8)
            .enabled(!this.busy)
            .onClick(() => {
              this.load(true);
            })
        }
        Button('返回')
          .height(30)
          .fontSize(11)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(8)
          .onClick(() => {
            WindowUtil.closeSelf('pages/LyricsPage');
          })
      }
      .width('100%')

      Column() {
        if (this.result.lines.length > 0) {
          List({ scroller: this.listScroller, space: 8 }) {
            ForEach(this.result.lines, (line: LyricLine, i: number) => {
              ListItem() {
                Text(line.text)
                  .fontSize(i == this.currentIndex ? 16 : 12)
                  .fontWeight(i == this.currentIndex ? FontWeight.Medium : FontWeight.Normal)
                  .fontColor(i == this.currentIndex ? T.ACCENT : T.FG_MUTE)
                  .width('100%')
                  .textAlign(TextAlign.Center)
                  .padding({ top: 5, bottom: 5 })
              }
            }, (line: LyricLine, i: number) => `${line.timeMs}-${i}`)
          }
          .width('100%')
          .layoutWeight(1)
          .scrollBar(BarState.Off)
          .padding({ left: 120, right: 120, top: 10, bottom: 10 })
        } else {
          Column({ space: 8 }) {
            Text('♪')
              .fontSize(34)
              .fontColor(T.FG_MUTE)
            Text('暂无歌词')
              .fontSize(14)
              .fontColor(T.FG_DIM)
            Text('可点「联网刷新」从 lrclib 获取；同目录 .lrc 会自动作为离线降级')
              .fontSize(10)
              .fontColor(T.FG_MUTE)
              .textAlign(TextAlign.Center)
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        }
      }
      .width('100%')
      .layoutWeight(1)
      .margin({ top: 10 })
      .backgroundColor(T.DOCK_BG)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 12, bottom: 10 })
    .backgroundColor(T.BASE)
    .alignItems(HorizontalAlign.Start)
    .onAreaChange((oldArea: Area, newArea: Area) => {
      this.updateIndex();
    })
  }
}
