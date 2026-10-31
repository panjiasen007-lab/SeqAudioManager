#!/usr/bin/env python
"""开发期启动脚本（免安装）：python run.py

打包后（PyInstaller / Nuitka）本文件也是推荐入口，此时包已在 sys.path 中，
不做路径注入。
"""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    _src = Path(__file__).resolve().parent / "src"
    if _src.exists():
        sys.path.insert(0, str(_src))

from seqaudio.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
