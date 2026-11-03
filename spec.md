# 规格：V1.5.1 增加 SACD ISO 镜像加载与分轨导出

## 1. 目标

让音序管家可以直接打开/加载 SACD ISO 光盘镜像，把镜像当作「带曲目索引的整轨文件」显示，
并像 WAV+CUE 一样一键导出分轨 DSF。用户验证样本：

```
F:\musicM\样本\赵鹏与惊堂木乐队 低音炮 3套车 SACD iso\赵鹏与惊堂木乐队 低音炮 3套车 SACD.iso
```

验收标准：

1. 添加该 ISO 后列表显示 12 轨，专辑「赵鹏与惊堂木乐队 低音炮 3套车 SACD」，歌手「赵鹏」，
   逐轨歌名为「三套车、卜奎大街、在那东山顶上 … 踏浪」，每轨显示各自时长（04:19、04:18 …）。
2. 逐轨歌名来自同目录 `专辑简介.txt` 的「专辑曲目」段；同目录没有可解析文本时退回 `Track NN`。
3. 「分轨导出」输出 12 个 `.dsf`，文件名 `01 - 三套车.dsf`、`02 - 卜奎大街.dsf` …，
   标签含专辑/歌手/轨号/日期，并嵌入同目录 `cover.jpg`；每轨时长与 `-P` 一致（误差 ≤ 0.6 秒）。
4. 自动化测试通过，打包后的 1.5.1 便携版/安装版也能加载与导出（工具随包分发）。

## 2. 非目标

- 不写入/修改 SACD ISO 本体（镜像只读）。
- 不做 SACD 多声道区导出（固定优先 2 Channel 区；无立体声区时退回第一个区）。
- 不做联网按专辑抓取逐轨歌名；逐轨标题只用本地文本，缺失时用 `Track NN` 占位。
- 不自动发布 GitHub Release；本轮只出本地安装包/绿色版，发布由用户另行确认。

## 3. 关键决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 解码工具 | 随包内置 `sacd_extract.exe`（GPL-2.0） | ffmpeg/mutagen 均不支持 SACD ISO |
| 曲目表 | 调 `sacd_extract -P` 解析 Area/Track/Duration | 已验证 12 轨时间码准确 |
| 专辑/歌手 | 直接读 ISO `SACDText` 原始字节并按 GBK/UTF-8/Big5 解码 | `-P` 输出为有损乱码，不可逆 |
| 逐轨标题 | 解析同目录 `.txt`/`.nfo` 的「专辑曲目」段；失败退回 `Track NN` | SACD TOC 无逐轨标题，但资源包通常带曲目文本 |
| 分轨方式 | 一次命令 `-2 -s -c -t 1,2,…,N -y 临时目录 -i ISO` | 一次读盘导出全部轨，约 10 秒；`-c` 兼容 DST 压缩盘 |
| 文件命名 | 暂存后按 `NN - 标题.dsf` 移到目标目录 | 工具会自建乱码专辑子目录，需要接管命名 |
| 标签/封面 | 复用 `write_tags` + 同目录 `cover.*` | 与 CUE 分轨行为一致 |
| 复用范围 | 复用 `CueSheet`/`CueTrack`/`cue_track`/`SplitCueWorker` | 列表展开、进度、路径去重全部现成 |
| 版本 | `1.5.1` | 1.5.0 已发布，不能覆盖 |

## 4. 代码改动（精确到文件与函数）

### 4.1 新增资源

- 复制 `F:\AudioManager\build\sacd_tool\sacd_extract.exe` → `F:\AudioManager\assets\tools\sacd_extract.exe`。
- 新增 `F:\AudioManager\assets\tools\LICENSE-sacd_extract.txt`（GPL-2.0 全文）。
- `build.py` 无需改 `--add-data`：现有规则已把整个 `assets` 目录打入便携版/安装载荷，
  `assets/tools` 会自动包含。

### 4.2 `src/seqaudio/core/model.py`

```python
SACD_EXTS: frozenset[str] = frozenset({".iso"})
SUPPORTED_EXTS = LOSSLESS_EXTS | LOSSY_EXTS | EXTRA_EXTS | SACD_EXTS
```

