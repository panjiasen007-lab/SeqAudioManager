# 任务：V1.5.1 SACD ISO 加载与分轨导出

> 规格见 `spec.md`。完成后逐项勾选，并记录验证结果。

## T1. 资源与授权

- [x] 复制 `build\sacd_tool\sacd_extract.exe` → `assets\tools\sacd_extract.exe`
- [x] 新增 `assets\tools\LICENSE-sacd_extract.txt`（GPL-2.0 全文）
- [x] `THIRD-PARTY.md` 增加 sacd_extract 组件条目（GPL-2.0，仅调用命令行）

## T2. 核心模型与探测

- [x] `core/model.py`：`SACD_EXTS`、`SUPPORTED_EXTS` 合并 `.iso`
- [x] `core/model.py`：`CueSheet` 增加 `sacd`、`area`、`channels`
- [x] 新建 `core/sacd.py`：`find_sacd_extract`、`parse_timecode`、
      `parse_print_output`、`read_sacd_text`、`read_folder_track_titles`、
      `read_folder_release_date`、`probe_sacd_iso`
- [x] `core/probe.py`：`probe()` 特判 `.iso` 转 `probe_sacd_iso`
- [x] `core/probe.py`：`check_extension`/`scan_folder` 自动接受 `.iso`
- [x] `core/authenticity.py`：ISO 短路口，避免无意义解码

## T3. 分轨导出

- [x] `pipeline/cue_split.py`：`_safe_filename` 增加 `suffix` 参数
- [x] `pipeline/cue_split.py`：新增 `split_sacd_sheet`
      （一次导出多轨 → 暂存 → 按 `NN - 真实曲名.dsf` 迁移 → 时长校验 → 标签 → 封面）
- [x] `pipeline/cue_split.py`：`split_cue_sheet` 识别 `sheet.sacd` 并分支
- [x] 逐轨标题非占位符时尝试联网歌词（占位符 `Track NN` 跳过）
- [x] `ui/main_window.py`：文件过滤器加 `*.iso`
- [x] `ui/main_window.py`：菜单/命令面板/空目标提示文案改为「CUE / SACD ISO」
- [x] 确认 `SplitCueWorker` 对 12 个同路径条目只导出一次

## T4. 自动化测试

- [x] `tests/test_core.py`：`TestSacdIso` 7 项
      （timecode、-P 解析、SACDText 解码、文本曲目解析、真实样本 probe、
      expand、ISO 鉴定短路）
- [x] `tests/test_ui_flow.py`：过滤器含 `*.iso`；样本扫描 12 行且首行「三套车」
- [x] 运行核心测试：204 项通过，5 项跳过（真实 DFF / E2E 等环境相关）

## T5. 真实样本全流程验收

- [x] 源码版加载样本 ISO：12 轨、专辑/歌手/时长正确，
      曲名为三套车/卜奎大街/…/踏浪，note 显示文本来源
- [x] 分轨导出到临时目录：12 个 DSF（文件名带真实曲名）全部通过时长校验
- [x] 抽查 DSF 标签（TIT2/TPE1/TALB/TRCK/date）与内嵌 `cover.jpg`
- [x] 抽查 DSF 可被 mutagen 打开、采样率 2.8224MHz/2 声道/1bit
- [x] 复制样本并删除 `专辑简介.txt`：标题退回 `Track NN`，加载不报错
- [x] 取消导出：进程终止、临时目录清理、无半截文件

## T6. 打包与清理

