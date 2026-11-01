"""音序管家 安装程序（独立的小型 Qt 应用）。

为什么自己写安装器
------------------
这台机器上没有 Inno Setup / NSIS / 7-Zip，能用的只有 Python。与其为了一个
安装包去装一整套工具链，不如直接用已经在手上的 PySide6 写一个 3 页安装向导：

    欢迎页（含 GPLv3 授权说明） → 选择目录与快捷方式 → 安装进度 → 完成

它做的事情和一个正式安装器没区别：
* 把随包的 7z 解压到目标目录；
* 写 HKCU 卸载项（免管理员权限，符合"每用户安装"的做法）；
* 创建桌面 / 开始菜单快捷方式；
* 卸载走主程序的 ``--uninstall``，不需要额外留一个卸载程序。

打包方式（由 build.py 调用）：
    pyinstaller --onefile --windowed --add-data "<payload>.7z;." installer.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:  # 构建时由 build.py 生成
    from _meta import APP_NAME, PAYLOAD, VERSION
except Exception:  # noqa: BLE001 - 便于直接把本文件当脚本调试
    APP_NAME, VERSION = "音序管家", "1.0.0"
    PAYLOAD = "payload.7z"

EXE_NAME = f"{APP_NAME}.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SeqAudioManager"

BG = "#070a10"
CARD = "#12161f"
LINE = "#232c3b"
FG = "#e8ecf4"
DIM = "#8b95a8"
MUTE = "#5a6478"
ACCENT = "#4d7cfe"
OK = "#34d399"

QSS = f"""
QWidget {{ background: {BG}; color: {FG}; font-family: "Noto Sans SC", "Microsoft YaHei UI", sans-serif; font-size: 13px; }}
QLabel {{ background: transparent; }}
QTextBrowser {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; padding: 8px; color: {DIM}; }}
QLineEdit {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 8px 10px; color: {FG}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QCheckBox {{ spacing: 8px; color: {DIM}; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid #3a4356; border-radius: 4px; background: {CARD}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 8px 16px; color: {FG}; }}
QPushButton:hover {{ border-color: #35405a; }}
QPushButton:disabled {{ color: {MUTE}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #6b93ff; }}
QPushButton#primary:disabled {{ background: #2f4a94; border-color: #2f4a94; color: #93a6d4; }}
QProgressBar {{ background: {CARD}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 3px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 #22d3ee); }}
"""


def resource_path(name: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent / name


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Programs" / "SeqAudioManager"


def desktop_dir() -> Path:
    # 桌面可能被重定向到 OneDrive，先查注册表再回退
    try:
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _ = winreg.QueryValueEx(handle, "Desktop")
        return Path(os.path.expandvars(value))
    except OSError:
        return Path(os.environ.get("USERPROFILE", "")) / "Desktop"


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA", "")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_shortcut(
    link: Path,
    target: Path,
    workdir: Path,
    icon: Optional[Path] = None,
    arguments: str = "",
) -> bool:
    """用 WScript.Shell 建 .lnk。

    没有 pythoncom/win32com，也不想为了一个快捷方式去装 pywin32，
    所以借 PowerShell 调 COM —— 一次性、无依赖。
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    icon_part = f"$s.IconLocation='{icon or target},0';"
    args_part = f"$s.Arguments='{arguments}';" if arguments else ""
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        f"$s.TargetPath='{target}';$s.WorkingDirectory='{workdir}';"
        f"{args_part}{icon_part}$s.Save()"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return link.exists() or result.returncode == 0


def write_registry(install_dir: Path, size_kb: int) -> None:
    import winreg

    exe = install_dir / EXE_NAME
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": VERSION,
        "Publisher": "SeqAudio Manager Contributors",
        "DisplayIcon": str(exe),
        "InstallLocation": str(install_dir),
        "UninstallString": f'"{exe}" --uninstall',
        "QuietUninstallString": f'"{exe}" --uninstall',
        "EstimatedSize": size_kb,
        "NoModify": 1,
        "NoRepair": 1,
        "URLInfoAbout": "https://www.gnu.org/licenses/gpl-3.0.html",
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ, value)


def _parse_version(text: str) -> tuple[int, ...]:
    """把 ``1.0.0`` 解析成 ``(1, 0, 0)``，用于版本比较。"""
    parts: list[int] = []
    for seg in (text or "").strip().split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def detect_installed() -> tuple[str, Optional[Path]]:
    """读注册表判断是否已安装，返回 ``(版本号, 安装目录)``。未安装返回 ``("", None)``。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            version, _ = winreg.QueryValueEx(key, "DisplayVersion")
            try:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
            except OSError:
                location = ""
        return str(version or "").strip(), (Path(location) if location else None)
    except OSError:
        return "", None


def install_mode(installed_version: str) -> str:
    """根据已安装版本判断安装模式。

    * ``fresh``   —— 未安装，全新安装；
    * ``repair``  —— 已装同版本，修复安装（覆盖程序文件，保留配置与备份）；
    * ``upgrade`` —— 已装其它版本，升级安装（保留配置与备份）。
    """
    if not installed_version:
        return "fresh"
    if _parse_version(installed_version) == _parse_version(VERSION):
        return "repair"
    return "upgrade"


class _Progress:
    """把 py7zr 的逐文件回调折算成百分比。"""

    def __init__(self, total: int, emit):
        self.total = max(1, total)
        self.done = 0
        self.emit = emit

    def bump(self, name: str) -> None:
        self.done += 1
        percent = min(99, int(self.done * 100 / self.total))
        self.emit(percent, Path(name).name)


def _make_callback(progress: _Progress):
    from py7zr.callbacks import ExtractCallback

    class Callback(ExtractCallback):
        def report_start_preparation(self) -> None:
            progress.emit(1, "准备解压…")

        def report_start(self, processing_file_path: str, processing_bytes: str) -> None:
            pass

        def report_update(self, decompressed_bytes: str) -> None:
            pass

        def report_end(self, processing_file_path: str, wrote_bytes: str) -> None:
            progress.bump(processing_file_path)

        def report_warning(self, message: str) -> None:
            pass

        def report_postprocess(self) -> None:
            pass

    return Callback()


def _merge_tree(source: Path, target: Path) -> None:
    """递归把 ``source`` 的内容合并进 ``target``，同名文件覆盖。"""
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            _merge_tree(item, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def extract_payload(payload: Path, target: Path, on_progress=None) -> None:
    """把随包 7z 的内容铺到 ``target`` 根目录（全新 / 修复 / 升级通用）。

    不能直接 ``extractall(path=target)``：便携压缩包带一层顶层目录
    （``音序管家-vX.Y.Z-x86-x64/``），直接解压会在安装目录里再套一层。
    旧实现靠「target 下只有一个子目录」来决定要不要把文件提上来，
    但**升级安装时 target 里已有旧文件，这个条件永远不成立**，于是新版本
    被留在子目录里、根目录的 exe 还是旧版 —— 表现为「提示升级成功、
    版本号写成了新版，实际跑的还是旧程序」。

    现在的做法：先解压到临时目录，定位压缩包内的单一顶层目录，
    清空 ``_internal`` 后整体覆盖到 target；``data/``（配置、日志、备份）
    与快捷方式不在压缩包内，天然保留。
    """
    import py7zr

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="seqaudio_setup_") as tmp:
        stage = Path(tmp)
        with py7zr.SevenZipFile(payload, "r") as archive:
            names = archive.getnames()
            progress = _Progress(len(names), on_progress) if on_progress else None
            archive.extractall(
                path=stage,
                callback=_make_callback(progress) if progress else None,
            )

        children = list(stage.iterdir())
        source = children[0] if len(children) == 1 and children[0].is_dir() else stage

        if on_progress is not None:
            on_progress(99, "整理文件…")

        # 程序文件整体替换，否则已删除的模块会残留在 _internal 里与新版本混用
        internal = target / "_internal"
        if internal.exists():
            try:
                shutil.rmtree(internal)
            except OSError as exc:
                raise RuntimeError(
                    f"旧程序文件被占用，请先关闭 {APP_NAME} 再安装：{exc}"
                ) from exc

        # 清理历史安装器留下的整包副本（旧 bug 会在安装目录里塞一层子目录）
        for stale in target.glob(f"{APP_NAME}-v*"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)

        _merge_tree(source, target)


class InstallWorker(QThread):
    progressed = Signal(int, str)
    finished_install = Signal(bool, str)

    def __init__(self, payload: Path, target: Path, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.target = target

    def run(self) -> None:
        try:
            extract_payload(self.payload, self.target, self.progressed.emit)
            self.finished_install.emit(True, "")
        except Exception as exc:  # noqa: BLE001
            self.finished_install.emit(False, f"{type(exc).__name__}: {exc}")


class Installer(QWidget):
    def __init__(self):
        super().__init__()
        self.installed_version, self.installed_dir = detect_installed()
        self.mode = install_mode(self.installed_version)
        # 修复/升级时默认装回原位置，避免在机器上装出两份
        self.target = self.installed_dir or default_install_dir()

        action = {"repair": "修复", "upgrade": "升级"}.get(self.mode, "安装")
        self.setWindowTitle(f"{action} {APP_NAME} {VERSION}")
        self.resize(720, 540)
        self.setMinimumSize(660, 500)

        self.worker: Optional[InstallWorker] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)
        layout.addLayout(self._build_header())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_license_page())
        self.stack.addWidget(self._build_options_page())
        self.stack.addWidget(self._build_progress_page())
        self.stack.addWidget(self._build_done_page())
        layout.addWidget(self.stack, 1)

        layout.addLayout(self._build_footer())

        icon = resource_path("app.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self._refresh_buttons()

    # ------------------------------------------------------------ 头部

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        pix = QPixmap(str(resource_path("app.png")))
        mark = QLabel()
        mark.setFixedSize(52, 52)
        if not pix.isNull():
            mark.setPixmap(pix.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            mark.setStyleSheet(f"background:{ACCENT}; border-radius:10px;")
        row.addWidget(mark)

        column = QVBoxLayout()
        column.setSpacing(2)
        action = {"repair": "修复", "upgrade": "升级"}.get(self.mode, "安装")
        title = QLabel(f"{action} {APP_NAME}")
        title.setStyleSheet(f"font-size:20px; font-weight:700; color:{FG};")
        column.addWidget(title)
        sub = QLabel(f"版本 {VERSION} · 本地音乐元数据批量处理与真假无损鉴定")
        sub.setStyleSheet(f"color:{DIM}; font-size:12px;")
        column.addWidget(sub)
        row.addLayout(column, 1)
        return row

    # ------------------------------------------------------------ 页面

    def _mode_text(self) -> str:
        if self.mode == "repair":
            return (
                f"检测到已安装 v{self.installed_version}，将进行修复安装"
                "（覆盖程序文件，保留配置与备份）。"
            )
        if self.mode == "upgrade":
            return (
                f"检测到已安装 v{self.installed_version}，将升级到 v{VERSION}"
                "（保留配置与备份）。"
            )
        return "未检测到已安装版本，将进行全新安装。"

    def _build_license_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QLabel("请阅读以下授权条款")
        hint.setStyleSheet(f"color:{DIM};")
        layout.addWidget(hint)

        mode_label = QLabel(self._mode_text())
        mode_label.setWordWrap(True)
        mode_label.setStyleSheet(
            f"color:{OK if self.mode != 'fresh' else ACCENT}; font-size:12px;"
        )
        layout.addWidget(mode_label)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(
            f"""
            <div style="line-height:180%; font-size:12px">
            <p><b style="color:{FG}">{APP_NAME} 以 GNU 通用公共许可证第 3 版（GPLv3）授权。</b></p>
            <p>这意味着你可以自由使用、研究、修改和再分发本软件，前提是：
            保留版权声明与协议文本；如果你分发修改版，也必须以 GPLv3 提供源代码。</p>
            <p>本软件<b>不提供任何担保</b>，包括适销性与特定用途适用性的默示担保。</p>
            <p style="color:{MUTE}">安装过程中会写入：<br/>
            · 程序文件 → 你选择的安装目录<br/>
            · 卸载信息 → 当前用户注册表（HKCU，不需要管理员权限）<br/>
            · 快捷方式 → 桌面与开始菜单（可取消）<br/>
            不会上传任何数据，也不会修改系统其他位置。</p>
            </div>
            """
        )
        layout.addWidget(browser, 1)

        self.accept_check = QCheckBox("我已阅读并同意 GNU General Public License v3.0 的条款")
        self.accept_check.toggled.connect(self._refresh_buttons)
        layout.addWidget(self.accept_check)
        return page

    def _build_options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QLabel("选择安装位置")
        hint.setStyleSheet(f"color:{DIM};")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = QLineEdit(str(self.target))
        self.path_edit.textChanged.connect(self._on_path_changed)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        browse.setFixedHeight(36)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self.space_label = QLabel("")
        self.space_label.setStyleSheet(f"color:{MUTE}; font-size:12px;")
        layout.addWidget(self.space_label)
        self._on_path_changed()

        self.desktop_check = QCheckBox("创建桌面快捷方式")
        self.desktop_check.setChecked(True)
        self.startmenu_check = QCheckBox("创建开始菜单快捷方式")
        self.startmenu_check.setChecked(True)
        self.launch_check = QCheckBox("安装完成后立即启动")
        self.launch_check.setChecked(True)
        for box in (self.desktop_check, self.startmenu_check, self.launch_check):
            layout.addWidget(box)

        note = QLabel(
            "安装到当前用户目录，不需要管理员权限。\n"
            + ("修复/升级前请先关闭正在运行的音序管家。\n" if self.mode != "fresh" else "")
            + "卸载：安装目录的「卸载 音序管家」，或「设置 → 应用」。"
        )
        note.setStyleSheet(f"color:{MUTE}; font-size:12px; line-height:160%;")
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addStretch(1)

        self.progress_label = QLabel("正在安装…")
        self.progress_label.setStyleSheet(f"color:{DIM};")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.file_label = QLabel("")
        self.file_label.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        self.file_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.file_label)
        layout.addStretch(1)
        return page

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addStretch(1)

        self.done_title = QLabel("安装完成")
        self.done_title.setStyleSheet(f"font-size:18px; font-weight:700; color:{OK};")
        self.done_title.setAlignment(Qt.AlignCenter)
        self.done_text = QLabel("")
        self.done_text.setStyleSheet(f"color:{DIM};")
        self.done_text.setAlignment(Qt.AlignCenter)
        self.done_text.setWordWrap(True)
        layout.addWidget(self.done_title)
        layout.addWidget(self.done_text)
        layout.addStretch(1)
        return page

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.close)
        self.btn_back = QPushButton("上一步")
        self.btn_back.clicked.connect(self._go_back)
        self.btn_next = QPushButton("下一步")
        self.btn_next.setObjectName("primary")
        self.btn_next.clicked.connect(self._go_next)
        self.btn_next.setFixedHeight(34)
        self.btn_back.setFixedHeight(34)
        self.btn_cancel.setFixedHeight(34)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_back)
        row.addWidget(self.btn_next)
        return row

    # ------------------------------------------------------------ 交互

    def _refresh_buttons(self) -> None:
        # 构造期会被提前调用：_build_options_page 里调 _on_path_changed，
        # 而后者会走到这里 —— 此时底部按钮（btn_next/btn_back）还没建出来。
        # 加上守卫，构造期直接跳过，等 _build_footer 完成后 __init__ 末尾再统一刷新。
        if not hasattr(self, "btn_next"):
            return
        index = self.stack.currentIndex()
        self.btn_back.setVisible(index == 1)
        if index == 0:
            self.btn_next.setText("下一步")
            self.btn_next.setEnabled(self.accept_check.isChecked())
        elif index == 1:
            self.btn_next.setText("安装")
            self.btn_next.setEnabled(bool(self.path_edit.text().strip()))
        elif index == 2:
            self.btn_next.setEnabled(False)
            self.btn_cancel.setEnabled(False)
        else:
            self.btn_cancel.setVisible(False)
            self.btn_back.setVisible(False)
            self.btn_next.setText("完成")
            self.btn_next.setEnabled(True)

    def _go_back(self) -> None:
        if self.stack.currentIndex() > 0:
            self.stack.setCurrentIndex(self.stack.currentIndex() - 1)
            self._refresh_buttons()

    def _go_next(self) -> None:
        index = self.stack.currentIndex()
        if index == 0:
            self.stack.setCurrentIndex(1)
        elif index == 1:
            self._start_install()
            return
        elif index == 3:
            if self.launch_check.isChecked():
                self._launch()
            self.close()
            return
        self._refresh_buttons()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择安装目录", str(self.target.parent))
        if folder:
            self.path_edit.setText(folder)

    def _on_path_changed(self) -> None:
        self.target = Path(self.path_edit.text().strip() or default_install_dir())
        try:
            free = shutil.disk_usage(self.target.anchor or "/").free
            self.space_label.setText(
                f"目标磁盘可用 {free / 1024 / 1024 / 1024:.1f} GB · 需要约 260 MB"
            )
        except OSError:
            self.space_label.setText("")
        self._refresh_buttons()

    def _start_install(self) -> None:
        payload = resource_path(PAYLOAD)
        if not payload.exists():
            QMessageBox.critical(self, "安装包损坏", f"找不到安装数据：{payload}")
            return
        # 只有全新安装才需要确认「目录非空」；修复/升级本来就是覆盖安装，
        # 原安装目录里必然有文件，再弹一次只会让用户多点一下。
        if (
            self.mode == "fresh"
            and self.target.exists()
            and any(self.target.iterdir())
        ):
            reply = QMessageBox.question(
                self,
                "目录非空",
                f"{self.target}\n\n该目录已存在且不为空，继续安装会覆盖同名文件。继续？",
            )
            if reply != QMessageBox.Yes:
                return

        self.stack.setCurrentIndex(2)
        self._refresh_buttons()
        self.worker = InstallWorker(payload, self.target, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.finished_install.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, percent: int, name: str) -> None:
        self.progress.setValue(percent)
        self.file_label.setText(name)

    def _on_finished(self, ok: bool, message: str) -> None:
        if not ok:
            self.progress_label.setText("安装失败")
            self.done_title.setText("安装失败")
            self.done_title.setStyleSheet("font-size:18px; font-weight:700; color:#f87171;")
            self.done_text.setText(message)
            self.stack.setCurrentIndex(3)
            self._refresh_buttons()
            return

        try:
            exe = self.target / EXE_NAME
            # 卸载入口：安装目录里始终放一个「卸载」快捷方式，指向主程序 --uninstall。
            # 不依赖「是否创建开始菜单快捷方式」—— 卸载是必备功能，不能变成可选项。
            create_shortcut(
                self.target / f"卸载 {APP_NAME}.lnk",
                exe,
                self.target,
                exe,
                arguments="--uninstall",
            )
            if self.desktop_check.isChecked():
                create_shortcut(desktop_dir() / f"{APP_NAME}.lnk", exe, self.target, exe)
            if self.startmenu_check.isChecked():
                create_shortcut(start_menu_dir() / f"{APP_NAME}.lnk", exe, self.target, exe)
                create_shortcut(
                    start_menu_dir() / f"卸载 {APP_NAME}.lnk",
                    exe,
                    self.target,
                    exe,
                    arguments="--uninstall",
                )
            size_kb = sum(p.stat().st_size for p in self.target.rglob("*") if p.is_file()) // 1024
            write_registry(self.target, size_kb)
        except OSError as exc:
            message = f"文件已就位，但写注册表/快捷方式失败：{exc}"

        title = {"repair": "修复完成", "upgrade": "升级完成"}.get(self.mode, "安装完成")
        self.progress.setValue(100)
        self.progress_label.setText(title)
        self.done_title.setText(title)
        self.done_text.setText(
            (message + "\n\n" if message else "")
            + f"已安装到：{self.target}\n"
            + "可以随时双击安装目录里的「卸载 音序管家」，或在「设置 → 应用」中卸载。"
        )
        self.stack.setCurrentIndex(3)
        self._refresh_buttons()

    def _launch(self) -> None:
        exe = self.target / EXE_NAME
        if not exe.exists():
            return
        try:
            subprocess.Popen([str(exe)], cwd=str(self.target))
        except OSError as exc:
            QMessageBox.warning(self, "启动失败", str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            reply = QMessageBox.question(self, "安装进行中", "安装尚未完成，确定退出吗？")
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        super().closeEvent(event)


def silent_install(target: Path, *, shortcuts: bool = True) -> int:
    """`--silent <目录>`：无人值守安装，结果写 JSON 到临时目录。

    windowed 打包后没有控制台，返回值只能落盘 —— 这也是我能自动化验证
    安装包是否真的能把文件铺好、注册表写对的唯一办法。
    """
    import json
    import time as _time

    report: dict = {"target": str(target), "ok": False}
    installed_version, _installed_dir = detect_installed()
    report["installed_version"] = installed_version
    report["mode"] = install_mode(installed_version)
    payload = resource_path(PAYLOAD)
    report["payload"] = str(payload)
    if not payload.exists():
        report["error"] = "安装数据缺失"
    else:
        try:
            target.mkdir(parents=True, exist_ok=True)
            started = _time.time()
            extract_payload(payload, target)
            report["extract_seconds"] = round(_time.time() - started, 1)

            exe = target / EXE_NAME
            report["exe"] = str(exe)
            report["exe_exists"] = exe.exists()
            # 记录 exe 大小与时间，便于验证升级后确实换成了新程序
            if exe.exists():
                stat = exe.stat()
                report["exe_size"] = stat.st_size
                report["exe_mtime"] = _time.strftime(
                    "%Y-%m-%d %H:%M:%S", _time.localtime(stat.st_mtime)
                )
            # 安装目录卸载入口：始终创建，不依赖 shortcuts 开关
            report["local_uninstall_lnk"] = create_shortcut(
                target / f"卸载 {APP_NAME}.lnk",
                exe,
                target,
                exe,
                arguments="--uninstall",
            )
            if shortcuts:
                report["desktop_lnk"] = create_shortcut(
                    desktop_dir() / f"{APP_NAME}.lnk", exe, target, exe
                )
                report["startmenu_lnk"] = create_shortcut(
                    start_menu_dir() / f"{APP_NAME}.lnk", exe, target, exe
                )
                report["uninstall_lnk"] = create_shortcut(
                    start_menu_dir() / f"卸载 {APP_NAME}.lnk",
                    exe,
                    target,
                    exe,
                    arguments="--uninstall",
                )
            size_kb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) // 1024
            report["size_mb"] = round(size_kb / 1024, 1)
            write_registry(target, size_kb)
            report["ok"] = exe.exists()
        except Exception as exc:  # noqa: BLE001
            report["error"] = f"{type(exc).__name__}: {exc}"

    out = Path(tempfile.gettempdir()) / "seqaudio_install.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main() -> int:
    if "--silent" in sys.argv:
        index = sys.argv.index("--silent")
        target = Path(sys.argv[index + 1]) if index + 1 < len(sys.argv) else default_install_dir()
        return silent_install(target, shortcuts="--no-shortcuts" not in sys.argv)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    # 复用主程序的字体（若安装包内带了字体目录）
    from PySide6.QtGui import QFontDatabase

    for name in ("NotoSansSC-Regular.otf", "NotoSansSC-Bold.otf", "JetBrainsMono-Regular.ttf"):
        candidate = resource_path(f"fonts/{name}")
        if candidate.exists():
            QFontDatabase.addApplicationFont(str(candidate))

    window = Installer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
