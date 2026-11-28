// 频谱查看：原生分析返回的 1/3 抽样频谱 + 截止线 + 判定理由。
import { T } from '../theme/Tokens';
import { AuthenticityResult, verdictColor } from '../core/AuthenticityService';

@CustomDialog
export struct SpectrumDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  title: string = '';
  result: AuthenticityResult = new AuthenticityResult();
  private canvasSettings: RenderingContextSettings = new RenderingContextSettings(true);
  private ctx: CanvasRenderingContext2D = new CanvasRenderingContext2D(this.canvasSettings);

  private draw(): void {
    const w = this.ctx.width;
    const h = this.ctx.height;
    if (w <= 0 || h <= 0) {
      return;
    }
    this.ctx.clearRect(0, 0, w, h);
    this.ctx.fillStyle = T.LOG;
    this.ctx.fillRect(0, 0, w, h);
    const padL = 44;
    const padR = 12;
    const padT = 12;
    const padB = 24;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const freqs = this.result.freqs;
    const db = this.result.db;
    if (freqs.length < 2 || db.length < 2) {
      return;
    }
    let peak = -200;
    for (const v of db) {
      peak = Math.max(peak, v);
    }
    const top = peak + 5;
    const bottom = peak - 110;
    const xOf = (f: number): number => {
      const lf = Math.log10(Math.max(20, f) / 20) / Math.log10(20000 / 20);
      return padL + Math.max(0, Math.min(1, lf)) * plotW;
    };
    const yOf = (v: number): number => {
      const t = (Math.max(bottom, Math.min(top, v)) - bottom) / (top - bottom);
      return padT + (1 - t) * plotH;
    };
    // 网格
    this.ctx.lineWidth = 1;
    this.ctx.strokeStyle = 'rgba(38,52,74,0.75)';
    const marks: number[] = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
    for (const f of marks) {
      const x = xOf(f);
      this.ctx.beginPath();
      this.ctx.moveTo(x, padT);
      this.ctx.lineTo(x, padT + plotH);
      this.ctx.stroke();
    }
    for (let i = 0; i <= 4; i++) {
      const y = padT + i * plotH / 4;
      this.ctx.beginPath();
      this.ctx.moveTo(padL, y);
      this.ctx.lineTo(padL + plotW, y);
      this.ctx.stroke();
    }
    // 频谱填充
    this.ctx.beginPath();
    this.ctx.moveTo(xOf(freqs[0]), padT + plotH);
    for (let i = 0; i < freqs.length; i++) {
      this.ctx.lineTo(xOf(freqs[i]), yOf(db[i]));
    }
    this.ctx.lineTo(xOf(freqs[freqs.length - 1]), padT + plotH);
    this.ctx.closePath();
    this.ctx.fillStyle = 'rgba(78,201,190,0.16)';
    this.ctx.fill();
    this.ctx.beginPath();
    for (let i = 0; i < freqs.length; i++) {
      const x = xOf(freqs[i]);
      const y = yOf(db[i]);
      if (i == 0) {
        this.ctx.moveTo(x, y);
      } else {
        this.ctx.lineTo(x, y);
      }
    }
    this.ctx.strokeStyle = T.CYAN;
    this.ctx.lineWidth = 1.6;
    this.ctx.stroke();
    // 截止线
    const cutoff = this.result.metrics.cutoffHz;
    if (cutoff > 0) {
      const x = xOf(cutoff);
      this.ctx.strokeStyle = verdictColor(this.result.verdict);
      this.ctx.lineWidth = 1;
      this.ctx.beginPath();
      this.ctx.moveTo(x, padT);
      this.ctx.lineTo(x, padT + plotH);
      this.ctx.stroke();
      this.ctx.fillStyle = verdictColor(this.result.verdict);
      this.ctx.font = '10vp sans-serif';
      this.ctx.fillText(`${(cutoff / 1000).toFixed(1)}k`, Math.min(x + 4, w - 40), padT + 12);
    }
    // 坐标文字
    this.ctx.fillStyle = T.FG_MUTE;
    this.ctx.font = '9vp sans-serif';
    for (const f of marks) {
      const label = f >= 1000 ? `${f / 1000}k` : `${f}`;
      this.ctx.fillText(label, xOf(f) - 6, h - 8);
    }
    this.ctx.fillText(`${Math.round(top)}`, 6, padT + 10);
    this.ctx.fillText(`${Math.round(bottom)}`, 2, padT + plotH);
  }

  build() {
    Column({ space: 8 }) {
      Row() {
        Column({ space: 2 }) {
          Text('频谱查看')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(this.title)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text(this.result.verdict)
          .fontSize(11)
          .fontColor(verdictColor(this.result.verdict))
          .padding({ left: 9, right: 9, top: 3, bottom: 3 })
          .borderRadius(9)
          .backgroundColor(T.alpha(T.PANEL, '14'))
          .border({ width: 1, color: verdictColor(this.result.verdict) })
      }
      .width('100%')

      Canvas(this.ctx)
        .width('100%')
        .height(300)
        .borderRadius(10)
        .border({ width: 1, color: T.LINE_SOFT })
        .onReady(() => {
          this.draw();
        })

      Row({ space: 16 }) {
        Text(`截止 ${(this.result.metrics.cutoffHz / 1000).toFixed(2)}kHz`)
          .fontSize(10)
          .fontColor(T.CYAN)
        Text(`陡度 ${this.result.metrics.steepOct.toFixed(0)}dB/oct`)
          .fontSize(10)
          .fontColor(T.FG_DIM)
        Text(`带宽 ${Math.round(this.result.metrics.ratio * 100)}%`)
          .fontSize(10)
          .fontColor(T.FG_DIM)
        Text(`有效位深 ${this.result.metrics.effDepth}bit`)
          .fontSize(10)
          .fontColor(T.FG_DIM)
      }
      .width('100%')

      Scroll() {
        Column({ space: 4 }) {
          ForEach(this.result.reasons, (r: string) => {
            Text(`· ${r}`)
              .fontSize(10)
              .fontColor(T.FG_DIM)
              .lineHeight(16)
              .width('100%')
          }, (r: string) => r)
          if (this.result.reasons.length == 0) {
            Text(this.result.error != '' ? this.result.error : '无判据说明')
              .fontSize(10)
              .fontColor(T.FG_MUTE)
          }
        }
        .width('100%')
      }
      .width('100%')
      .layoutWeight(1)
      .scrollBar(BarState.Auto)

      Row() {
        Text('抽样 4 段 × 4s（DSD 2s）平均频谱；对数频率轴')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
        Blank()
        Button('关闭')
          .height(30)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 18, right: 18 })
          .onClick(() => {
            this.onClose();
          })
      }
      .width('100%')
    }
    .width(900)
    .height(560)
    .padding(14)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}
