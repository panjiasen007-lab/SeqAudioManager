@Entry
@Component
struct Index {
  @State status: string = 'POC 阶段：本地曲库扫描 + FLAC 播放链路验证';

  build() {
    Column({ space: 10 }) {
      Text('音序管家')
        .fontSize(42)
        .fontWeight(FontWeight.Bold)
        .fontColor('#F5F5F7')

      Text('HarmonyOS PC · 分支基线 v1.8.2')
        .fontSize(16)
        .fontColor('#9A9AA0')

      Divider()
        .color('#333338')
        .margin({ top: 12, bottom: 12 })

      Text(this.status)
        .fontSize(15)
        .fontColor('#C8C8CE')

      Blank()

      Button('开始扫描曲库')
        .width(220)
        .height(44)
        .backgroundColor('#E8B04B')
        .fontColor('#1A1A1C')
        .fontWeight(FontWeight.Medium)
        .onClick(() => {
          this.status = '扫描能力待接入（POC 第一步）';
        })
    }
    .width('100%')
    .height('100%')
    .padding(40)
    .alignItems(HorizontalAlign.Start)
    .backgroundColor('#111114')
  }
}
