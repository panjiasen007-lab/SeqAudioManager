"""卸载支持：删除安装目录、注册表项与快捷方式。

为什么用 PowerShell 延时删除
----------------------------
Windows 上正在运行的 EXE 无法删除自己的文件（文件被占用）。标准做法是
**把自己交给另一个进程**，等当前进程退出后再删。这里用一段临时 PowerShell
脚本：先 sleep 让主进程退出，再删目录、清注册表、删快捷方式。

为什么要把数据和程序分开处理
----------------------------
``data/`` 里有配置、日志、**备份的原始音频**。用户卸载软件往往只是不想用它了，
未必想连备份一起扔 —— 所以默认把 ``data/`` 挪到 ``%APPDATA%\\SeqAudioManager``，
只有用户明确选择「全部删除」才真的删掉。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: 注册表卸载项（HKCU，免管理员权限）
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SeqAudioManager"

APP_EXE_NAME = "音序管家.exe"


def install_dir_from_registry() -> Optional[Path]:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
        if value:
            return Path(value)
    except OSError:
        return None
    return None


def desktop_shortcut() -> Path:
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "音序管家.lnk"


def start_menu_shortcut() -> Path:
    base = os.environ.get("APPDATA", "")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "音序管家.lnk"


def build_uninstall_script(install_dir: Path, *, keep_data: bool) -> str:
    """生成卸载用的 PowerShell 脚本。"""
    install = str(install_dir).replace("'", "''")
    lines = [
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Start-Sleep -Milliseconds 1500",
        f"$install = '{install}'",
    ]
    if keep_data:
        # 先把 data 挪走，再删目录：反过来就再也找不回备份了
        lines += [
            "$data = Join-Path $install 'data'",
            "$dest = Join-Path $env:APPDATA 'SeqAudioManager'",
            "if (Test-Path $data) {",
            "  New-Item -ItemType Directory -Force -Path $dest | Out-Null",
            "  Copy-Item -Path (Join-Path $data '*') -Destination $dest -Recurse -Force",
            "}",
        ]
    lines += [
        "Remove-Item -LiteralPath $install -Recurse -Force",
        f"Remove-Item -LiteralPath 'HKCU:\\{UNINSTALL_KEY}' -Recurse -Force",
        f"Remove-Item -LiteralPath '{desktop_shortcut()}' -Force",
        f"Remove-Item -LiteralPath '{start_menu_shortcut()}' -Force",
    ]
    return "\n".join(lines)


def run_uninstall(install_dir: Path, *, keep_data: bool) -> bool:
    """把删除动作交给一个独立进程，然后由调用方退出。"""
    script = build_uninstall_script(install_dir, keep_data=keep_data)
    try:
        handle, path = tempfile.mkstemp(prefix="seqaudio-uninstall-", suffix=".ps1")
        with os.fdopen(handle, "w", encoding="utf-8-sig") as stream:
            stream.write(script)
    except OSError as exc:
        log.error("写入卸载脚本失败：%s", exc)
        return False

    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", path,
            ],
            # 只用 CREATE_NO_WINDOW：PowerShell 是控制台程序，需要有个（隐藏的）
            # 控制台才能跑。早先同时加了 DETACHED_PROCESS，两个标志互相冲突，
            # 进程会立刻退出、脚本一句都没执行 —— 表现就是"点了卸载但什么都没发生"。
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
    except OSError as exc:
        log.error("启动卸载进程失败：%s", exc)
        return False
    return True
