#!/usr/bin/env python
"""开发期启动脚本（免安装）：python run.py [--no-asio]

打包后（PyInstaller / Nuitka）本文件也是推荐入口，此时包已在 sys.path 中，
不做路径注入。

为什么这里要自己设一次 ``SD_ENABLE_ASIO``
------------------------------------------
sounddevice 在 **import 的那一刻** 就 ``dlopen`` 了某一个 PortAudio 二进制：
环境里存在 ``SD_ENABLE_ASIO`` 时加载 ``libportaudio64bit-asio.dll``，否则加载
``libportaudio64bit.dll``。换句话说，环境变量的生效时机是「模块首次被 import」，
晚一行就永远不生效（改的是已经 dlopen 过的库，白改）。

本文件的 ``from seqaudio.app import main`` 会一路 import 到 sounddevice，所以
必须**在那一行之前**把环境准备好。``seqaudio.app._bootstrap_asio()`` 也会再调
一次（幂等），但那时已经晚了 —— 那是给「别人 import 本包」的路径兜底，不是给
本入口用的。

关闭方式只有两条：命令行 ``--no-asio`` 或环境变量 ``SEQAUDIO_NO_ASIO=1``
（设计 §1.4：开关不放 settings —— Settings.load() 需要 import 与磁盘 IO，
比「import sounddevice 之前」这个硬时限晚，且那种开关只能"下次启动生效"，
用户会以为当场就关了，半生效等于说谎）。
"""

import os
import sys
from pathlib import Path

#: 关闭 ASIO 的命令行开关（大小写敏感，与 ``--selftest`` 等既有开关风格一致）
NO_ASIO_FLAGS = ("--no-asio", "--no_asio")

#: ``SEQAUDIO_NO_ASIO`` 认这些值（其余一律当作"没关"）
_TRUTHY = ("1", "true", "yes", "on")


def _asio_disabled(argv) -> bool:
    """命令行或环境变量是否要求关闭 ASIO。"""
    for token in argv:
        if str(token).strip().lower() in NO_ASIO_FLAGS:
            return True
    return str(os.environ.get("SEQAUDIO_NO_ASIO", "")).strip().lower() in _TRUTHY


def _bootstrap_asio(argv=None) -> bool:
    """在 import sounddevice 之前决定启用哪个 PortAudio 构建（幂等）。

    Returns:
        是否要求 ASIO 构建（``True`` 表示已把 ``SD_ENABLE_ASIO`` 放进环境）。

    注意 ``--no-asio`` 时必须**删掉**变量而不是写成 ``"0"``：sounddevice 的判据
    是 ``'SD_ENABLE_ASIO' in os.environ``（只看存在与否），写 0 一样会加载 ASIO
    构建 —— 那会让"关闭"变成一句空话。

    同时写/清 ``SEQAUDIO_ASIO_DISABLED`` 标记：``devices.sounddevice()`` 会
    ``setdefault("SD_ENABLE_ASIO", "1")`` 兜底，而"变量不在"和"用户关了"对它
    毫无区别，没有这个标记，一次设备枚举就会把用户的 ``--no-asio`` 推翻。
    """
    if _asio_disabled(sys.argv if argv is None else argv):
        os.environ.pop("SD_ENABLE_ASIO", None)
        os.environ["SEQAUDIO_ASIO_DISABLED"] = "1"
        return False
    os.environ.pop("SEQAUDIO_ASIO_DISABLED", None)
    os.environ.setdefault("SD_ENABLE_ASIO", "1")
    return True


if not getattr(sys, "frozen", False):
    _src = Path(__file__).resolve().parent / "src"
    if _src.exists():
        sys.path.insert(0, str(_src))

# 必须在 import seqaudio 之前（见模块 docstring）
_bootstrap_asio()

from seqaudio.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
