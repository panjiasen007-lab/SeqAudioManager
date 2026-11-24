import { router } from '@kit.ArkUI';
import { PlayerEngine } from '../player/PlayerEngine';
import { fmtDuration } from '../utils/Util';

// 底部迷你播放条：独立组件，仅自身随播放状态刷新
@Component
export struct MiniPlayer {
  @StorageLink('npPlaying') npPlaying: boolean = false;
  @StorageLink('npTitle') npTitle: string = '';
  @StorageLink('npArtist') npArtist: string = '';
  @StorageLink('npPos') npPos: number = 0;
  @StorageLink('npDur') npDur: number = 0;
  private engine: PlayerEngine = PlayerEngine.get();

  build() {
    Column() {
      Progress({
        value: this.npDur > 0 ? this.npPos : 0,
        total: this.npDur > 0 ? this.npDur : 1,
        type: ProgressType.Linear
      })
        .width('100%')
        .height(2)
        .color('#E8B04B')
        .style({ strokeWidth: 2 })

      Row({ space: 12 }) {
        Column({ space: 3 }) {
          Text(this.npTitle == '' ? '未在播放' : this.npTitle)
            .fontSize(14)
            .fontColor('#F5F5F7')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Text(this.npArtist)
            .fontSize(11)
            .fontColor('#9A9AA0')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
          .alignItems(HorizontalAlign.Start)
          .layoutWeight(1)
          .onClick(() => {
            router.pushUrl({ url: 'pages/PlayerPage' });
          })

        Text(`${fmtDuration(this.npPos)} / ${fmtDuration(this.npDur)}`)
          .fontSize(11)
          .fontColor('#9A9AA0')

        Button() {
          if (this.npPlaying) {
            Row({ space: 3 }) {
              Column().width(3.5).height(13).backgroundColor('#F5F5F7')
              Column().width(3.5).height(13).backgroundColor('#F5F5F7')
            }
          } else {
            Image($r('app.media.ic_play')).width(16).height(16)
          }
        }
          .width(36)
          .height(36)
          .type(ButtonType.Circle)
          .backgroundColor('#2A2A30')
          .onClick(() => {
            this.engine.toggle();
          })

        Button() {
          Image($r('app.media.ic_next')).width(16).height(16)
        }
          .width(36)
          .height(36)
          .type(ButtonType.Circle)
          .backgroundColor('#2A2A30')
          .onClick(() => {
            this.engine.nextByUser();
          })
      }
        .width('100%')
        .height(58)
        .padding({ left: 16, right: 16 })
    }
      .width('100%')
      .backgroundColor('#1A1A1E')
      .borderRadius({ topLeft: 12, topRight: 12 })
  }
}
