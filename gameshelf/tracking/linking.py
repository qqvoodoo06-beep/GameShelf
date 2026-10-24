"""Связь библиотеки с диском: где установлена игра, как её запустить, перенос часов из Steam."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from ..database import Library
from ..i18n import tr
from ..models import Game
from .installed import InstalledGame, match_installed, normalize_name, scan_all, steam_playtimes

AUTO_SOURCES = ("steam", "epic", "gog")
RESCAN_SECONDS = 300  # раз в 5 минут заново смотрим, что установлено: игру могли поставить или удалить


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a or "")) == os.path.normcase(os.path.normpath(b or ""))


def _drive_available(path: str) -> bool:
    """Диск (флешка, второй диск, BitLocker) подключён? Если нет — привязку не снимаем."""
    anchor = Path(path).anchor
    return bool(anchor) and Path(anchor).exists()


class InstallLinker(QObject):
    """Находит, где установлены игры из библиотеки, и привязывает их — сама, без участия пользователя."""

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._installed: list[InstalledGame] | None = None
        self._scanned_at = 0.0
        self._busy = False
        library.changed.connect(self.link_all)  # новую игру в библиотеке сразу ищем на диске
        self._timer = QTimer(self)
        self._timer.setInterval(RESCAN_SECONDS * 1000)
        self._timer.timeout.connect(self.link_all)  # программа может неделями работать в трее
        self._timer.start()

    def installed(self) -> list[InstalledGame]:
        if self._installed is None or time.monotonic() - self._scanned_at > RESCAN_SECONDS:
            self._scan()
        return self._installed

    def _scan(self) -> None:
        self._installed = scan_all()
        self._scanned_at = time.monotonic()

    def rescan(self) -> int:
        """Заново ищет установленные игры (кнопка в настройках). Возвращает число новых привязок."""
        self._scan()
        return self.link_all()

    def link_all(self) -> int:
        if self._busy:
            return 0
        self._busy = True
        try:
            with self._library.batch():
                return self._link_all()
        finally:
            self._busy = False

    def _link_all(self) -> int:
        installed = self.installed()
        by_id = {(item.source, item.source_id): item for item in installed}
        linked = 0
        for game in self._library.games():
            # Указанное вручную и отвязанное пользователем не трогаем
            if game.install_source in ("manual", "none"):
                continue
            if game.is_linked and game.install_source in AUTO_SOURCES:
                current = by_id.get((game.install_source, game.install_id))
                if current:
                    if not (_same_path(current.install_dir, game.install_dir)
                            and _same_path(current.exe_path, game.exe_path)):
                        self._link(game, current)  # игру перенесли в другую библиотеку
                    continue
                if not _drive_available(game.install_dir):
                    continue  # диск с игрой сейчас не подключён — подождём
            match = match_installed(game, installed)
            if match:
                if not game.is_linked:
                    linked += 1
                self._link(game, match)
            elif game.is_linked:
                self._library.set_install(game.key, "")  # игру удалили с компьютера
        return linked

    def _link(self, game: Game, item: InstalledGame) -> None:
        self._library.set_install(game.key, item.source, item.source_id, item.install_dir, item.exe_path)

    def copies(self, game: Game) -> list[InstalledGame]:
        """Все установленные копии игры — бывает, что она стоит и в Steam, и в GOG."""
        if self._installed is None:
            return []
        name = normalize_name(game.name)
        appid = str(game.id) if game.source == "steam" else ""
        return [item for item in self._installed
                if (item.source == "steam" and item.source_id == appid)
                or (item.source == game.install_source and item.source_id == game.install_id)
                or (name and normalize_name(item.name) == name)]

    def find(self, game: Game, rescan: bool = False) -> InstalledGame | None:
        """Поиск по кнопке «Найти автоматически» — работает и для отвязанных игр."""
        if rescan:
            self._scan()  # вдруг игру установили только что
        return match_installed(game, self.installed())


# Ярлык игры из GOG Galaxy, Battle.net и т. п. указывает на сам лаунчер (а игру выбирает параметрами запуска).
# Привязать к нему игру нельзя: время шло бы всё время, пока лаунчер висит в трее, а «Играть» открывал бы лаунчер.
LAUNCHER_EXES = {
    "galaxyclient.exe", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "battle.net.exe",
    "battle.net launcher.exe", "upc.exe", "ubisoftconnect.exe", "uplay.exe", "eadesktop.exe", "ealauncher.exe",
    "origin.exe", "amazon games.exe", "playnite.desktopapp.exe", "playnite.fullscreenapp.exe", "gamecenter.exe",
    "itch.exe",
}


def is_launcher_exe(path: str) -> bool:
    return os.path.basename(path or "").lower() in LAUNCHER_EXES


def steam_appid_from_url(url: str) -> str:
    """appid из ссылки ярлыка Steam (.url): steam://rungameid/1145360 или steam://run/1145360. "" — не Steam."""
    url = (url or "").strip().lower()
    for prefix in ("steam://rungameid/", "steam://run/"):
        if url.startswith(prefix):
            appid = url[len(prefix):].split("/")[0].split("?")[0]
            return appid if appid.isascii() and appid.isdigit() and len(appid) <= 10 else ""
    return ""


