// 封面搜索：左侧当前封面与曲目信息，右侧候选列表（桌面版对话框结构）。
import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import picker from '@ohos.file.picker';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { CoverService, CoverCandidate, CoverApplyResult } from '../cover/CoverService';
import { T } from '../theme/Tokens';
import { SectionHeader } from '../components/DesktopUI';
import { AppLog } from '../utils/AppLog';
import { fmtSpec } from '../utils/Util';

@Entry
@Component
struct CoverPage {
  @State private track: Track | undefined = undefined;
  @State private candidates: CoverCandidate[] = [];
  @State private message: string = '';
  @State private busy: boolean = false;
  @State private coverPath: string = '';

  aboutToAppear(): void {
    this.initAsync();
  }

  private async initAsync(): Promise<void> {
    const stored = AppStorage.get<string>('seqaudio_cover_path');
    const raw = stored != undefined ? stored : '';
    let paths: string[] = [];
    try {
      paths = JSON.parse(raw) as string[];
    } catch (e) {
      paths = [];
    }
    if (paths.length > 0) {
      const list = await Db.tracksByPaths(paths);
      if (list.length > 0) {
        this.track = list[0];
        if (list[0].coverBlobPath != '') {
          this.coverPath = `file://${list[0].coverBlobPath}`;
        }
      }
    }
  }

  private async search(): Promise<void> {
    if (!this.track || this.busy) {
      return;
    }
    this.busy = true;
    this.message = '正在搜索封面…';
    AppLog.info(`封面搜索：${this.track.artist} - ${this.track.album}`);
    this.candidates = await CoverService.search(this.track.artist, this.track.album);
    this.message = this.candidates.length > 0 ? `找到 ${this.candidates.length} 个候选` : '无结果，可手动指定';
    this.busy = false;
  }

  private async apply(c: CoverCandidate): Promise<void> {
    if (!this.track || this.busy) {
      return;
    }
    this.busy = true;
    const ctx = getContext(this) as common.UIAbilityContext;
    const res: CoverApplyResult = await CoverService.apply(ctx, this.track, c);
    this.message = `${res.ok ? '成功' : '失败'}｜${c.source}｜${res.message}`;
    if (res.ok && this.track.coverBlobPath != '') {
      this.coverPath = `file://${this.track.coverBlobPath}`;
    }
    AppLog.info(`封面嵌入：${res.ok ? '成功' : '失败'} · ${c.source}`);
    this.busy = false;
  }

  private async manual(): Promise<void> {
    if (!this.track || this.busy) {
      return;
    }
    try {
      const dp = new picker.DocumentViewPicker();
      const opts = new picker.DocumentSelectOptions();
      opts.maxSelectNumber = 1;
      const uris = await dp.select(opts);
      if (!uris || uris.length == 0) {
        return;
      }
      this.busy = true;
      const ctx = getContext(this) as common.UIAbilityContext;
      const res = await CoverService.applyLocal(ctx, this.track, uris[0]);
      this.message = `${res.ok ? '成功' : '失败'}｜手动指定｜${res.message}`;
      if (res.ok && this.track.coverBlobPath != '') {
        this.coverPath = `file://${this.track.coverBlobPath}`;
      }
      this.busy = false;
    } catch (e) {
      this.message = '已取消选择';
    }
  }

