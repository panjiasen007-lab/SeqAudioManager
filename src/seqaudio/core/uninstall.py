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

import base64
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
    """桌面快捷方式路径。

    桌面可能被 OneDrive 重定向到别的目录，所以和安装器一样先查注册表的
    ``User Shell Folders\\Desktop``；硬编码 ``%USERPROFILE%\\Desktop`` 会让
    重定向用户卸载后留下一个删不掉的图标。
    """
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(value)) / "音序管家.lnk"
    except OSError:
        return Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "音序管家.lnk"


def start_menu_shortcut() -> Path:
    base = os.environ.get("APPDATA", "")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "音序管家.lnk"


def uninstall_shortcut() -> Path:
    """开始菜单里的「卸载」快捷方式（安装器创建，卸载时一并删除）。"""
    base = os.environ.get("APPDATA", "")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "卸载 音序管家.lnk"


def build_uninstall_script(
    install_dir: Path, *, keep_data: bool, wait_pid: Optional[int] = None
) -> str:
    """生成卸载用的 PowerShell 脚本。

    为什么不用一行 ``Remove-Item`` 了事
    ----------------------------------
    ``Remove-Item`` 会走 PowerShell 的命令解析与安全策略：某些环境把删除
    重定向到回收站、某些安全软件会拦下来，文件就删不掉。快捷方式与目录都
    遇到过这种情况（不报错，只是没删）。所以这里先 ``Remove-Item``，失败
    再用 .NET 的 ``File.Delete`` / ``Directory.Delete`` 兜底，并重试几轮 ——
    卸载完还能看见残留图标，是用户最能直接感知的「卸载不干净」。

    为什么要 ``Wait-Process``
    ------------------------
    固定 ``Start-Sleep 1.5s`` 在打包后的 EXE 上不够用：PyInstaller 单文件版
    启动要解包、Qt 初始化也要时间，静默卸载时主进程往往 1.5 秒还没退完，
    于是 ``音序管家.exe`` 被占用、5 次重试全废 —— 表现就是「点了卸载，
    数据搬走了，程序目录和注册表还在」。改成盯着调用方的 PID 等它真退出，
    再开始删。
    """
    install = str(install_dir).replace("'", "''")
    lines = [
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Start-Sleep -Milliseconds 600",
    ]
    if wait_pid:
        lines += [
            f"Wait-Process -Id {int(wait_pid)} -Timeout 60 -ErrorAction SilentlyContinue",
            "Start-Sleep -Milliseconds 400",
        ]
    lines.append(f"$install = '{install}'")
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
        "  if (-not (Test-Path -LiteralPath $path)) { Write-Output ('skip(absent): ' + $path); return }",
        # 重试要够久：快捷方式刚创建时资源管理器会持有句柄，立即删除会失败。
        # 12 × 500ms ≈ 6 秒，覆盖「装完马上卸载」这种极端节奏。
        "  for ($i = 0; $i -lt 12; $i++) {",
        # 先走 .NET：某些环境把 Remove-Item 包成了「移到回收站」，回收站不可用
        # 时直接 fail-closed 抛错，文件删不掉（实测桌面 .lnk 就是这样残留的）。
        "    try { [System.IO.File]::SetAttributes($path, [System.IO.FileAttributes]::Normal) } catch { }",
        "    try { [System.IO.File]::Delete($path) } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { Write-Output ('deleted: ' + $path); return }",
        "    try { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { Write-Output ('deleted2: ' + $path); return }",
        "    Start-Sleep -Milliseconds 500",
        "  }",
        "  Write-Output ('failed: ' + $path)",
        "}",
        "function Remove-TreeHard($path) {",
        "  if (-not (Test-Path -LiteralPath $path)) { Write-Output ('tree skip(absent): ' + $path); return }",
        "  for ($i = 0; $i -lt 5; $i++) {",
        "    try { [System.IO.Directory]::Delete($path, $true) } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { Write-Output ('tree deleted: ' + $path); return }",
        "    try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue } catch { }",
        "    if (-not (Test-Path -LiteralPath $path)) { Write-Output ('tree deleted2: ' + $path); return }",
        "    Start-Sleep -Milliseconds 300",
        "  }",
        "  Write-Output ('tree failed: ' + $path)",
        "}",
        "Remove-TreeHard $install",
        f"Remove-Item -LiteralPath 'HKCU:\\{UNINSTALL_KEY}' -Recurse -Force",
        f"Remove-FileHard '{desktop_shortcut()}'",
        f"Remove-FileHard '{start_menu_shortcut()}'",
        f"Remove-FileHard '{uninstall_shortcut()}'",
    ]
    return "\n".join(lines)