def read_url_shortcut(path: str) -> str:
    """Адрес из интернет-ярлыка Windows (.url — так Steam, Epic и Ubisoft кладут игры на рабочий стол)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as file:
            for line in file.read(64 * 1024).splitlines():
                if line.strip().lower().startswith("url="):
                    return line.strip()[4:].strip()
    except (OSError, ValueError):
        pass
    return ""


def link_manual(library: Library, key: str, exe_path: str) -> None:
    """Игра без лаунчера: пользователь сам указал её exe."""
    # Настоящий путь (без ярлыков-ссылок на папки): именно его Windows показывает у запущенного процесса
    path = os.path.realpath(os.path.normpath(exe_path))
    library.set_install(key, "manual", "", os.path.dirname(path), path)


def _gog_command(install_id: str) -> tuple[str, list[str], str] | None:
    """Команда запуска GOG-игры из реестра: у игр на DOSBox/ScummVM важны параметры и рабочая папка."""
    if sys.platform != "win32" or not install_id:
        return None
    import winreg

    for root in (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{root}\{install_id}") as key:
                def value(name: str) -> str:
                    try:
                        return str(winreg.QueryValueEx(key, name)[0] or "")
                    except OSError:
                        return ""
                command = value("launchCommand")
                parts = QProcess.splitCommand(command) if command else []
                if not parts and value("exe"):
                    parts = [value("exe")] + QProcess.splitCommand(value("launchParam"))
                if parts:
                    workdir = value("workingDir") or os.path.dirname(parts[0])
                    return parts[0], parts[1:], workdir
        except OSError:
            continue
    return None


def launch_game(game: Game) -> str | None:
    """Запускает игру. Возвращает текст ошибки или None, если всё хорошо."""
    if game.install_source == "steam" and game.install_id:
        QDesktopServices.openUrl(QUrl(f"steam://rungameid/{game.install_id}"))
        return None
    if game.install_source == "epic" and game.install_id:
        QDesktopServices.openUrl(QUrl(f"com.epicgames.launcher://apps/{quote(game.install_id)}"
                                      "?action=launch&silent=true"))
        return None
    if game.install_source == "gog":
        command = _gog_command(game.install_id)
        if command and Path(command[0]).is_file():
            ok, _pid = QProcess.startDetached(*command)
            return None if ok else tr("Windows не дал запустить игру.")
    if game.exe_path and Path(game.exe_path).is_file():
        ok, _pid = QProcess.startDetached(game.exe_path, [], os.path.dirname(game.exe_path))
        return None if ok else tr("Windows не дал запустить файл игры.")
    return tr("Файл игры не найден. Укажите его заново.")


def _steam_appid(game: Game) -> int | None:
    """appid в Steam: у игры из каталога Steam — её id, у игры из RAWG — если она найдена в Steam-установке."""
    if game.source == "steam":
        return game.id
    if game.install_source == "steam" and game.install_id.isascii() and game.install_id.isdigit():
        return int(game.install_id)
    return None


def import_steam_hours(library: Library) -> int:
    """Переносит наигранные часы из файлов Steam на этом компьютере. Возвращает число обновлённых игр.

    Берём большее из двух значений, чтобы не посчитать одно и то же время дважды.
    """
    playtimes = steam_playtimes()
    updated = 0
    with library.batch():
        for game in library.games():
            appid = _steam_appid(game)
            if appid not in playtimes:
                continue
            hours, last_played_unix = playtimes[appid]
            last_played = datetime.fromtimestamp(last_played_unix).isoformat(timespec="seconds") \
                if last_played_unix else ""
            new_hours = max(game.hours, round(hours, 2))
            new_last = max(game.last_played, last_played)
            if new_hours != game.hours or new_last != game.last_played:
                library.set_playtime(game.key, new_hours, new_last)
                updated += 1
    return updated
