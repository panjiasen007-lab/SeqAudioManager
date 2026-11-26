// 批量整理：桌面式双卡（批量重命名 / 重复检测）+ 预览表。
import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import {
  RenameDedupService, RenamePreview, RenameReport, DuplicateGroup
} from '../library/RenameDedupService';
import { T } from '../theme/Tokens';
import { SectionHeader } from '../components/DesktopUI';
import { AppLog } from '../utils/AppLog';
import { WindowUtil } from '../utils/WindowUtil';
import { fmtDuration } from '../utils/Util';

@Entry
@Component
struct LibraryToolsPage {
  @State private template: string = '{artist} - {album} - {track} {title}';
  @State private tracks: Track[] = [];
  @State private previews: RenamePreview[] = [];
  @State private cuePaths: string[] = [];
  @State private groups: DuplicateGroup[] = [];
  @State private reportText: string = '';
  @State private busy: boolean = false;
  @State private scopeDir: string = '';

  aboutToAppear(): void {
    const scoped = AppStorage.get<string>('seqaudio_rename_dir');
    if (scoped != undefined && scoped != '') {
      this.scopeDir = scoped;
    }
    AppStorage.setOrCreate('seqaudio_rename_dir', '');
    this.loadTracks();
  }

  private dirOf(path: string): string {
    const i = path.lastIndexOf('/');
    return i > 0 ? path.substring(0, i) : path;
  }

  private shortDir(): string {
    if (this.scopeDir == '') {
      return '全库';
    }
    const i = this.scopeDir.lastIndexOf('/');
    return i >= 0 ? this.scopeDir.substring(i + 1) : this.scopeDir;
  }

  private async loadTracks(): Promise<void> {
    const all = await Db.listTracks('');
    const cuePaths: string[] = [];
    for (const t of all) {
      if (t.isCueChild == 1 && t.cuePath != '') {
        cuePaths.push(t.cuePath);
      }
    }
    this.cuePaths = cuePaths;
    let visible = all.filter((t: Track) => t.isCueChild == 0);
    if (this.scopeDir != '') {
      visible = visible.filter((t: Track) => this.dirOf(t.path) == this.scopeDir);
    }
    this.tracks = visible;
  }

  private resetScope(): void {
    this.scopeDir = '';
    this.previews = [];
    this.reportText = '';
    this.loadTracks();
  }

  private makePreview(): void {
    this.previews = RenameDedupService.buildPreview(this.tracks, this.template, this.cuePaths);
    const pending = this.previews.filter((p: RenamePreview) => p.status == '待应用').length;
    this.reportText = `预览 ${this.previews.length} 项（待应用 ${pending}），应用失败时整批回滚`;
  }

  private async applyRename(): Promise<void> {
    if (this.busy || this.previews.length == 0) {
      return;
    }
    this.busy = true;
    const rep: RenameReport = await RenameDedupService.applyRenames(this.previews);
    await this.loadTracks();
    this.makePreview();
    this.reportText = `重命名：成功 ${rep.ok} · 失败 ${rep.failed} · 回滚 ${rep.rolledBack}` +
      (rep.errors.length > 0 ? `｜${rep.errors.slice(0, 3).join(' / ')}` : '');
    AppLog.info(this.reportText);
    this.busy = false;
  }

  private async findGroups(): Promise<void> {
    this.busy = true;
    this.groups = await RenameDedupService.findDuplicates();
    this.reportText = `发现 ${this.groups.length} 组疑似重复（每组保留第一条）`;
    AppLog.info(this.reportText);
    this.busy = false;
  }

  private async markGroups(): Promise<void> {
    if (this.busy || this.groups.length == 0) {
      return;
    }
    this.busy = true;
    const n = await RenameDedupService.markDuplicates(this.groups);
    this.reportText = `已标记重复 ${n} 首（不删除文件）`;
    AppLog.info(this.reportText);
    this.busy = false;
  }

  private async quarantineGroups(): Promise<void> {
    if (this.busy || this.groups.length == 0) {
      return;
    }
    this.busy = true;
    const ctx = getContext(this) as common.UIAbilityContext;
    const n = await RenameDedupService.quarantineDuplicates(this.groups,
      `${ctx.filesDir}/Quarantine`);
    await this.loadTracks();
    this.groups = [];
    this.reportText = `已隔离 ${n} 首到 Quarantine 目录（未删除）`;
    AppLog.warn(this.reportText);
    this.busy = false;
  }

  private statusColor(s: string): string {
    if (s == '失败') {
      return T.BAD;
    }
    if (s == '无需变更' || s == 'CUE 母文件跳过') {
      return T.FG_MUTE;
    }
    return T.ACCENT;
  }

  @Builder
  cardHeader(title: string, hint: string, accent: string) {
    SectionHeader({ title: title, hint: hint, accent: accent })
  }

