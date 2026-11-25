import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import picker from '@ohos.file.picker';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { CoverService, CoverCandidate, CoverApplyResult } from '../cover/CoverService';

const ACCENT = '#E8B04B';

@Entry
@Component
struct CoverPage {
  @State track: Track | undefined = undefined;
  @State candidates: CoverCandidate[] = [];
  @State message: string = '';
  @State busy: boolean = false;

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
      }
    }
  }

  private async search(): Promise<void> {
    if (!this.track || this.busy) {
      return;
    }
    this.busy = true;
    this.message = '正在搜索封面…';
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
      this.busy = false;
    } catch (e) {
      this.message = '已取消选择';
    }
  }

  build() {
    Column() {
      Row() {
        Text('封面搜索').fontSize(20).fontWeight(FontWeight.Bold).fontColor('#F5F5F7')
        Blank()
        Button('返回').fontSize(12).height(30).backgroundColor('#2A2A30').fontColor('#F0F0F2')
          .onClick(() => { router.back(); })
      }
        .width('100%')

      if (this.track) {
        Text(`${this.track.title}｜${this.track.artist} - ${this.track.album}`)
          .fontSize(12).fontColor('#8A8A92').maxLines(2).margin({ top: 8 })
        Row({ space: 8 }) {
          Button('搜索封面').fontSize(12).height(30).backgroundColor(ACCENT).fontColor('#1A1A1C')
            .enabled(!this.busy).onClick(() => { this.search(); })
          Button('手动指定').fontSize(12).height(30).backgroundColor('#2A2A30').fontColor('#F0F0F2')
            .enabled(!this.busy).onClick(() => { this.manual(); })
        }
          .width('100%').margin({ top: 10 })
        Text(this.message).fontSize(11).fontColor(ACCENT).maxLines(2).margin({ top: 6 })
          .visibility(this.message == '' ? Visibility.None : Visibility.Visible)

        List({ space: 2 }) {
          ForEach(this.candidates, (c: CoverCandidate) => {
            ListItem() {
              Row({ space: 10 }) {
                Column({ space: 3 }) {
                  Text(`${c.source} · ${c.title}`).fontSize(12).fontColor('#F0F0F2').maxLines(1)
                  Text(c.artist).fontSize(10).fontColor('#8A8A92').maxLines(1)
                }
                  .layoutWeight(1).alignItems(HorizontalAlign.Start)
                Text(`匹配 ${c.score}`).fontSize(10).fontColor(ACCENT)
                Button('嵌入').fontSize(10).height(26).backgroundColor('#26221A').fontColor(ACCENT)
                  .enabled(!this.busy).onClick(() => { this.apply(c); })
              }
                .width('100%').padding({ top: 8, bottom: 8, left: 4, right: 4 })
            }
          }, (c: CoverCandidate) => c.source + c.url)
        }
          .width('100%').layoutWeight(1).margin({ top: 10 }).scrollBar(BarState.Auto)
      } else {
        Text('未找到曲目').fontSize(14).fontColor('#8A8A92').margin({ top: 20 })
      }
    }
      .width('100%').height('100%')
      .padding({ left: 24, right: 24, top: 18, bottom: 12 })
      .backgroundColor('#111114').alignItems(HorizontalAlign.Start)
  }
}
