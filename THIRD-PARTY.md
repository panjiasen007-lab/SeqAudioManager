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

## 字体（SIL Open Font License 1.1）

| 字体 | 用途 | 授权文本 |
|---|---|---|
| Noto Sans SC（Regular / Bold） | 界面主字体 | `assets/fonts/OFL-NotoSansSC.txt` |
| JetBrains Mono（Regular / Bold） | 数据列等宽字体 | `assets/fonts/OFL-JetBrainsMono.txt` |

两种字体都随软件分发，未做字形修改（未使用保留字体名）。OFL 允许随任意软件
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