`CueSheet` 增加：

```python
sacd: bool = False
area: str = ""
channels: int = 0
```

### 4.3 新建 `src/seqaudio/core/sacd.py`

公开函数：

- `find_sacd_extract() -> Optional[str]`
  - 查找顺序：`paths.asset_path("tools")`、`sys._MEIPASS/assets/tools`、
    `app_dir()/assets/tools`、`app_dir()/tools`、`app_dir()/data/tools`、`shutil.which`。
- `tool_cwd() -> str`
  - 返回一个**纯 ASCII 且存在**的工作目录（优先 `%TEMP%`，其次 `%SystemRoot%\Temp`、`C:\`）；
  - 所有 `sacd_extract` 子进程都用它作为 `cwd`；
  - 原因：该工具用 ANSI 接口读当前目录，路径含中文时会把中文字符丢掉
    （实测把 `音序管家-v1.5.1` 变成 `-v1.5.1`），随后报「目录不存在」并退出 ——
    用户在中文名文件夹里双击便携版就会导入失败。
- `parse_timecode("mm:ss:ff") -> int`（75 帧/秒，返回毫秒）。
- `parse_print_output(text) -> list[SacdArea]`：
  - 按 `Area Information [n]` 分段；
  - 提取 `Area Description`、`Track Count`、`Total play time`、`Speaker config`、
    `Track_Start_Time_Code`、`Duration`；
  - `SACD_AREAS` 选 `Speaker config` 含 `2 Channel` 的区，否则第一个区。
- `read_sacd_text(iso_path) -> tuple[str, str]`：
  - 扫描文件前 4MB 定位 `SACDText`；
  - 大端 u16 偏移 `+0x10/+0x12/+0x20/+0x22` 分别取专辑名/专辑歌手/碟名/碟歌手；
  - 字符串按 NUL 截断，依次尝试 `utf-8 → gb18030 → big5 → cp1252`。
- `read_folder_track_titles(iso_path, expected_count) -> dict[int, str]`：
  - 扫描 ISO 同目录 `*.txt`、`*.nfo`（大小写不敏感），优先文件名含
    `专辑`/`简介`/`info`/`曲目`/`track` 的，其次按文件名排序；
  - 文本依次尝试 `utf-8-sig → gb18030 → big5`；
  - 只解析「专辑曲目 / 曲目 / 曲目列表 / Track List」标题之后的内容；
  - 行格式支持 `01 三套车`、`01. 三套车`、`01 - 三套车`、`1、三套车`、
    `01  三套车`，也支持全角数字/顿号；
  - 遇到连续空行后出现新的「XXX：」小节标题即停止；
  - 只收 1..`expected_count` 的轨道号，重复号取第一次，最终按轨号排序；
  - 返回空 dict 表示没有可用曲目文本。
- `read_folder_release_date(iso_path) -> str`（可选辅助）：
  - 在上面的文本里找 `发行时间：2023年5月` / `发行日期` / `Release Date`，
    规范成 `YYYY-MM` 或 `YYYY`；找不到返回空串。
- `probe_sacd_iso(iso_path) -> Track`：
  - 无工具/`-P` 失败 → `TrackStatus.FAILED`，note 写明原因；
  - 专辑/歌手优先 `SACDText`，为空时用文本元数据兜底；
  - 日期优先文本「发行时间」，没有则用 `-P` 的 `Creation date`；
  - 逐轨标题取 `read_folder_track_titles` 的结果，缺失时 `Track {n:02d}`；
  - 构造 `AudioInfo(fmt="SACD ISO", lossless=True, sample_rate=2_822_400,
    bit_depth=1, channels=2, dsd_rate="DSD64", duration=总时长, file_size=...)`；
  - 构造 `CueSheet(sacd=True, audio_file=iso, title=专辑, album=专辑,
    performer=歌手, date=发行日期, tracks=[CueTrack...])`；
  - 每轨 `start_ms`/`end_ms` 来自 `-P`；
  - note：`SACD ISO：12 轨 · 2 Channel · 52:25`；有文本曲目时
    note `曲目名来自 专辑简介.txt（12/12）`。

### 4.4 `src/seqaudio/core/probe.py`

- `check_extension` 接受 `.iso`（由 `SUPPORTED_EXTS` 覆盖）。
- `probe()` 在 `open_audio` 前特判：

```python
if path.suffix.lower() in SACD_EXTS:
    from .sacd import probe_sacd_iso
    return probe_sacd_iso(path)
