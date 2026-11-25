import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import {
  RenameDedupService, RenamePreview, RenameReport, DuplicateGroup
} from '../library/RenameDedupService';

const ACCENT = '#E8B04B';

@Entry
@Component
struct LibraryToolsPage {
  @State template: string = '{artist} - {album} - {track} {title}';
  @State tracks: Track[] = [];
  @State previews: RenamePreview[] = [];
  @State groups: DuplicateGroup[] = [];
  @State reportText: string = '';
  @State busy: boolean = false;

  aboutToAppear(): void {
    this.loadTracks();
  }

  private async loadTracks(): Promise<void> {
    const all = await Db.listTracks('');
    this.tracks = all.filter((t: Track) => t.isCueChild == 0);
  }

  private makePreview(): void {
    this.previews = RenameDedupService.buildPreview(this.tracks, this.template);
    this.reportText = `预览 ${this.previews.length} 项，应用失败时整批回滚`;
  }

  private async applyRename(): Promise<void> {
    if (this.busy || this.previews.length == 0) {
      return;
    }
    this.busy = true;
    const rep: RenameReport = await RenameDedupService.applyRenames(this.previews);
    await this.loadTracks();
    this.reportText = `重命名：成功 ${rep.ok} · 失败 ${rep.failed} · 回滚 ${rep.rolledBack}` +
      (rep.errors.length > 0 ? `｜${rep.errors.slice(0, 3).join(' / ')}` : '');
    this.busy = false;
  }

  private async findGroups(): Promise<void> {
    this.busy = true;
    this.groups = await RenameDedupService.findDuplicates();
    this.reportText = `发现 ${this.groups.length} 组疑似重复（每组保留第一条）`;
    this.busy = false;
  }

  private async markGroups(): Promise<void> {
    if (this.busy || this.groups.length == 0) {
      return;
    }
    this.busy = true;
    const n = await RenameDedupService.markDuplicates(this.groups);
    this.reportText = `已标记重复 ${n} 首（不删除文件）`;
    this.busy = false;
  }

  private async quarantineGroups(): Promise<void> {
    if (this.busy || this.groups.length == 0) {
      return;
    }
    this.busy = true;
    const ctx = getContext(this) as common.UIAbilityContext;
    const n = await RenameDedupService.quarantineDuplicates(this.groups, `${ctx.filesDir}/Quarantine`);
    await this.loadTracks();
    this.reportText = `已隔离 ${n} 首到 Quarantine 目录（未删除）`;
    this.busy = false;
  }

  build() {
    Column() {
      Row() {
        Text('曲库整理').fontSize(20).fontWeight(FontWeight.Bold).fontColor('#F5F5F7')
        Blank()
        Button('返回').fontSize(12).height(30).backgroundColor('#2A2A30').fontColor('#F0F0F2')
          .onClick(() => { router.back(); })
      }
        .width('100%')

      Text(this.reportText).fontSize(11).fontColor(ACCENT).maxLines(2).margin({ top: 6 })
        .visibility(this.reportText == '' ? Visibility.None : Visibility.Visible)

      Scroll() {
        Column({ space: 10 }) {
          Text('批量重命名').fontSize(14).fontColor(ACCENT).margin({ top: 10 })
          Text('变量：{title} {artist} {album} {year} {track} {index}；留空字段自动跳过')
            .fontSize(10).fontColor('#8A8A92')
          TextInput({ text: this.template })
            .height(34).fontSize(12).backgroundColor('#1E1E22').borderRadius(6).width('100%')
            .onChange((v: string) => { this.template = v; })
          Row({ space: 8 }) {
            Button('生成预览').fontSize(11).height(28).backgroundColor('#2A2A30').fontColor('#F0F0F2')
              .onClick(() => { this.makePreview(); })
            Button(this.busy ? '处理中…' : '应用重命名').fontSize(11).height(28)
              .backgroundColor(ACCENT).fontColor('#1A1A1C').enabled(!this.busy && this.previews.length > 0)
              .onClick(() => { this.applyRename(); })
          }
            .width('100%')

          ForEach(this.previews, (p: RenamePreview) => {
            Column({ space: 2 }) {
              Text(p.oldName).fontSize(10).fontColor('#8A8A92').maxLines(1).width('100%')
              Text(`${p.status}：${p.newName}`).fontSize(11)
                .fontColor(p.status == '失败' ? '#E06C6C' : '#B8B8C0').maxLines(1).width('100%')
            }
              .width('100%').padding({ top: 4, bottom: 4 })
          }, (p: RenamePreview) => `${p.id}`)

          Text('重复检测').fontSize(14).fontColor(ACCENT).margin({ top: 12 })
          Text('指纹 = 歌手/专辑/标题 + 时长(±1s) + 采样率/位深；只标记或隔离，不自动删除')
            .fontSize(10).fontColor('#8A8A92')
          Row({ space: 8 }) {
            Button('查找重复').fontSize(11).height(28).backgroundColor('#2A2A30').fontColor('#F0F0F2')
              .enabled(!this.busy).onClick(() => { this.findGroups(); })
            Button('标记重复').fontSize(11).height(28).backgroundColor('#26221A').fontColor(ACCENT)
              .enabled(!this.busy && this.groups.length > 0).onClick(() => { this.markGroups(); })
            Button('隔离重复').fontSize(11).height(28).backgroundColor('#26221A').fontColor(ACCENT)
              .enabled(!this.busy && this.groups.length > 0).onClick(() => { this.quarantineGroups(); })
          }
            .width('100%')

          ForEach(this.groups, (g: DuplicateGroup) => {
            Column({ space: 2 }) {
              Text(`重复组 ${g.tracks.length} 首 · 保留 #${g.keepId}`).fontSize(11).fontColor('#F0F0F2')
              ForEach(g.tracks, (t: Track) => {
                Text(`${t.id == g.keepId ? '保留' : '重复'}｜${t.fileName}｜${fmtDur(t.durationMs)}`)
                  .fontSize(10).fontColor('#8A8A92').maxLines(1).width('100%')
              }, (t: Track) => `${t.id}`)
            }
              .width('100%').padding({ top: 4, bottom: 4 })
          }, (g: DuplicateGroup) => g.key)
        }
          .width('100%').alignItems(HorizontalAlign.Start)
      }
        .layoutWeight(1).margin({ top: 10 })
    }
      .width('100%').height('100%')
      .padding({ left: 24, right: 24, top: 18, bottom: 12 })
      .backgroundColor('#111114').alignItems(HorizontalAlign.Start)
  }
}

function fmtDur(ms: number): string {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${(s % 60 < 10 ? '0' : '')}${s % 60}`;
}