  build() {
    Column() {
      Row({ space: 10 }) {
        Column({ space: 2 }) {
          Text('封面搜索')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text('iTunes / 网易云多源候选 · 嵌入 FLAC / MP3 / M4A')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
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

      if (this.track) {
        Row({ space: 10 }) {
          Column({ space: 10 }) {
            SectionHeader({ title: '当前封面', hint: '嵌入后写入文件', accent: T.CYAN })
            if (this.coverPath != '') {
              Image(this.coverPath)
                .width(220)
                .height(220)
                .borderRadius(12)
                .objectFit(ImageFit.Cover)
                .border({ width: 1, color: T.LINE })
            } else {
              Column() {
                Text('无封面')
                  .fontSize(11)
                  .fontColor(T.FG_MUTE)
              }
              .width(220)
              .height(220)
              .justifyContent(FlexAlign.Center)
              .backgroundColor(T.INPUT)
              .borderRadius(12)
              .border({ width: 1, color: T.LINE, style: BorderStyle.Dashed })
            }
            Text(this.track.title)
              .fontSize(13)
              .fontWeight(FontWeight.Medium)
              .fontColor(T.FG)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
            Text(`${this.track.artist} · ${this.track.album}`)
              .fontSize(10)
              .fontColor(T.FG_DIM)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
            Text(fmtSpec(this.track))
              .fontSize(10)
              .fontColor(T.CYAN)
            Row({ space: 8 }) {
              Button('搜索封面')
                .layoutWeight(1)
                .height(30)
                .fontSize(10)
                .fontColor(T.ON_ACCENT)
                .backgroundColor(T.ACCENT)
                .borderRadius(8)
                .enabled(!this.busy)
                .onClick(() => {
                  this.search();
                })
              Button('手动指定')
                .height(30)
                .fontSize(10)
                .fontColor(T.FG)
                .backgroundColor(T.GHOST_BG)
                .borderRadius(8)
                .enabled(!this.busy)
                .onClick(() => {
                  this.manual();
                })
            }
            .width('100%')
            Text(this.message)
              .fontSize(10)
              .fontColor(this.message.indexOf('失败') >= 0 ? T.BAD : T.ACCENT)
              .maxLines(2)
              .width('100%')
          }
          .width(280)
          .padding(12)
          .backgroundColor(T.CARD)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)

          Column({ space: 8 }) {
            SectionHeader({ title: '候选封面', hint: `${this.candidates.length} 个`, accent: T.PURPLE })
            if (this.candidates.length == 0) {
              Column({ space: 6 }) {
                Text('搜索后在此选择')
                  .fontSize(11)
                  .fontColor(T.FG_DIM)
                Text('优先使用专辑名匹配；失败时可手动指定本地图片')
                  .fontSize(10)
                  .fontColor(T.FG_MUTE)
              }
              .width('100%')
              .layoutWeight(1)
              .justifyContent(FlexAlign.Center)
            } else {
              List({ space: 4 }) {
                ForEach(this.candidates, (c: CoverCandidate) => {
                  ListItem() {
                    Row({ space: 10 }) {
                      Column() {
                        Text('♪')
                          .fontSize(18)
                          .fontColor(T.FG_MUTE)
                      }
                      .width(44)
                      .height(44)
                      .justifyContent(FlexAlign.Center)
                      .backgroundColor(T.INPUT)
                      .borderRadius(6)
                      Column({ space: 2 }) {
                        Text(`${c.source} · ${c.title}`)
                          .fontSize(11)
                          .fontColor(T.FG)
                          .maxLines(1)
                          .textOverflow({ overflow: TextOverflow.Ellipsis })
                        Text(c.artist)
                          .fontSize(9)
                          .fontColor(T.FG_DIM)
                          .maxLines(1)
                      }
                      .layoutWeight(1)
                      .alignItems(HorizontalAlign.Start)
                      Text(`匹配 ${c.score}`)
                        .fontSize(9)
                        .fontColor(T.ACCENT)
                      Button('嵌入')
                        .height(26)
                        .fontSize(10)
                        .fontColor(T.ACCENT)
                        .backgroundColor(T.CHIP_ON_BG)
                        .borderRadius(6)
                        .enabled(!this.busy)
                        .onClick(() => {
                          this.apply(c);
                        })
                    }
                    .width('100%')
                    .height(56)
                    .padding({ left: 8, right: 8 })
                    .backgroundColor(T.TABLE_HEAD_BG)
                    .borderRadius(8)
                  }
                }, (c: CoverCandidate) => `${c.source}-${c.url}`)
              }
              .width('100%')
              .layoutWeight(1)
              .scrollBar(BarState.Auto)
            }
          }
          .layoutWeight(1)
          .height('100%')
          .padding(12)
          .backgroundColor(T.CARD)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)
        }
        .width('100%')
        .layoutWeight(1)
        .margin({ top: 10 })
      } else {
        Column() {
          Text('未找到曲目')
            .fontSize(13)
            .fontColor(T.FG_MUTE)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      }
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 12, bottom: 10 })
    .backgroundColor(T.BASE)
    .alignItems(HorizontalAlign.Start)
  }
}
