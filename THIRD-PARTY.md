# 第三方组件与授权声明

音序管家（SeqAudio Manager）本体以 **GNU General Public License v3.0**
（`GPL-3.0-or-later`）授权分发，协议全文见同目录下的 `LICENSE`。
本文件列出随软件分发的第三方组件及其授权，各自的完整授权文本随包提供。

## 运行时依赖

| 组件 | 版本要求 | 授权 | 用途 | 说明 |
|---|---|---|---|---|
| PySide6 / Qt 6 | ≥ 6.7 | LGPL-3.0（或商业授权） | 图形界面 | 动态链接使用，未修改 Qt 源码；按 LGPL 要求可替换为其他版本 |
| mutagen | ≥ 1.47 | GPL-2.0-or-later | 音频标签读写 | 与 GPLv3 兼容 |
| NumPy | ≥ 1.26 | BSD-3-Clause | 频谱分析数值计算 | 宽松授权，仅需保留版权声明 |
| FFmpeg | 7.1（imageio-ffmpeg 内置） | LGPL-2.1-or-later（本构建） | 解码 DSD/APE/WV/FLAC 做频谱分析 | 仅调用其命令行做**解码**，不做再编码；可自行替换为系统 ffmpeg |
| imageio-ffmpeg | ≥ 0.5 | BSD-2-Clause | 提供 ffmpeg 可执行文件 | 分发的是官方 LGPL 构建 |
| sacd_extract | 0.3.9.3-107（Windows x64） | GPL-2.0 | 读取 SACD ISO 曲目表、导出分轨 DSF | 以独立命令行程序调用；授权全文见 `assets/tools/LICENSE-sacd_extract.txt` |

## 字体（SIL Open Font License 1.1）

| 字体 | 用途 | 授权文本 |
|---|---|---|
| Noto Sans SC（Regular / Bold） | 中文无衬线（默认） | `assets/fonts/OFL-NotoSansSC.txt` |
| 霞鹜文楷 LXGW WenKai（Regular） | 中文楷体 | `assets/fonts/OFL-LXGWWenKai.txt` |
| 得意黑 Smiley Sans（Oblique） | 中文展示 | `assets/fonts/OFL-SmileySans.txt` |
| 马善政毛笔楷书 Ma Shan Zheng | 中文书法 | `assets/fonts/OFL-MaShanZheng.txt` |
| 站酷庆科黄油体 ZCOOL QingKe HuangYou | 中文展示 | `assets/fonts/OFL-ZCOOLQingKeHuangYou.txt` |
| Inter（Variable） | 西文 UI | `assets/fonts/OFL-Inter.txt` |
| IBM Plex Sans | 西文 UI | `assets/fonts/OFL-IBMPlexSans.txt` |
| Source Sans 3 | 西文 UI | `assets/fonts/OFL-SourceSans3.txt` |
| Fira Sans | 西文 UI | `assets/fonts/OFL-FiraSans.txt` |
| Public Sans | 西文 UI | `assets/fonts/OFL-PublicSans.txt` |
| Manrope | 西文 UI | `assets/fonts/OFL-Manrope.txt` |
| Work Sans | 西文 UI | `assets/fonts/OFL-WorkSans.txt` |
| Rubik | 西文 UI | `assets/fonts/OFL-Rubik.txt` |
| Lato | 西文 UI | `assets/fonts/OFL-Lato.txt` |
| Noto Sans | 西文 UI | `assets/fonts/OFL-NotoSans.txt` |
| JetBrains Mono（Regular / Bold） | 等宽（默认） | `assets/fonts/OFL-JetBrainsMono.txt` |
| Fira Code | 等宽连字 | `assets/fonts/OFL-FiraCode.txt` |
| IBM Plex Mono | 等宽 | `assets/fonts/OFL-IBMPlexMono.txt` |
| Source Code Pro | 等宽 | `assets/fonts/OFL-SourceCodePro.txt` |
| Cascadia Code | 等宽连字 | `assets/fonts/OFL-CascadiaCode.txt` |

20 款字体都随软件分发，未做字形修改（未使用保留字体名）。OFL 允许随任意软件
免费分发与商用，唯一义务是保留授权文本与版权声明。

## 构建期工具（不随软件分发）

| 工具 | 授权 | 用途 |
|---|---|---|
| PyInstaller | GPL-2.0-or-later + 特别例外条款 | 打包为 Windows 可执行文件 |
| py7zr | LGPL-2.1 | 生成便携版 `.7z` 与安装包载荷 |
| Pillow | HPND / PIL License | 生成应用图标（`tools/make_icon.py`） |
| SciPy | BSD-3-Clause | 图标抠图的形态学运算 |

> PyInstaller 的例外条款明确允许打包后的商业/开源程序以任意授权分发，
> 不影响本项目的 GPLv3 授权选择。

## 数据来源（联网功能，可选）

| 服务 | 用途 | 条款 |
|---|---|---|
| MusicBrainz | 录音/发行版元数据 | CC0 / 公共领域数据 |
| Cover Art Archive | 封面图片 | 各封面版权归原权利人，仅作展示 |
| LRCLIB | 同步歌词 | 开放歌词库，遵循其服务条款 |

**软件不上传任何音频文件或个人信息**，联网仅发送歌名/歌手等检索关键词。
断开网络后全部功能（除联网匹配与歌词下载）仍可正常使用。

## 参考实现（未复制代码）

均衡器的**中心频率**采用音频行业通用的 1/3 倍频程标准频点表（IEC 61260 / ISO 266，20 Hz–20 kHz 共 31 段）。此前版本曾采用半倍频程 20 段频点表：

    22 / 31 / 42 / 63 / 88 / 125 / 177 / 250 / 350 / 500 /
    710 / 1k / 1.4k / 2k / 2.8k / 4k / 5.6k / 8k / 11k / 16k Hz

该频点表在 Diamond Cut 20-Band Graphic EQ 的公开文档、FxSound
（github.com/fxsound2/fxsound-app，仓库 LICENSE 为 **AGPL-3.0**；官网宣传页写作
GPL-3.0，以仓库为准）等项目中公开可见。

本项目**仅采用频点数值**（事实性数据：一组中心频率，不构成对程序表达的复制），
未复制上述项目的任何源代码、预设资源或界面素材，因此不产生授权义务。DSP 仍是
自研的 `BiquadEq`（重叠保留法 + 自算 biquad 级联频响，见
`src/seqaudio/player/dsp.py`）；8 个内置预设曲线是本项目自建的听感起点数值。
