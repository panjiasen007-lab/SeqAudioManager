#!/usr/bin/env python
"""开发期启动脚本（免安装）：python run.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from seqaudio.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