```

- `expand_cue_tracks()` 不改：SACD 的多轨 `CueSheet` 自动展开为 12 行。

### 4.5 `src/seqaudio/core/authenticity.py`

`analyze()` 开头增加短路口，避免对 2.2GB ISO 跑 12 次注定失败的 ffmpeg 解码：

```python
if path.suffix.lower() == ".iso":
    result.error = "SACD ISO 为光盘镜像，需先分轨为 DSF/DFF 后鉴定"
    return result
```

### 4.6 `src/seqaudio/pipeline/cue_split.py`

- `_safe_filename(number, title, suffix=".flac")` 增加后缀参数，CUE 调用不变。
- 新增 `split_sacd_sheet(...) -> SplitSummary`：
  1. 校验 `sheet.sacd`、工具、轨数；
  2. 在输出目录下建临时目录；
  3. `Popen([tool, "-2", "-s", "-c", "-t", "1,2,...", "-y", tmp, "-i", iso])`，
     stdout 重定向到日志文件，每 0.2 秒检查 cancel，取消则 terminate；
  4. 递归收集 `*.dsf`，按文件名前缀 `NN` 映射到 `CueTrack`；
  5. 逐轨：已有目标文件先验时长决定复用/覆盖；移到 `NN - 标题.dsf`；
     `probe(dst)` 校验时长（容差 0.6 秒，失败删除并计入 failed）；
  6. 写标签（title/artist/album/albumartist/date/genre/track=`n/12`）；
  7. 同目录 `cover.jpg/folder.jpg/front.jpg/...` 读入并嵌入；
  8. 标题是占位符（`Track NN`）时跳过联网歌词，避免无效请求；
  9. `on_progress(index, total)`；
  10. `finally` 删除临时目录。
- `split_cue_sheet()` 在 `sheet` 校验后分支：

```python
if getattr(sheet, "sacd", False):
    return split_sacd_sheet(track, output_dir, client, ...)
