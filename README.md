# 音序管家 · SeqAudio Manager

Windows 本地音乐元数据批量处理工具。批量导入 → 联网补全 → 清标签水印 → 安全写标签 → 批量重命名 → 导出报表，一键跑完。

面向本地音乐收藏玩家，**无损优先**：FLAC / APE / WAV / WV / TTA / ALAC / DSF / DFF 全部支持，只改标签区块、绝不重新编码音频。

---

## 技术选型

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.10+ | 音频标签生态最成熟，开发迭代快 |
| 标签引擎 | **mutagen** | 唯一同时覆盖 FLAC/APE/WV/TTA/DSF/DFF/MP4/ID3 的成熟库；`save()` 只替换标签区块，天然满足无损要求 |
| 界面 | **PySide6 (Qt 6)** | Qt 官方维护，Win10/11 高分屏与 125%/150% 缩放表现稳定；LGPL 授权，商业分发无负担 |
| 网络 | 标准库 `urllib` | 目标 API 都是简单 GET，无需 HTTP/2；少一层依赖就少一层 DLL 冲突，打包更小 |
| 打包 | **Nuitka** | 编译成 C，启动快、体积小、杀软误报显著少于 PyInstaller |

被排除的方案：C# + TagLib#（DSD/TTA/WV 支持残缺、DFF 基本没有）、Rust + Tauri + lofty（DFF 仍需手写，周期过长）、C++ + Qt（维护成本高）。

---

## 目录结构

```
src/seqaudio/
├── app.py                  应用入口（高 DPI、主题、异常兜底、免责声明）
├── core/                   纯 Python 核心层，不依赖 Qt，可独立测试
│   ├── model.py            Track / Tags / AudioInfo / CueSheet / MatchCandidate
│   ├── tags.py             标签读写适配：VorbisComment / ID3 / APEv2 / MP4 四套体系
│   ├── probe.py            格式探测、技术参数、流式读取（4GB 文件不进内存）
│   ├── writer.py           安全写入：备份 → 影子副本 → 写后校验 → 原子替换
│   ├── cue.py              CUE 解析（UTF-8/UTF-16/GBK 编码自识别）
│   ├── watermark.py        标签水印清理（强规则整段清空 / 片段删除 / 自定义词）
│   ├── rename.py           模板重命名（非法字符、保留名、循环改名安全）
│   ├── lyrics.py           LRC 清洗、同步歌词判定、同名 .lrc 读写
│   ├── integrity.py        FLAC 结构校验 / 可选 flac.exe 深度 MD5
│   ├── report.py           CSV 报表（UTF-8-SIG，Excel 直开不乱码）
│   ├── config.py           设置与预设方案持久化
│   ├── paths.py            绿色优先的目录解析（EXE 同级 → AppData 回落）
│   └── logging_setup.py    文件日志 + UI 实时日志双通道
├── net/                    联网数据源（全部开源平台）
│   ├── http.py             零依赖 HTTP：重试退避、按主机限流、熔断、磁盘缓存
│   ├── musicbrainz.py      元数据主源（Lucene 查询 + 发行版优选）
│   ├── coverart.py         封面源（体积与次数双重限制）
│   └── lrclib.py           歌词主源（精确匹配 → 模糊检索）
├── pipeline/
│   ├── matcher.py          多源聚合 + 置信度评分（简繁归一、版本惩罚）
│   └── processor.py        单首流程 + 批量调度 + 失败隔离
└── ui/
    ├── main_window.py      主窗口：表格、工具栏、日志、右键菜单、拖拽
    ├── track_model.py      表格模型 + 筛选代理（只看无损 / 只看问题）
    ├── settings_panel.py   参数面板
    ├── dialogs.py          标签编辑 / 候选选择 / 重命名预览
    ├── worker.py           QThread 包装，保证 UI 不阻塞
    ├── disclaimer.py       启动免责声明
    └── theme.py            深色主题 QSS
```

---

## 开发与运行