- [x] 版本号 `1.5.1`：`src/seqaudio/__init__.py`、`pyproject.toml`、`installer/_meta.py`
- [x] `python build.py --target all`，产出便携 `.7z` 与 `Setup.exe`
- [x] 便携版自检：版本 1.5.1、`sacd_extract` 随包、SACD ISO 解析出 12 轨/真实曲名
- [x] 安装版静默安装到临时目录：自检与 SACD ISO 解析一致
- [x] 打包内 `sacd_extract` 实际导出 1 轨 DSF 成功（完整 12 轨已由源码流程验证）
- [x] 清理 `build/sacd_out`、`build/sacd_tool`、`build/sacd_extract.zip` 及验收临时目录
- [x] 清理仓库根乱码目录 `ÕÔÅôÓë¾ªÌÃÄ¾ÀÖ¶Ó ... SACD`
- [x] 更新 `README.md`、`THIRD-PARTY.md`、`docs/GAP.md`
- [x] `git status` 确认样本目录已忽略、无临时/2GB 文件进入提交
- [x] 本轮不自动发布 GitHub Release（按规格第 3 节）

## T7. 中文路径导入失败修复（用户实测反馈）

- [x] 复现：`sacd_extract` 用 ANSI 读 cwd，中文目录名被丢字符（`音序管家-v1.5.1` → `-v1.5.1`），
      报「目录不存在」，导入变成 1 条失败记录
- [x] `core/sacd.py` 新增 `tool_cwd()`，所有 `-P`/导出子进程使用 ASCII 工作目录
- [x] `_on_scan_finished` 用新分轨替换同路径旧的失败/未展开记录，避免重试永远被去重
- [x] `_on_scan_failed` 增加明确弹窗与状态提示；`_run_print` 同时读取 stdout/stderr
- [x] 新增回归测试 `test_probe_from_chinese_cwd`
- [x] 中文名便携版、中文名安装目录下 `--selftest-scan` 均得到 12 轨
- [x] 中文工作目录下完整导出 12 个 DSF 成功
- [x] 重新打包 1.5.1，刷新 `dist` 解压目录

## T9. 主题重做与停止按钮修复

- [x] 11 套调色板全部重做（底/面板/强调/状态色/正文/次要/弱文字）
- [x] 新增 `ui/backdrop.py`：11 种程序化背景笔触（星云/黑胶纹/花瓣/日光/落叶/
      冰晶/海浪/晚霞/太极/纸纹/网格），按主题与窗口尺寸缓存
- [x] 主按钮/成功/危险按钮按背景亮度自动选深/浅文字；对比度测试全部 ≥ 4.5
- [x] 主题菜单增加底/强调/辅助色渐变小色块
- [x] 停止按钮接入批处理、鉴定、封面、分轨、扫描、重读标签；鉴定停止显示已完成数量
- [x] `scan_folder` 支持 cancel，避免大目录扫描无法中断
- [x] UI 流程测试：11 主题切换、背景渲染、对比度、停止取消、SACD 导入全通过
- [x] 重新打包 1.5.1 便携版/安装版并刷新 dist 解压目录

## T10. 8 语言全界面纯化

- [x] 新增 `ui/ui_text_main.py` / `ui_text_settings.py` / `ui_text_dialogs.py` /
      `ui_text_html.py`，以简体中文原文为键，共 581 条 × 8 语言
- [x] `i18n.py` 增加 `tr_text` / `tr_format` / `tr_dynamic` / `tr_html`、
      带占位符动态文案模板匹配（按首字符分桶）、`localize_tree` 全控件树翻译
- [x] `QEvent.Show` 过滤器：新弹出的对话框/消息框先翻译再显示
- [x] 覆盖主窗口、设置面板、检查器、表格列头与 tooltip、右键菜单、命令面板、
      10 个对话框、曲库体检、清理/隔离区、关于页、首次使用须知、运行时日志与状态摘要
- [x] Qt 消息框标准按钮、输入框占位符、数值单位后缀、字体下拉 tooltip 跟随语言
- [x] 启动时按 `Settings.language` 先设语言，免责声明与主窗口首屏即为所选语言
- [x] 繁体词条简体字扫描通过；英/日/韩/俄/法/德运行时审计无中文残留
- [x] core 测试 205 项、UI 流程测试、`compileall` 全通过
- [x] 重新打包 1.5.1 便携版/安装版，包内自检与 12 轨扫描通过