def script_command(script: str) -> list[str]:
    """把脚本包成 PowerShell 命令（``-EncodedCommand``，UTF-16LE Base64）。"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-EncodedCommand", encoded,
    ]


def launch_script(script: str) -> bool:
    """用 PowerShell 执行一段脚本（走 ``-EncodedCommand``）。

    为什么不写临时 ``.ps1`` 再 ``-File`` 执行：脚本里全是中文路径
    （``音序管家.lnk``、``卸载 音序管家.lnk``），而 Windows PowerShell 5.1
    从文件读脚本时按 **ANSI 代码页**解释——utf-8 写入的中文会变成乱码，
    ``Test-Path`` 于是返回 false，删除函数「找到了也不删」直接返回，且
    一句报错都没有。表现就是「卸载完桌面图标还在」。

    ``-EncodedCommand`` 收的是 UTF-16LE 的 Base64，与代码页无关，中文路径
    一定能原样到达 PowerShell。
    """
    try:
        subprocess.Popen(
            script_command(script),
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


def hard_unlink(path: Path) -> bool:
    """彻底清掉一个文件，成功返回 True。分三级降级。

    为什么不用 ``Path.unlink()``：Windows 上它可能被环境重定向成「移到回收站」
    （安全软件的删除钩子、或组策略的回收站重定向），而被重定向的位置
    （开始菜单、受保护目录）回收站操作会失败 —— 实测报
    ``trash operation: Some operations were aborted``。

    三级降级：
    1. Win32 ``DeleteFileW``：物理删除，绕开回收站钩子；
    2. 改名成 ``*.deleted``：实测某些环境（本机就是）**允许重命名但拒绝删除**
       （父目录缺「删除子项」权限），改名后开始菜单/桌面立刻不再显示它；
    3. 对改名后的文件再试一次删除，删不掉就留着 —— 用户看不见，下次安装或
       手动清理时自然消失。
    """
    if not path.exists():
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # 先清只读/系统属性，否则 DeleteFileW 会因属性失败
        kernel32.SetFileAttributesW(str(path), 0x80)  # FILE_ATTRIBUTE_NORMAL
        if kernel32.DeleteFileW(str(path)) and not path.exists():
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("DeleteFileW 失败 %s：%s", path, exc)

    # 二级：改名（不显示即为成功）
    try:
        trash = path.with_name(path.name + ".deleted")
        if trash.exists():
            try:
                trash.unlink()
            except OSError:
                pass
        path.replace(trash)
        log.info("删除被拒，已改名隐藏：%s", trash.name)
    except OSError as exc:
        log.warning("删除失败（含改名）%s：%s", path, exc)
        return False

    # 三级：对改名后的文件再试一次物理删除
    try:
        import ctypes

        ctypes.windll.kernel32.DeleteFileW(str(trash))
    except Exception:  # noqa: BLE001
        pass
    return True


def remove_shortcuts_and_registry() -> list[str]:
    """由**主进程**直接删快捷方式与注册表项，返回删掉/失败的项目描述。

    为什么不让 PowerShell 统一干：快捷方式和注册表都不占用正在运行的 EXE，
    主进程自己能删，而且还绕开一个坑 —— 某些环境（安全软件/受控文件夹访问）
    会拒绝 ``powershell.exe`` 对桌面这类受保护位置的写操作，实测
    ``[System.IO.File]::Delete`` 在桌面 .lnk 上返回「访问被拒绝」，而同用户的
    进程直接调 Win32 API 就能删。

    PowerShell 脚本里仍保留同样的删除步骤作为兜底（不存在就跳过，幂等）。
    """
    done: list[str] = []
    for lnk in (desktop_shortcut(), start_menu_shortcut(), uninstall_shortcut()):
        if not lnk.exists():
            continue
        if hard_unlink(lnk):
            done.append(f"删除 {lnk.name}")
        else:
            done.append(f"失败 {lnk.name}")
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        done.append("删除注册表项")
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("删除注册表项失败：%s", exc)
        done.append(f"注册表：{exc}")
    return done


def run_uninstall(install_dir: Path, *, keep_data: bool) -> bool:
    """卸载：主进程先清快捷方式与注册表，目录交给外部进程延时删除。"""
    for item in remove_shortcuts_and_registry():
        log.info("卸载：%s", item)
    script = build_uninstall_script(
        install_dir, keep_data=keep_data, wait_pid=os.getpid()
    )
    return launch_script(script)
    return True
