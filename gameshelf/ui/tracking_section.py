"""Блок «Учёт времени» в окне игры: где установлена, кнопка «Указать файл игры», история сеансов."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, Signal
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import icons
from ..context import ctx
from ..i18n import tr
from ..models import INSTALL_SOURCE_TITLES, Game
from ..theme import C
from ..timefmt import format_duration, relative_day
from ..tracking.installed import scan_steam
from ..tracking.linking import is_launcher_exe, link_manual
from .widgets import WrapLabel, hbox

RECENT_SESSIONS = 6
LAUNCHER_HINT = tr("Это ярлык лаунчера, а не самой игры. Укажите .exe из папки игры "
                   "или нажмите «Найти автоматически»")


class TrackingSection(QWidget):
    changed = Signal()  # привязка изменилась — окну игры нужно обновить кнопку «Играть»

    def __init__(self, key: str | None, parent=None):
        super().__init__(parent)
        self._key = key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel(tr("Учёт времени"))
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.status = WrapLabel(object_name="Muted")
        layout.addWidget(self.status)

        self.pick_button = self._button(tr("  Указать файл игры…"), "folder", self.pick)
        self.find_button = self._button(tr("  Найти автоматически"), "search", self._find)
        self.unlink_button = QPushButton(tr("  Отвязать"))
        self.unlink_button.setObjectName("Danger")  # красная кнопка с рамкой — видно, что на неё можно нажать
        self.unlink_button.setIcon(icons.icon("unlink", C["danger"], 16))
        self.unlink_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlink_button.setToolTip(tr("Не считать время для этой игры и не искать её автоматически"))
        self.unlink_button.clicked.connect(self.unlink)
        # Красная кнопка — отдельной строкой: так ряд не вылезает за край даже в самом узком окне
        layout.addLayout(hbox(self.pick_button, self.find_button, None, spacing=8))
        layout.addLayout(hbox(self.unlink_button, None))

        self.summary = WrapLabel()
        layout.addWidget(self.summary)
        self.sessions = QVBoxLayout()
        self.sessions.setSpacing(4)
        layout.addLayout(self.sessions)

        ctx.library.playtime_changed.connect(self._on_playtime)
        ctx.tracker.active_changed.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _button(text: str, icon_name: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(icons.icon(icon_name, C["text"], 16))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _game(self) -> Game | None:
        return ctx.library.get(self._key) if self._key else None

    # ------------------------------------------------------------------ отображение

    def refresh(self) -> None:
        game = self._game()
        in_library = game is not None
        for button in (self.pick_button, self.find_button):
            button.setVisible(in_library)
        # Не нашли игру, а играют в неё не на этом компьютере (консоль, другой ПК) — можно спокойно выключить учёт.
        # У игр с файлом, указанным вручную, «Отвязать» стоит в шапке окна, рядом с «Играть», — здесь не дублируем
        self.unlink_button.setVisible(in_library and game.install_source not in ("none", "manual"))
        if in_library:
            self.unlink_button.setText(tr("  Отвязать") if game.is_linked else tr("  Не считать время"))
        self.summary.setVisible(in_library)
        self._clear_sessions()
        if not in_library:
            self.status.setText(tr("Добавьте игру в библиотеку — тогда GameShelf сам будет считать, "
                                   "сколько вы в неё играете."))
            return

        if not ctx.settings.tracking_enabled:
            playing = tr("Учёт времени выключен в настройках.\n")
        elif ctx.tracker.is_active(game.key):
            playing = (tr("▶ Игра запущена — время идёт, пока её окно активно.\n")
                       if ctx.settings.track_foreground_only else tr("▶ Вы сейчас в игре — время идёт.\n"))
        else:
            playing = ""
        if game.is_linked:
            where = game.exe_path if game.install_source == "manual" else game.install_dir
            source = INSTALL_SOURCE_TITLES.get(game.install_source, game.install_source)
            text = tr("Игра на компьютере ({source}): {where}", source=source, where=where)
            if not Path(where).exists():
                text += tr("\nФайл не найден — возможно, игру перенесли или удалили. Укажите его заново.")
            shared = self._shared_with(game)
            if shared:
                text += tr("\nЭтот же файл или папка привязаны к «{game}» — время будет идти только одной из игр.",
                           game=shared)
        elif game.install_source == "none":
            text = tr("Учёт времени для этой игры выключен. Чтобы включить, найдите игру автоматически или "
                      "укажите её файл — можно просто перетащить .exe или ярлык игры в это окно.")
        else:
            text = tr("GameShelf не нашёл эту игру на компьютере. Если она установлена без лаунчера "
                      "(например, скачана с другого сайта), укажите её файл — обычно это .exe в папке игры. "
                      "Можно просто перетащить .exe или ярлык игры в это окно.")
        self.status.setText(playing + text)

        sessions = ctx.library.sessions(game.key)
        if not sessions:
            self.summary.setText(tr("Сеансов пока нет — запустите игру, и время начнёт считаться само."))
            self.summary.setVisible(game.is_linked and ctx.settings.tracking_enabled)
            return
        tracked = sum(s.seconds for s in sessions)
        longest = max(s.seconds for s in sessions)
        lines = []
        if game.last_played:
            lines.append(tr("Последний раз: {when}", when=relative_day(game.last_played)))
        lines.append(tr("Сеансов: {count} · засчитано {tracked} · самый долгий {longest}",
                        count=len(sessions), tracked=format_duration(tracked), longest=format_duration(longest)))
        self.summary.setText("\n".join(lines))
        for session in sessions[:RECENT_SESSIONS]:
            row = QLabel(f"•  {relative_day(session.started_at)} — {format_duration(session.seconds)}")
            row.setObjectName("SessionRow")
            self.sessions.addWidget(row)

    @staticmethod
    def _shared_with(game: Game) -> str:
        """Название другой игры с тем же exe или той же папкой лаунчера (например, HL2 и её эпизоды)."""
        def norm(path: str) -> str:
            return os.path.normcase(os.path.normpath(path)) if path else ""
        for other in ctx.library.games():
            if other.key == game.key or not other.is_linked:
                continue
            same_exe = game.exe_path and norm(other.exe_path) == norm(game.exe_path)
            same_dir = (game.install_source != "manual" and other.install_source != "manual"
                        and norm(other.install_dir) == norm(game.install_dir))
            if same_exe or same_dir:
                return other.name
        return ""

    def _clear_sessions(self) -> None:
        while self.sessions.count():
            item = self.sessions.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_playtime(self, key: str) -> None:
        if key == self._key:
            self.refresh()

    # ------------------------------------------------------------------ действия

    def pick(self) -> None:
        """«Указать файл игры…» — окно выбора файла (кнопка здесь и кнопка в шапке окна игры)."""
        game = self._game()
        if game is None:
            return
        start = os.path.dirname(game.exe_path) if game.exe_path else \
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation)
        path, _ = QFileDialog.getOpenFileName(self, tr("Укажите файл игры"), start,
                                              tr("Программы (*.exe);;Все файлы (*)"))
        if path:
            self.link_file(path)

    def link_file(self, path: str) -> None:
        """Привязать игру к её .exe: выбран в окне или перетащен мышкой."""
        game = self._game()
        if game is None:
            return
        if is_launcher_exe(path):
            ctx.toast(LAUNCHER_HINT)
            return
        link_manual(ctx.library, game.key, path)
        ctx.toast(tr("Файл игры указан — время «{name}» будет считаться само", name=game.name)
                  if ctx.settings.tracking_enabled
                  else tr("Файл игры указан. Включите учёт времени в настройках, чтобы оно считалось"))
        self._after_link_change()

    def link_steam(self, appid: str) -> None:
        """Перетащили ярлык Steam (.url): привязываем к установленной игре с этим appid."""
        game = self._game()
        if game is None:
            return
        installed = next((item for item in scan_steam() if item.source_id == appid), None)
        if installed is None:
            ctx.toast(tr("Эта игра Steam не установлена на компьютере — установите её или укажите .exe игры"))
            return
        ctx.library.set_install(game.key, "steam", appid, installed.install_dir, installed.exe_path)
        ctx.toast(tr("«{name}» привязана к игре Steam — время будет считаться само", name=game.name))
        self._after_link_change()

    def _find(self) -> None:
        game = self._game()
        if game is None:
            return
        match = ctx.linker.find(game, rescan=True)
        if match is None:
            ctx.toast(tr("Не нашли игру в Steam, Epic Games и GOG — укажите её файл вручную"))
            return
        ctx.library.set_install(game.key, match.source, match.source_id, match.install_dir, match.exe_path)
        ctx.toast(tr("Нашли «{name}» в {source}", name=game.name,
                     source=INSTALL_SOURCE_TITLES.get(match.source, match.source)))
        self._after_link_change()

    def unlink(self) -> None:
        """«Отвязать» / «Не считать время» — кнопка здесь или в шапке окна игры."""
        game = self._game()
        if game is None:
            return
        ctx.library.set_install(game.key, "none")
        ctx.toast(tr("Учёт времени для «{name}» выключен", name=game.name))
        self._after_link_change()

    def _after_link_change(self) -> None:
        self.refresh()
        self.changed.emit()
