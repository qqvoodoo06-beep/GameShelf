"""Сборка GameShelf.exe.

Запуск: build.bat (или .venv\\Scripts\\python build.py [--shortcut]).
Результат: dist\\GameShelf\\GameShelf.exe — папку dist\\GameShelf можно переносить куда угодно целиком.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_DIR = DIST / "GameShelf"
EXE = APP_DIR / "GameShelf.exe"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
_qt_app = None

# Модули, которые приложению не нужны, — чтобы сборка была меньше
EXCLUDES = ["tkinter", "unittest", "pydoc", "doctest", "pdb", "test", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.QtPdf",
            "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtOpenGL"]


def make_icon(path: Path) -> None:
    """Рисует иконку из логотипа приложения (тот же код, что и в окне) и сохраняет .ico с PNG внутри."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication

    from gameshelf.icons import logo_pixmap

    global _qt_app  # без запущенного QGuiApplication Qt не умеет рисовать
    _qt_app = QGuiApplication.instance() or QGuiApplication([])
    images = []
    for size in ICON_SIZES:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        logo_pixmap(size, 1.0).toImage().save(buffer, "PNG")
        images.append((size, bytes(data)))

    # Формат ICO: заголовок, таблица записей по 16 байт, затем сами картинки
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 означает 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.write_bytes(header + entries + blobs)


def make_version_file(path: Path) -> None:
    """Описание файла, которое видно в «Свойства → Подробно». FileDescription — просто «GameShelf»: под этим именем
    программа видна в Диспетчере задач, и оно одинаково понятно на любом языке."""
    from gameshelf.config import APP_VERSION

    numbers = [int(part) for part in APP_VERSION.split(".")] + [0] * 4
    version = tuple(numbers[:4])
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version}, prodvers={version}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'GameShelf'),
      StringStruct('FileDescription', 'GameShelf'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('ProductVersion', '{APP_VERSION}'),
      StringStruct('OriginalFilename', 'GameShelf.exe'),
      StringStruct('InternalName', 'GameShelf'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
""", encoding="utf-8")


def run_pyinstaller(icon: Path, version_file: Path) -> None:
    import PyInstaller.__main__

    args = [
        str(ROOT / "main.py"),
        "--name", "GameShelf",
        "--windowed",            # без чёрного окна консоли
        "--onedir",              # папка с программой: запускается быстрее, чем один большой exe
        "--noconfirm", "--clean",
        "--icon", str(icon),
        "--version-file", str(version_file),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
        "--log-level", "WARN",
    ]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    PyInstaller.__main__.run(args)


# Части Qt, которые PyInstaller кладёт «на всякий случай», а приложению они не нужны:
# программный OpenGL, Qt Quick/QML (их тянет плагин экранной клавиатуры), PDF и лишние форматы картинок.
# Нужное остаётся: qwindows (окна), qjpeg (обложки), qsvg (иконки), qschannelbackend (HTTPS).
PRUNE = [
    "opengl32sw.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll",
    "Qt6QmlWorkerScript.dll", "Qt6Pdf.dll", "Qt6OpenGL.dll", "Qt6VirtualKeyboard.dll",
    "plugins/platforminputcontexts", "plugins/generic", "plugins/networkinformation", "plugins/styles",
    "plugins/platforms/qdirect2d.dll", "plugins/platforms/qminimal.dll",
    "plugins/imageformats/qpdf.dll", "plugins/imageformats/qtiff.dll", "plugins/imageformats/qwebp.dll",
    "plugins/imageformats/qicns.dll", "plugins/imageformats/qtga.dll", "plugins/imageformats/qwbmp.dll",
    "plugins/imageformats/qgif.dll",
    "plugins/tls/qopensslbackend.dll", "plugins/tls/qcertonlybackend.dll",
    "translations",
]


def prune() -> float:
    """Удаляет лишнее из сборки, возвращает освобождённые мегабайты."""
    qt_dir = APP_DIR / "_internal" / "PySide6"
    freed = 0
    for relative in PRUNE:
        target = qt_dir / relative
        if target.is_dir():
            freed += sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            shutil.rmtree(target)
        elif target.is_file():
            freed += target.stat().st_size
            target.unlink()
    # OpenSSL для Qt больше не нужен: HTTPS идёт через встроенный в Windows Schannel
    for name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        target = APP_DIR / "_internal" / name
        if target.exists():
            freed += target.stat().st_size
            target.unlink()
    return freed / (1024 * 1024)


def create_shortcut() -> Path:
    """Ярлык «GameShelf» на рабочем столе."""
    script = (
        "$desktop = [Environment]::GetFolderPath('Desktop');"
        "$link = Join-Path $desktop 'GameShelf.lnk';"
        "$shell = New-Object -ComObject WScript.Shell;"
        "$shortcut = $shell.CreateShortcut($link);"
        f"$shortcut.TargetPath = '{EXE}';"
        f"$shortcut.WorkingDirectory = '{APP_DIR}';"
        f"$shortcut.IconLocation = '{EXE},0';"
        "$shortcut.Description = 'GameShelf';"
        "$shortcut.Save();"
        "Write-Output $link"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=True)
    return Path(result.stdout.strip())


def folder_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build GameShelf.exe / Сборка GameShelf.exe")
    parser.add_argument("--shortcut", action="store_true", help="create a desktop shortcut / создать ярлык на рабочем столе")
    options = parser.parse_args()

    if APP_DIR.exists():
        try:
            shutil.rmtree(APP_DIR)
        except PermissionError:
            print("Could not delete the old build: close GameShelf and run the build again.\n"
                  "Не удалось удалить старую сборку — закройте GameShelf и запустите сборку снова.")
            return 1
    BUILD.mkdir(exist_ok=True)

    print("1/3  Drawing the icon / Рисую иконку…")
    icon = BUILD / "GameShelf.ico"
    make_icon(icon)
    version_file = BUILD / "version_info.txt"
    make_version_file(version_file)

    print("2/3  Building the app, 1–3 minutes / Собираю программу (1–3 минуты)…")
    run_pyinstaller(icon, version_file)
    if not EXE.exists():
        print("Build failed: GameShelf.exe was not created / Сборка не удалась: GameShelf.exe не появился.")
        return 1
    freed = prune()

    print(f"3/3  Done / Готово (pruned / убрано лишнего: {freed:.0f} MB).")
    print(f"     App / Программа: {EXE}")
    print(f"     Folder size / Размер папки: {folder_size_mb(APP_DIR):.0f} MB")
    if options.shortcut:
        print(f"     Shortcut / Ярлык: {create_shortcut()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