```

### 4.7 `src/seqaudio/ui/main_window.py`

- `add_files` 文件过滤器加入 `*.iso`：
  `"音频 (*.flac ... *.dff *.iso *.mp3 ...)"`。
- 右键菜单与命令面板文案改为「分轨导出（CUE / SACD ISO）…」。
- `run_split_cue` 无目标提示补 SACD ISO；其余复用现有 Worker。
- `SplitCueWorker` 无需改动（已按 path 去重，SACD 的 12 行只导出一次）。

### 4.8 文档与版本

- `src/seqaudio/__init__.py`、`pyproject.toml`、`installer/_meta.py` → `1.5.1`。
- `README.md`：支持格式表加 SACD ISO；说明需内置 `sacd_extract`，输出 DSF。
- `THIRD-PARTY.md`：新增 `sacd_extract 0.3.9.3`（GPL-2.0，仅调用命令行解码）。
- `docs/GAP.md`：记录 SACD 已支持与「逐轨标题由外部匹配」限制。

### 4.9 测试

`tests/test_core.py` 新增 `TestSacdIso`：

1. `test_parse_timecode`：`00:02:00=2000ms`、`04:19:58=259773ms`。
2. `test_parse_print_output`：用真实 `-P` 文本片段断言 12 轨/2 Channel/52:25:58。
3. `test_read_sacd_text`：临时稀疏 ISO，在扇区 511 写入 GBK 的 `SACDText`，
   断言还原「赵鹏与惊堂木乐队 低音炮 3套车 SACD」「赵鹏」。
4. `test_read_folder_track_titles`：用样本 `专辑简介.txt`（存在时）断言 12 首：
   01 三套车 … 12 踏浪；再写临时文本验证 `01.`、`1、`、全角数字、缺轨号、
   越界轨号、重复轨号不误收。
5. `test_probe_real_sample`：样本存在时加载，断言 12 轨、专辑、歌手、
   逐轨标题、每轨时长；样本不存在则 `skipTest`。
6. `test_expand_sacd_tracks`：`expand_cue_tracks([probe])` 得到 12 个 `Track`，
   共享同一 `path`，`cue_track` 0..11，标题为真实曲名。

`tests/test_ui_flow.py`：

7. 文件过滤器包含 `*.iso`（读取 `MainWindow` 的常量或菜单文案）。
8. 扫描样本目录后模型行数为 12，且第 1 行标题为「三套车」（样本存在时）。

## 5. 手工验收（真实样本）

1. 启动源码版，添加样本 ISO：列表 12 行、专辑/歌手/时长正确，
   曲名为三套车、卜奎大街 … 踏浪，note 显示「曲目名来自 专辑简介.txt（12/12）」。
2. 选中 ISO → 分轨导出到临时目录：12 个 DSF，文件名带真实曲名；
   逐个 `probe` 校验时长；用 mutagen 校验标签（TIT2/TPE1/TALB/TRCK）与 APIC 封面；
   抽查 1 个文件频谱/可播放。
3. 打包 1.5.1 便携版，从解压目录启动，重复第 1、2 步（验证 `assets/tools`
   随包生效）。
4. 安装版安装到临时目录，重复第 1、2 步。
5. 复制样本文件夹到临时目录并删除 `专辑简介.txt`，重新加载：标题应退回
   `Track 01…12`，不应报错（验证文本缺失兜底）。
6. 把便携版/安装版放到**中文名目录**（如 `音序管家-v1.5.1-x86-x64`）后启动，
   重新加载并导出 12 轨 —— 验证 `tool_cwd` 修复。
7. 清理调查临时物：`build/sacd_out`、`build/sacd_tool`、`build/sacd_extract.zip`、
   仓库根乱码目录 `ÕÔÅôÓë¾ªÌÃÄ¾ÀÖ¶Ó ... SACD`。

## 6. 风险与兜底

- 中文/非 ASCII 工作目录：`sacd_extract` 会丢字符并拒绝启动。所有调用统一传
  `cwd=tool_cwd()`（ASCII 目录），已在中文名便携版/安装目录下实测导入与导出。
- 非 GBK 的 SACDText（日文/繁体）可能解码偏差：按 UTF-8→GB18030→Big5 顺序尝试，
  失败退回 `cp1252`，并 note「元数据编码未识别」。
- 曲目文本格式不统一：只解析「专辑曲目」小节，且轨号必须在 1..N 内；
  解析不出时静默退回 `Track NN`，不把简介正文误当歌名。
- 文本里的「01 三套车」可能带时长/括号后缀：先按行首轨号+分隔符拆，
  标题保留原文，不做过度清洗。
- DST 压缩盘：命令固定带 `-c`，导出为未压缩 DSF，体积会变大但兼容性最好。
- 磁盘空间：12 轨 DSF 约 2.2GB，导出前在日志提示所需空间。
- 取消：用户取消时终止 `sacd_extract`，清理临时目录，不留在半截文件。

## 7. 需要确认

请确认以下两点后开始执行：

1. 是否同意版本号 `1.5.1`、本轮只出本地安装包/绿色版，不自动发布 GitHub？
2. 是否同意逐轨歌名优先解析同目录 `.txt`/`.nfo` 的「专辑曲目」段，缺失时才用
   `Track NN` 占位，不做联网专辑抓轨？

## 8. 追加：主题重做与停止按钮修复

- 11 套调色板全部重做，新增 `ui/backdrop.py` 程序化背景笔触
  （星云/黑胶纹/花瓣/日光/落叶/冰晶/海浪/晚霞/太极/纸纹/网格）。
- 主按钮、成功/危险按钮按背景亮度自动选择深/浅文字；
  正文、次要、弱文字对比度全部 ≥ 3:1，按钮文字 ≥ 4.5:1。
- 停止按钮接入批处理、无损鉴定、封面、分轨、扫描、重读标签；
  鉴定停止后报告已完成数量；`scan_folder` 支持逐文件取消。