  build() {
    Column() {
      Row({ space: 10 }) {
        Column({ space: 2 }) {
          Text('批量整理')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text('模板重命名 · 失败整批回滚 · 重复只标记/隔离，不自动删除')
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
            WindowUtil.closeSelf('pages/LibraryToolsPage');
          })
      }
      .width('100%')

      Row({ space: 10 }) {
        Column({ space: 8 }) {
          this.cardHeader('批量重命名', '模板预览 · CUE 母文件保护', T.ACCENT)
          Row({ space: 8 }) {
            Text(this.scopeDir == '' ? '范围：全库' :
              `范围：${this.shortDir()}（${this.tracks.length} 首）`)
              .fontSize(9)
              .fontColor(this.scopeDir == '' ? T.FG_MUTE : T.CYAN)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .layoutWeight(1)
            if (this.scopeDir != '') {
              Button('改为全库')
                .height(22)
                .fontSize(9)
                .fontColor(T.FG_DIM)
                .backgroundColor(T.GHOST_BG)
                .borderRadius(5)
                .onClick(() => {
                  this.resetScope();
                })
            }
          }
          .width('100%')
          Text('变量：{title} {artist} {album} {year} {track} {index}；留空字段自动跳过并折叠分隔符')
            .fontSize(9)
            .fontColor(T.FG_MUTE)
            .lineHeight(15)
          TextInput({ text: this.template })
            .height(32)
            .fontSize(11)
            .fontColor(T.FG)
            .backgroundColor(T.INPUT)
            .borderRadius(6)
            .border({ width: 1, color: T.LINE_SOFT })
            .onChange((v: string) => {
              this.template = v;
            })
          Row({ space: 8 }) {
            Button('生成预览')
              .height(28)
              .fontSize(10)
              .fontColor(T.FG)
              .backgroundColor(T.GHOST_BG)
              .borderRadius(6)
              .onClick(() => {
                this.makePreview();
              })
            Button(this.busy ? '处理中…' : '应用重命名')
              .height(28)
              .fontSize(10)
              .fontColor(T.ON_ACCENT)
              .backgroundColor(T.ACCENT)
              .borderRadius(6)
              .enabled(!this.busy && this.previews.length > 0)
              .onClick(() => {
                this.applyRename();
              })
            Blank()
            Text(`${this.previews.length} 项`)
              .fontSize(9)
              .fontColor(T.FG_MUTE)
          }
          .width('100%')
        }
        .layoutWeight(1)
        .padding(12)
        .backgroundColor(T.CARD)
        .borderRadius(T.R_CARD)
        .border({ width: 1, color: T.LINE_SOFT })
        .alignItems(HorizontalAlign.Start)

        Column({ space: 8 }) {
          this.cardHeader('重复检测', '指纹：歌手/专辑/标题 + 时长 ±1s + 规格', T.PURPLE)
          Text('只标记或隔离，不自动删除；隔离后可在文件管理里还原。')
            .fontSize(9)
            .fontColor(T.FG_MUTE)
            .lineHeight(15)
          Row({ space: 8 }) {
            Button('查找重复')
              .height(28)
              .fontSize(10)
              .fontColor(T.FG)
              .backgroundColor(T.GHOST_BG)
              .borderRadius(6)
              .enabled(!this.busy)
              .onClick(() => {
                this.findGroups();
              })
            Button('标记重复')
              .height(28)
              .fontSize(10)
              .fontColor(T.ACCENT)
              .backgroundColor(T.CHIP_ON_BG)
              .borderRadius(6)
              .enabled(!this.busy && this.groups.length > 0)
              .onClick(() => {
                this.markGroups();
              })
            Button('隔离重复')
              .height(28)
              .fontSize(10)
              .fontColor(T.WARN)
              .backgroundColor(T.alpha(T.WARN, '1A'))
              .borderRadius(6)
              .enabled(!this.busy && this.groups.length > 0)
              .onClick(() => {
                this.quarantineGroups();
              })
          }
          .width('100%')
          Text(this.groups.length > 0 ? `${this.groups.length} 组` : '尚未检测')
            .fontSize(9)
            .fontColor(T.FG_MUTE)
        }
        .layoutWeight(1)
        .padding(12)
        .backgroundColor(T.CARD)
        .borderRadius(T.R_CARD)
        .border({ width: 1, color: T.LINE_SOFT })
        .alignItems(HorizontalAlign.Start)
      }
      .width('100%')
      .margin({ top: 10 })

      Text(this.reportText)
        .fontSize(10)
        .fontColor(T.ACCENT)
        .maxLines(2)
        .margin({ top: 8 })
        .visibility(this.reportText == '' ? Visibility.None : Visibility.Visible)

      Row() {
        Text('原文件名').layoutWeight(1).fontSize(10).fontColor(T.FG_MUTE).padding({ left: 10 })
        Text('预览 / 状态').layoutWeight(1).fontSize(10).fontColor(T.FG_MUTE)
        Text('结果').width(80).fontSize(10).fontColor(T.FG_MUTE)
      }
      .width('100%')
      .height(30)
      .margin({ top: 8 })
      .backgroundColor(T.TABLE_HEAD_BG)
      .border({ width: { bottom: 1 }, color: { bottom: T.LINE_SOFT } })

      List() {
        ForEach(this.previews, (p: RenamePreview) => {
          ListItem() {
            Row() {
              Text(p.oldName)
                .layoutWeight(1)
                .fontSize(10)
                .fontColor(T.FG_MUTE)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .padding({ left: 10 })
              Text(p.newName)
                .layoutWeight(1)
                .fontSize(10)
                .fontColor(T.FG_DIM)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Text(p.status)
                .width(80)
                .fontSize(9)
                .fontColor(this.statusColor(p.status))
            }
            .width('100%')
            .height(32)
            .border({ width: { bottom: 1 }, color: { bottom: T.ROW_LINE } })
          }
        }, (p: RenamePreview) => `${p.id}-${p.status}-${p.newName}`)
      }
      .width('100%')
      .layoutWeight(1)
      .scrollBar(BarState.Auto)
      .backgroundColor(T.TABLE_BG)
      .borderRadius(T.R_CARD)
      .border({ width: 1, color: T.LINE_SOFT })
      .clip(true)

      if (this.groups.length > 0) {
        Text(`重复组：${this.groups.map((g: DuplicateGroup) =>
          `${g.tracks.length} 首（保留 #${g.keepId}）`).join(' · ')}`)
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .maxLines(2)
          .margin({ top: 6 })
          .width('100%')
      }
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 12, bottom: 10 })
    .backgroundColor(T.BASE)
    .alignItems(HorizontalAlign.Start)
  }
}