```bash
pip install -r requirements.txt
python run.py
```

跑核心层自测（不启动界面）：

```bash
python tests/test_core.py
```

生成测试样本音频（纯字节构造，不需要 ffmpeg）：

```bash
python tests/make_samples.py
```

## 打包单文件 EXE

```bash
pip install nuitka zstandard ordered-set
python tools/make_icon.py     # 生成 assets/app.ico
python build.py               # 产物：dist/音序管家.exe
python build.py --debug       # 保留控制台窗口排查问题
python build.py --onedir      # 目录版，启动更快
```

---

## 安全设计（无损部分最关键）

写入流程做了三层保护，任何一层失败都不会留下损坏文件：

1. **备份**：原文件先复制到 `backup/`，保留原盘符与目录结构；
2. **影子副本**：常规文件复制出 `*.seqaudio.tmp`，标签只写在副本上；≥2GB 的大文件改为「原地写入 + 异常自动从备份恢复」，避免双份拷贝撑爆磁盘；
3. **写后校验**：重开副本比对时长 / 采样率 / 声道数，确认音频流未被截断，才 `os.replace` 原子替换。

任何环节抛异常 → 删除副本 → 原文件一个字节都没动。

## 匹配准确性

置信度 = 源相关度 34% + 标题相似度 28% + 歌手 20% + 专辑 10% + 时长 8%，低于 62% 不自动写入，转入「待人工确认」。

针对中文曲库的两处专门处理：

- **简繁归一**：MusicBrainz 中文条目多为繁体（陳奕迅 / 富士山下），不归一会导致相似度偏低；
- **版本惩罚**：候选专辑名含「演唱会 / Live / Remix / 伴奏」等而本地曲目没有时扣 18 分，避免把现场版错配成原版。

## 网络健壮性

- MusicBrainz 严格 1.1s/次限流（官方要求），LRCLIB 1.2s，其余按主机分别计时；
- 429 / 5xx 指数退避重试，404 立即失败不浪费配额；
- **熔断**：某源连续失败 3 次后冷却 180 秒（封面源 2 次 / 300 秒），避免 1000 首的批量任务被单个失效源拖成几小时；
- 检索结果落盘缓存（默认 30 天），重复曲目不重复请求。

---

## 功能完成度

**P0（已实现）**
- 全格式导入解析、流式读取、无损/采样率/位深展示与筛选
- MusicBrainz 元数据 + Cover Art Archive 封面 + LRCLIB 歌词
- 标签水印清理、模板重命名、CSV 报表
- 安全写入（不重编码 + 备份 + 回滚 + 写后校验）
- CUE 解析与整轨标签补全（模式 1）、GBK/UTF-16 乱码修复
- 数据源优先级、限流、熔断、缓存

**P1（部分已实现）**
- 多源聚合置信度评分 ✅
- 封面管理 ✅（替换/清除，导出未做）
- 标签乱码修复 ✅（读取时按编码探测）
- FLAC 结构校验 ✅（深度 MD5 需系统装 flac.exe）
- 预设方案 ✅ / CSV 报表 ✅
- AcoustID 音频指纹 ⬜（接口已预留 `acoustid_key`）
- CUE 分轨导出（模式 2）⬜

**P2（未实现）**
- 音频人声水印降噪、转码、任务断点续处理

## 已知限制

- DFF（DSDIFF）标签只读：mutagen 未实现该容器写回，软件会明确提示「只读格式，已跳过」，不会冒险破坏母带文件；
- 老国产播放器的 GBK 标签：ID3 无 GBK 标准，兼容模式采用 v2.3 + UTF-16（业界通行做法，兼容性最好）；
- 加密格式（ncm/qmc/kgm）不做解密，导入时直接标记跳过。

## 法律边界

仅用于整理用户本人合法拥有或已获授权的音频。启动时强制展示免责声明并需勾选确认。歌词版权归原词曲权利人，本软件不存储、不分发歌词内容。
