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


def uninstall_shortcut() -> Path:
    """开始菜单里的「卸载」快捷方式（安装器创建，卸载时一并删除）。"""
    base = os.environ.get("APPDATA", "")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "卸载 音序管家.lnk"


def build_uninstall_script(install_dir: Path, *, keep_data: bool) -> str:
    """生成卸载用的 PowerShell 脚本。

    为什么不用一行 ``Remove-Item`` 了事
    ----------------------------------
    ``Remove-Item`` 会走 PowerShell 的命令解析与安全策略：某些环境把删除
    重定向到回收站、某些安全软件会拦下来，文件就删不掉。快捷方式与目录都
    遇到过这种情况（不报错，只是没删）。所以这里先 ``Remove-Item``，失败
    再用 .NET 的 ``File.Delete`` / ``Directory.Delete`` 兜底，并重试几轮 ——
    卸载完还能看见残留图标，是用户最能直接感知的「卸载不干净」。
    """
    install = str(install_dir).replace("'", "''")
    lines = [
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Start-Sleep -Milliseconds 1500",
        f"$install = '{install}'",
    ]
    if keep_data:
        # 先把 data 挪走，再删目录：反过来就再也找不回备份了。
        # 放在函数定义之前，保证「复制」一定早于任何删除动作。
        lines += [
            "$data = Join-Path $install 'data'",
            "$dest = Join-Path $env:APPDATA 'SeqAudioManager'",
            "if (Test-Path $data) {",
            "  New-Item -ItemType Directory -Force -Path $dest | Out-Null",
            "  Copy-Item -Path (Join-Path $data '*') -Destination $dest -Recurse -Force",
            "}",
        ]

    lines += [
        "function Remove-FileHard($path) {",
        "  if (-not (Test-Path -LiteralPath $path)) { return }",
        "  for ($i = 0; $i -lt 5; $i++) {",
        "    try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { return }",
        "    try { [System.IO.File]::SetAttributes($path, [System.IO.FileAttributes]::Normal) } catch { }",
        "    try { [System.IO.File]::Delete($path) } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { return }",
        "    Start-Sleep -Milliseconds 400",
        "  }",
        "}",
        "function Remove-TreeHard($path) {",
        "  if (-not (Test-Path -LiteralPath $path)) { return }",
        "  for ($i = 0; $i -lt 5; $i++) {",
        "    try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { return }",
        "    try { [System.IO.Directory]::Delete($path, $true) } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { return }",
        "    Start-Sleep -Milliseconds 400",
        "  }",
        "}",
        "Remove-TreeHard $install",
        f"Remove-Item -LiteralPath 'HKCU:\\{UNINSTALL_KEY}' -Recurse -Force",
        f"Remove-FileHard '{desktop_shortcut()}'",
        f"Remove-FileHard '{start_menu_shortcut()}'",
        f"Remove-FileHard '{uninstall_shortcut()}'",
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
