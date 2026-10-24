from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget,
                               QSystemTrayIcon, QWidget)

from .. import i18n, icons
from ..achievements.service import RARE_PERCENT
from ..config import data_dir
from ..context import ctx
from ..i18n import tr, tr_for
from ..models import Game, Status
from ..timefmt import format_duration
from .achievements_page import AchievementsPage
from .details_dialog import GameDetailsDialog
from .library_page import LibraryPage
from .overlay import GOLD, VIOLET, AchievementOverlay, OverlayItem
from .search_page import SearchPage
from .settings_dialog import SettingsDialog
from .sidebar import Sidebar
from .stats_page import StatsPage
from .welcome_page import WelcomePage
from .widgets import Toast


def _quoted(name: str) -> str:
    """Название в кавычках: «…» по-русски, “…” по-английски."""
    return tr("«{name}»", name=name)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GameShelf")
        self.setWindowIcon(icons.app_icon())
        self.resize(1320, 840)
        self.setMinimumSize(1080, 680)

        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        self.root_stack = QStackedWidget()
        outer.addWidget(self.root_stack)

        # Основной экран: боковое меню + страницы
        main = QWidget()
        main_layout = QHBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.sidebar = Sidebar()
        self.pages = QStackedWidget()
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.pages, 1)

        self.library_page = LibraryPage()
        self.search_page = SearchPage()
        self.stats_page = StatsPage()
        self.achievements_page = AchievementsPage()
        for page in (self.library_page, self.search_page, self.stats_page, self.achievements_page):
            self.pages.addWidget(page)

        self.welcome_page = WelcomePage()
        self.root_stack.addWidget(self.welcome_page)
        self.root_stack.addWidget(main)

        self.toast = Toast(central)
        ctx.toast = self.toast.show_message

        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.settings_requested.connect(self.open_settings)
        self.library_page.open_game.connect(self.open_game)
        self.library_page.go_search.connect(lambda: self.navigate("search"))
        self.search_page.open_game.connect(self.open_game)
        self.search_page.open_settings.connect(self.open_settings)
        self.stats_page.go_search.connect(lambda: self.navigate("search"))
        self.achievements_page.open_game.connect(self.open_game)
        self.welcome_page.finished.connect(self._finish_welcome)
        self.overlay = ctx.overlay = AchievementOverlay(ctx.settings, ctx.images, data_dir() / "assets", self)
        self._unlock_batch: dict[str, list] = {}
        self._meta_batch: list = []
        self._unlock_timer = QTimer(self, singleShot=True, interval=500, timeout=self._flush_achievements)
        ctx.achievements.unlocked.connect(self._on_achievement)
        ctx.achievements.meta_unlocked.connect(self._on_meta_achievement)
        ctx.library.changed.connect(self._update_counts)

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)

        self._quitting = False
        self._intro = None  # заставка при запуске (её ставит intro.play_intro), пока идёт
        self._setup_tray()
        ctx.tracker.active_changed.connect(self._update_now_playing)
        ctx.tracker.session_ended.connect(self._on_session_ended)
        ctx.tracker.tick.connect(self._update_now_playing)

        self._update_counts()
        if ctx.settings.get("welcome_done"):
            self.root_stack.setCurrentIndex(1)
            self.navigate("all")
        else:
            self.root_stack.setCurrentIndex(0)

    def _finish_welcome(self) -> None:
        ctx.settings.set("welcome_done", True)
        self.root_stack.setCurrentIndex(1)
        # Новичку сразу показываем каталог, чтобы было из чего собрать библиотеку
        self.navigate("search")

    def navigate(self, key: str) -> None:
        self.sidebar.set_current(key)
        if key == "search":
            self.pages.setCurrentWidget(self.search_page)
        elif key == "stats":
            self.pages.setCurrentWidget(self.stats_page)
        elif key == "achievements":
            self.pages.setCurrentWidget(self.achievements_page)
        else:
            self.library_page.set_status_filter(None if key == "all" else Status(key))
            self.pages.setCurrentWidget(self.library_page)

    def _focus_search(self) -> None:
        if self.root_stack.currentIndex() == 1:
            self.navigate("search")
            self.search_page.focus_search()

    def _update_counts(self) -> None:
        self.sidebar.set_counts(ctx.library.counts())
        self.sidebar.set_sources({ctx.settings.catalog} | {g.source for g in ctx.library.games()})

    def open_game(self, game: Game) -> None:
        # Открываем после выхода из обработчика клика: пока окно открыто, карточку могут пересоздать
        QTimer.singleShot(0, lambda: self._exec_game_dialog(game))

    def _exec_game_dialog(self, game: Game) -> None:
        dialog = GameDetailsDialog(game, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.exec()

    # ------------------------------------------------------------------ значок у часов

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip("GameShelf")
        menu = QMenu(self)
        menu.addAction(icons.icon("grid", "#c4b5fd", 16), tr("Открыть GameShelf"), self.show_from_tray)
        menu.addSeparator()
        menu.addAction(icons.icon("x", "#f87171", 16), tr("Выйти"), self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        # Окно можно закрыть, а программа продолжит считать время в фоне
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_from_tray()

    def show_from_tray(self) -> None:
        if self._intro is not None:  # ещё идёт заставка (щёлкнули по значку, запустили повторно) — заканчиваем её,
            self._intro.skip()       # а окно она откроет сама
            return
        # Снимаем только «свёрнуто»: развёрнутое на весь экран окно таким и останется
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def start_in_tray(self) -> None:
        """Запуск вместе с Windows: окно не показываем, работает только значок у часов."""
        if not self.tray.isVisible():
            self.show()

    def _close_modal_windows(self) -> bool:
        """Перед выходом закрываем открытые поверх окна (окно игры, настройки). Окно игры может спросить
        «Сохранить изменения?» — если там нажали «Отмена», выходить не будем. False — пользователь передумал."""
        for _ in range(10):  # окна могут быть вложены (вопрос поверх окна игры)
            modal = QApplication.activeModalWidget()
            if modal is None or not modal.isVisible():
                return True
            if not modal.close():
                return False
        return True

    def quit_app(self) -> None:
        if not self._close_modal_windows():
            return  # программа продолжает работать как ни в чём не бывало: значок у часов на месте
        self._quitting = True
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        if not self._quitting and ctx.settings.close_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not ctx.settings.get("tray_hint_shown"):
                ctx.settings.set("tray_hint_shown", True)
                text = (tr("Время в играх продолжает считаться. Открыть или выйти — через этот значок.")
                        if ctx.settings.tracking_enabled
                        else tr("Учёт времени выключен в настройках. Открыть или выйти — через этот значок."))
                self.tray.showMessage(tr("GameShelf свёрнут в значок у часов"), text, icons.app_icon(), 6000)
            return
        event.accept()
        self.quit_app()

    # ------------------------------------------------------------------ учёт времени

    def _on_session_ended(self, key: str, seconds: int) -> None:
        game = ctx.library.get(key)
        if game is None:
            return
        text = tr("«{name}»: сыграно {duration}", name=game.name, duration=format_duration(seconds))
        # Свёрнутое или перекрытое игрой окно Qt тоже считает «видимым» — тогда нужно уведомление Windows
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            ctx.toast(text)
        elif ctx.settings.notify_sessions and self.tray.isVisible():
            self.tray.showMessage(tr("Сеанс завершён"), text, icons.app_icon(), 5000)

    # ------------------------------------------------------------------ достижения

    def _deliver(self, shown: list[tuple[str, bool]], items: list[OverlayItem],
                 tray_only: list[tuple[str, bool]]) -> None:
        """Внутри окна — тостом, если на него смотрят; поверх игры — оверлеем; иначе — уведомлением у часов.
        shown — строки того, что есть в items (плашках), tray_only — того, у чего плашки нет (свой оверлей Steam)."""
        lines = shown + tray_only
        if not lines:
            return
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            ctx.toast(f"{self._title(lines)}: {self._text(lines)}")
            return
        if items and self.overlay.enabled():
            self.overlay.show_items(items)
            lines = tray_only
        if lines and self.tray.isVisible():
            self.tray.showMessage(self._title(lines), self._text(lines), icons.app_icon(), 6000)

    @staticmethod
    def _text(lines: list[tuple[str, bool]]) -> str:
        if len(lines) == 1:
            return lines[0][0]
        return "\n".join(("GameShelf: " if meta else "") + text for text, meta in lines)

    @staticmethod
    def _title(lines: list[tuple[str, bool]]) -> str:
        if len(lines) == 1:
            text, meta = lines[0]
            if meta:
                return tr("🏆 Достижение GameShelf")
            if text.startswith(_quoted("")[:1]):  # про одно достижение строка начинается с его названия в кавычках
                return tr("🏆 Новое достижение")
        return tr("🏆 Новые достижения")

    @staticmethod
    def _overlay_item(achievement, game_name: str) -> OverlayItem:
        rare = achievement.rarity is not None and achievement.rarity < RARE_PERCENT
        subtitle = game_name
        if achievement.rarity is not None:
            subtitle = tr("{game} · есть у {percent}% игроков", game=game_name,
                          percent=i18n.decimal(achievement.rarity, ".1f"))
        title = tr("Редкое достижение!") if rare else tr("Достижение получено")
        return OverlayItem(title, achievement.name or achievement.api,
                           subtitle, icon_url=achievement.icon, accent=GOLD if rare else VIOLET, game=game_name)

    @staticmethod
    def _line(game_name: str, achievements: list) -> str:
        names = [_quoted(a.name or a.api) for a in achievements]
        if len(achievements) == 1:
            rarity = achievements[0].rarity
            if rarity is None:
                return f"{names[0]} — {game_name}"
            return tr("{achievement} — {game} · есть у {percent}% игроков", achievement=names[0], game=game_name,
                      percent=i18n.decimal(rarity, ".1f"))
        shown = ", ".join(names[:2])
        if len(names) > 2:
            shown = tr("{names} и ещё {more}", names=shown, more=len(names) - 2)
        return tr("{count} в «{game}»: {names}", count=len(names), game=game_name, names=shown)

    def _on_achievement(self, key: str, achievement) -> None:
        if not ctx.settings.get("notify_achievements", True):
            return
        # Несколько достижений приходят пачкой (кряк записал файл один раз) — копим и сообщаем одним сообщением
        self._unlock_batch.setdefault(key, []).append(achievement)
        self._unlock_timer.start()

    def _on_meta_achievement(self, achievement) -> None:
        if ctx.settings.get("notify_achievements", True):
            # Свои достижения тоже копим: «Перенести часы из Steam» может открыть сразу несколько
            self._meta_batch.append(achievement)
            self._unlock_timer.start()

    def _games_in_batch(self, batch: dict[str, list]) -> list[tuple[str, list]]:
        """Одна игра может быть в библиотеке дважды (из Steam и из RAWG) — сообщаем о её достижении один раз."""
        games: dict = {}
        for key, achievements in batch.items():
            achievement_set = ctx.achievements.get(key)
            game = ctx.library.get(key)
            name, seen = games.setdefault(achievement_set.appid if achievement_set else key,
                                          (game.name if game else tr("игра"), {}))
            for achievement in achievements:
                seen.setdefault(achievement.api.lower(), achievement)
        return [(name, list(seen.values())) for name, seen in games.values()]

    def _flush_achievements(self) -> None:
        batch, self._unlock_batch = self._unlock_batch, {}
        meta, self._meta_batch = self._meta_batch, []
        for_steam = ctx.settings.get("overlay_for_steam", False)
        shown, items, tray_only = [], [], []
        for game_name, achievements in self._games_in_batch(batch):
            # Засчитанное самим Steam он покажет в своём оверлее — по умолчанию не дублируем
            on_overlay = [a for a in achievements if for_steam or a.origin != "steam"]
            steam_own = [a for a in achievements if not (for_steam or a.origin != "steam")]
            if on_overlay:
                shown.append((self._line(game_name, on_overlay), False))
                items += [self._overlay_item(a, game_name) for a in on_overlay]
            if steam_own:
                tray_only.append((self._line(game_name, steam_own), False))
        for achievement in meta:
            definition = achievement.definition
            shown.append((tr("«{name}» — {description}", name=definition.name,
                             description=definition.description.lower()), True))
            items.append(OverlayItem(tr("Достижение GameShelf"), definition.name, definition.description,
                                     icon_name=definition.icon, accent=definition.color, game="GameShelf"))
        self._deliver(shown, items, tray_only)

    def _update_now_playing(self) -> None:
        playing = []
        for key, seconds in ctx.tracker.active():
            game = ctx.library.get(key)
            if game:
                playing.append((game.name, seconds))
        self.sidebar.set_now_playing(playing)
        if playing:
            names = ", ".join(f"{name} — {format_duration(seconds)}" for name, seconds in playing)
            self.tray.setToolTip(tr("GameShelf · сейчас: {games}", games=names)[:127])
        else:
            self.tray.setToolTip("GameShelf")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        accepted = dialog.exec()
        changed = dialog.catalog_changed
        new_language = dialog.restart_language
        dialog.deleteLater()  # иначе закрытые окна настроек копятся как дочерние объекты главного окна
        if accepted:
            self.library_page.update_banner()  # автозапуск или учёт времени могли включить/выключить
            self.library_page.refresh_cards()  # значки часов на карточках зависят от «Учёта времени»
        if accepted and changed:
            self.search_page.on_catalog_changed()
            self._update_counts()
            ctx.toast(tr("Настройки сохранены · каталог {catalog}", catalog=ctx.catalog().title))
        if accepted and new_language:
            self._offer_restart(new_language)

    def _offer_restart(self, language: str) -> None:
        """Язык сменился: спрашиваем уже на новом языке — его только что выбрали, значит, его точно поймут."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("GameShelf")
        box.setText(tr_for(language, "Язык сменится после перезапуска GameShelf."))
        box.setInformativeText(tr_for(language, "Перезапустить сейчас?"))
        restart = QPushButton(tr_for(language, "Перезапустить"))
        restart.setObjectName("Primary")  # имя — до addButton, иначе стиль кнопки не применится
        box.addButton(restart, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr_for(language, "Позже"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(restart)
        box.exec()
        if box.clickedButton() is restart:
            self.restart_app()

    def restart_app(self) -> None:
        """Выйти и запустить GameShelf заново — новую копию запустит run(), когда эта всё сохранит."""
        ctx.restart_requested = True
        self.quit_app()
        if not self._quitting:  # выход отменили (окно игры спросило про несохранённые изменения)
            ctx.restart_requested = False
