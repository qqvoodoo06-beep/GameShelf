from __future__ import annotations

import os
import sys

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtWidgets import QApplication

from . import i18n
from .config import APP_NAME, Settings, data_dir
from .context import ctx
from .database import Database, Library
from .images import ImageLoader
from .net import Http
from .providers import create_providers
from .theme import apply_theme

NO_INTRO = "--no-intro"  # перезапуск после смены языка: заставку уже видели
METADATA_LANGUAGE_KEY = "metadata_language"  # на каком языке в библиотеке сохранены жанры и отзывы Steam


def _set_windows_app_id() -> None:
    """Свой идентификатор для Windows: на панели задач будет иконка GameShelf, а не Python."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GameShelf.App")
    except (AttributeError, OSError):
        pass


def create_app() -> QApplication:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("")
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)  # тёмный заголовок окна в Windows
    except AttributeError:
        pass
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    return app


def start_background_jobs() -> None:
    """Поиск установленных игр и чтение достижений — чуть позже, когда окно уже на экране: на медленном диске
    это заметная пауза, и во время заставки анимация бы подёргивалась."""
    QTimer.singleShot(1500, ctx.linker.link_all)
    QTimer.singleShot(2000, ctx.achievements.start)
    QTimer.singleShot(3000, refresh_catalog_language)


def refresh_catalog_language() -> None:
    """Жанры и подписи отзывов Steam хранятся в библиотеке на том языке, на котором игру добавили. После смены
    языка интерфейса один раз запрашиваем их заново — иначе в английском интерфейсе остались бы русские жанры,
    а статистика считала бы «Экшен» и «Action» разными жанрами (и наоборот)."""
    language = i18n.lang()
    if ctx.settings.get(METADATA_LANGUAGE_KEY, "ru") == language:  # всё добавленное до перевода — на русском
        return
    steam = ctx.providers.get("steam")
    ids = [game.id for game in ctx.library.games() if game.source == "steam"]
    if steam is None or not ids:
        ctx.settings.set(METADATA_LANGUAGE_KEY, language)
        return

    def update(games: list) -> None:
        with ctx.library.batch():
            for fresh in games:
                ctx.library.update_metadata(fresh.key, fresh)

    def done(complete: bool) -> None:
        if complete:  # не всё получили (нет сети) — попробуем при следующем запуске
            ctx.settings.set(METADATA_LANGUAGE_KEY, language)

    steam.refresh(ids, update, done)


def create_window(app: QApplication, background: bool = True):
    """Поднимает сервисы (база, каталоги, учёт времени) и создаёт главное окно.
    background=False — фоновые задачи запустит вызывающий (run() — после заставки)."""
    from .achievements.service import AchievementService
    from .achievements.steam_web import SteamAchievementsWeb
    from .tracking.linking import InstallLinker
    from .tracking.tracker import PlaytimeTracker

    folder = data_dir()
    apply_theme(app, folder / "assets")

    network = QNetworkAccessManager(app)
    http = Http(network, app)
    ctx.settings = Settings(folder / "settings.json")
    ctx.library = Library(Database(folder / "library.db"))
    ctx.images = ImageLoader(network, folder / "covers")
    ctx.providers = create_providers(http, ctx.settings)
    ctx.linker = InstallLinker(ctx.library, app)
    ctx.tracker = PlaytimeTracker(ctx.library, ctx.settings, ctx.linker, app)
    ctx.achievements_web = SteamAchievementsWeb(http, folder / "achievements", app)
    ctx.achievements = AchievementService(ctx.library, ctx.settings, ctx.achievements_web, app)

    from .ui.main_window import MainWindow

    window = MainWindow()
    ctx.tracker.start()
    app.aboutToQuit.connect(ctx.tracker.stop)  # сохранить идущие сеансы при выходе
    # Пока игра запущена — заглядываем в её достижения; после закрытия — перечитываем окончательно
    ctx.tracker.tick.connect(lambda: [ctx.achievements.on_game_running(key) for key, _ in ctx.tracker.active()])
    ctx.tracker.session_ended.connect(lambda key, _seconds: ctx.achievements.reload(key))
    if background:
        start_background_jobs()
    return window


def build():
    """Создаёт приложение и главное окно без запуска цикла событий (используется и в тестах)."""
    app = create_app()
    return app, create_window(app)


def _start_again() -> None:
    """Новая копия программы (после смены языка) — сразу с главным окном, без заставки."""
    args = [arg for arg in sys.argv[1:] if arg not in ("--tray", NO_INTRO)] + [NO_INTRO]
    if not getattr(sys, "frozen", False):
        args.insert(0, os.path.abspath(sys.argv[0]))  # запуск из исходников: python main.py
    QProcess.startDetached(sys.executable, args, os.getcwd())


def run() -> int:
    from . import autostart
    from .single_instance import SingleInstance
    from .ui.intro import SHOW_INTRO_KEY, play_intro

    app = create_app()
    instance = SingleInstance(parent=app)
    if not instance.try_lock():
        return 0  # GameShelf уже запущен — он сам покажет своё окно

    window = create_window(app, background=False)
    instance.activated.connect(window.show_from_tray)
    autostart.sync()  # если программу перенесли в другую папку, обновим команду автозапуска
    if "--tray" in sys.argv:
        window.start_in_tray()  # запуск вместе с Windows — сразу в значок у часов, без заставки
        start_background_jobs()
    elif ctx.settings.get(SHOW_INTRO_KEY, True) and NO_INTRO not in sys.argv:
        # «Disorder Game Studio представляет», потом главное окно и фоновые задачи
        play_intro(window).finished.connect(start_background_jobs)
    else:
        window.show()
        start_background_jobs()
    code = app.exec()
    if ctx.restart_requested:
        # Сеансы и настройки уже сохранены (aboutToQuit) — отпускаем блокировку, иначе новая копия решит,
        # что GameShelf ещё запущен, и сразу выйдет
        instance.release()
        _start_again()
    return code
