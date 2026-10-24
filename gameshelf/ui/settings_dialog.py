from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QLabel, QLineEdit, QListView,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import autostart, i18n, icons
from ..config import data_dir
from ..context import ctx
from ..i18n import ntr, tr
from ..theme import C
from ..tracking.linking import import_steam_hours
from .details_dialog import ASK_UNSAVED_KEY
from .intro import SHOW_INTRO_KEY
from .overlay import CORNERS
from .widgets import IconBadge, WrapLabel, hbox, make_scroll_area, no_wheel

RAWG_KEY_URL = "https://rawg.io/apidocs"
KEY_HINT = tr("Для каталога RAWG вставьте ключ")
CATALOG_ICONS = {"steam": "monitor", "rawg": "globe"}
TEST_DELAY_SECONDS = 5


def section(icon_name: str, title: str, text: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 20)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("SectionTitle")
    text_label = WrapLabel(text, object_name="Muted")
    texts = QVBoxLayout()
    texts.setSpacing(2)
    texts.addWidget(title_label)
    texts.addWidget(text_label)
    layout.addLayout(hbox(IconBadge(icon_name, C["primary_light"], 40, 20), texts, spacing=14))
    layout.addSpacing(4)
    return frame, layout


class CatalogOption(QPushButton):
    """Крупная кнопка-вариант: название каталога и пояснение."""

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider_name = provider.name
        self.setObjectName("StatusOption")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        title = QLabel(tr("{title}  ·  по умолчанию", title=provider.title) if provider.name == "steam"
                       else tr("{title}  ·  нужен ключ", title=provider.title) if provider.needs_key
                       else provider.title)
        title.setStyleSheet("font-weight: 600; background: transparent;")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text = WrapLabel(provider.description, object_name="Muted")
        text.setStyleSheet("background: transparent;")
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addLayout(hbox(self._icon(provider.name), title, None, spacing=10))
        layout.addWidget(text)

    @staticmethod
    def _icon(name: str) -> QLabel:
        label = QLabel()
        label.setPixmap(icons.pixmap(CATALOG_ICONS.get(name, "gamepad"), C["primary_light"], 18))
        label.setStyleSheet("background: transparent;")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return label

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Настройки"))
        self.setMinimumSize(640, 640)
        self.resize(660, 760)
        self.catalog_changed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 20, 12)
        root.setSpacing(16)

        title = QLabel(tr("Настройки"))
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # --- Язык: заголовок на обоих языках, чтобы раздел нашёл и тот, кто не читает текущий
        frame, layout = section(
            "globe", "Язык · Language" if i18n.lang() == "ru" else "Language · Язык",
            tr("Язык интерфейса. Новый язык включится после перезапуска GameShelf."),
        )
        self.language_combo = no_wheel(QComboBox())
        self.language_combo.setView(QListView())
        system = i18n.LANGUAGES[i18n.system_language()]
        self.language_combo.addItem(tr("Как в системе ({language})", language=system), "auto")
        for code, name in i18n.LANGUAGES.items():
            self.language_combo.addItem(name, code)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(
            ctx.settings.get(i18n.LANGUAGE_KEY, "auto"))))
        layout.addLayout(hbox(self.language_combo, None))
        root.addWidget(frame)
        # Язык, на который нужно перезапуститься после «Сохранить» ("" — язык не меняется)
        self.restart_language = ""

        # --- Каталог
        frame, layout = section(
            "search", tr("Каталог игр"),
            tr("Откуда загружать игры для поиска. Уже добавленные игры остаются в библиотеке при любом выборе."),
        )
        self.catalog_group = QButtonGroup(self)
        for provider in ctx.providers.values():
            option = CatalogOption(provider)
            option.setChecked(provider.name == ctx.settings.catalog)
            self.catalog_group.addButton(option)
            layout.addWidget(option)
        self.catalog_group.buttonClicked.connect(lambda _button: self._update_key_hint())
        root.addWidget(frame)

        # --- Ключ RAWG
        frame, layout = section(
            "key", tr("API-ключ RAWG (необязательно)"),
            tr("Нужен только для каталога RAWG. Получить бесплатно: зарегистрируйтесь на rawg.io и нажмите "
               "«Get API Key»."),
        )
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText(tr("Вставьте сюда ваш API-ключ RAWG"))
        self.key_edit.setMinimumHeight(42)
        self.key_edit.addAction(icons.icon("key", C["text_dim"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.key_edit.setText(ctx.settings.rawg_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.textChanged.connect(self._on_key_edited)
        show = self.key_edit.addAction(icons.icon("eye", C["text_dim"], 18), QLineEdit.ActionPosition.TrailingPosition)
        show.setToolTip(tr("Показать / скрыть ключ"))
        show.triggered.connect(self._toggle_echo)
        layout.addWidget(self.key_edit)

        self.check_button = QPushButton(tr("Проверить ключ"))
        self.check_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_button.clicked.connect(self._check_key)
        get_key = QPushButton(tr("  Получить ключ"))
        get_key.setObjectName("Link")
        get_key.setIcon(icons.icon("external", C["primary_light"], 16))
        get_key.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RAWG_KEY_URL)))
        self.key_status = QLabel()
        layout.addLayout(hbox(self.check_button, self.key_status, None, get_key, spacing=12))
        root.addWidget(frame)

        # --- Учёт времени
        frame, layout = section(
            "clock", tr("Учёт времени"),
            tr("GameShelf сам замечает, когда запущена игра из библиотеки, и считает время. "
               "Игры из Steam, Epic Games и GOG находятся автоматически, для остальных укажите файл в окне игры."),
        )
        self.tracking_box = QCheckBox(tr("Считать время в играх автоматически"))
        self.tracking_box.setChecked(ctx.settings.tracking_enabled)
        self.foreground_box = QCheckBox(tr("Только когда окно игры активно (свёрнутая игра не считается)"))
        self.foreground_box.setChecked(ctx.settings.track_foreground_only)
        self.autostart_box = QCheckBox(tr("Запускать GameShelf вместе с Windows — чтобы время считалось всегда"))
        self.autostart_box.setChecked(autostart.is_enabled())
        if autostart.disabled_in_task_manager():
            self.autostart_box.setToolTip(tr("Автозапуск был отключён в Диспетчере задач. "
                                             "Поставьте галочку и сохраните — GameShelf снова включит его."))
        self.tray_box = QCheckBox(tr("При закрытии окна сворачивать в значок у часов"))
        self.tray_box.setChecked(ctx.settings.close_to_tray)
        self.notify_box = QCheckBox(tr("Показывать уведомление, когда сеанс закончился"))
        self.notify_box.setChecked(ctx.settings.notify_sessions)
        self.achievements_box = QCheckBox(tr("Сообщать о новых достижениях — в играх и в GameShelf"))
        self.achievements_box.setChecked(ctx.settings.get("notify_achievements", True))
        for box in (self.tracking_box, self.foreground_box, self.autostart_box, self.tray_box, self.notify_box,
                    self.achievements_box):
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(box)
        self.tracking_box.toggled.connect(self.foreground_box.setEnabled)
        self.foreground_box.setEnabled(self.tracking_box.isChecked())

        layout.addSpacing(4)
        rescan = QPushButton(tr("  Найти установленные игры"))
        rescan.setIcon(icons.icon("search", C["text"], 16))
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.clicked.connect(self._rescan)
        import_steam = QPushButton(tr("  Перенести часы из Steam"))
        import_steam.setIcon(icons.icon("clock", C["text"], 16))
        import_steam.setCursor(Qt.CursorShape.PointingHandCursor)
        import_steam.setToolTip(tr("Один раз взять наигранные часы из файлов Steam на этом компьютере. "
                                   "Берётся большее значение, поэтому время не задвоится."))
        import_steam.clicked.connect(self._import_steam)
        layout.addLayout(hbox(rescan, import_steam, None, spacing=10))
        self.tracking_status = WrapLabel(object_name="Muted")
        self.tracking_status.hide()
        layout.addWidget(self.tracking_status)
        root.addWidget(frame)

        # --- Оверлей достижений
        frame, layout = section(
            "trophy", tr("Оверлей достижений"),
            tr("Плашка «Достижение получено» поверх игры — как в Steam. Работает в окне и в режиме «полноэкранный "
               "без рамки»; в старом эксклюзивном полном экране появится, когда вы свернёте игру."),
        )
        self.overlay_box = QCheckBox(tr("Показывать полученные достижения поверх игры"))
        self.overlay_box.setChecked(ctx.settings.get("overlay_enabled", True))
        self.overlay_corner = no_wheel(QComboBox())
        self.overlay_corner.setView(QListView())
        for key, label in CORNERS.items():
            self.overlay_corner.addItem(label, key)
        self.overlay_corner.setCurrentIndex(max(0, self.overlay_corner.findData(
            ctx.settings.get("overlay_corner", "bottom_right"))))
        self.overlay_seconds = no_wheel(QComboBox())
        self.overlay_seconds.setView(QListView())
        for seconds in (4, 6, 8, 12):
            self.overlay_seconds.addItem(ntr(seconds, ("{n} секунда", "{n} секунды", "{n} секунд")), seconds)
        self.overlay_seconds.setCurrentIndex(max(0, self.overlay_seconds.findData(
            int(ctx.settings.get("overlay_seconds", 6)))))
        self.overlay_sound = QCheckBox(tr("Со звуком"))
        self.overlay_sound.setChecked(ctx.settings.get("overlay_sound", True))
        self.overlay_steam = QCheckBox(tr("Показывать и для игр Steam (у них есть свой оверлей — будет два)"))
        self.overlay_steam.setChecked(ctx.settings.get("overlay_for_steam", False))
        for box in (self.overlay_box, self.overlay_sound, self.overlay_steam):
            box.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.overlay_box)
        corner_label, seconds_label = QLabel(tr("Где:")), QLabel(tr("Сколько:"))
        corner_label.setObjectName("Muted")
        seconds_label.setObjectName("Muted")
        layout.addLayout(hbox(corner_label, self.overlay_corner, seconds_label, self.overlay_seconds, None, spacing=10))
        layout.addWidget(self.overlay_sound)
        layout.addWidget(self.overlay_steam)
        self.overlay_test = QPushButton(tr("  Проверить оверлей"))
        self.overlay_test.setIcon(icons.icon("play", C["text"], 14))
        self.overlay_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_test.clicked.connect(self._test_overlay)
        self.overlay_status = QLabel()
        self.overlay_status.setObjectName("Muted")
        layout.addLayout(hbox(self.overlay_test, self.overlay_status, None, spacing=12))
        root.addWidget(frame)

        # --- Запуск
        frame, layout = section(
            "play", tr("Запуск"),
            tr("Короткая заставка студии перед открытием программы. Её можно пропустить щелчком или любой клавишей."),
        )
        self.intro_box = QCheckBox(tr("Показывать заставку Disorder Game Studio при запуске"))
        self.intro_box.setChecked(ctx.settings.get(SHOW_INTRO_KEY, True))
        self.intro_box.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.intro_box)
        root.addWidget(frame)

        # --- Данные
        frame, layout = section(
            "folder", tr("Данные"),
            tr("Библиотека хранится только на этом компьютере. Обложки кэшируются, чтобы не скачивать их каждый раз."),
        )
        path_label = WrapLabel(str(data_dir()), object_name="Muted")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)
        open_folder = QPushButton(tr("Открыть папку"))
        open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        self.clear_cache = QPushButton()
        self.clear_cache.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_cache.clicked.connect(self._clear_cache)
        self._update_cache_label()
        layout.addLayout(hbox(open_folder, self.clear_cache, None, spacing=10))
        # Галочку «Больше не спрашивать» из вопроса при закрытии окна игры можно снять здесь
        self.ask_unsaved_box = QCheckBox(tr("Спрашивать о несохранённых изменениях при закрытии окна игры"))
        self.ask_unsaved_box.setChecked(ctx.settings.get(ASK_UNSAVED_KEY, True))
        self.ask_unsaved_box.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addSpacing(4)
        layout.addWidget(self.ask_unsaved_box)
        root.addWidget(frame)
        root.addStretch()

        scroll: QScrollArea = make_scroll_area(content)
        outer.addWidget(scroll, 1)

        cancel = QPushButton(tr("Отмена"))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("Сохранить"))
        save.setObjectName("Primary")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setDefault(True)
        save.clicked.connect(self._save)
        outer.addLayout(hbox(None, cancel, save, spacing=10, margins=(28, 8, 28, 20)))
        self._update_key_hint()

    def _selected_catalog(self) -> str:
        button = self.catalog_group.checkedButton()
        return button.provider_name if button else "steam"

    def _on_key_edited(self) -> None:
        # Результат прошлой проверки относится к старому ключу — убираем его
        self._set_status("", None)
        self.check_button.setEnabled(True)
        self._update_key_hint()

    def _update_key_hint(self) -> None:
        if self._selected_catalog() == "rawg" and not self.key_edit.text().strip():
            self._set_status(KEY_HINT, False)
        elif self.key_status.text() == KEY_HINT:
            self._set_status("", None)

    def _toggle_echo(self) -> None:
        normal = self.key_edit.echoMode() == QLineEdit.EchoMode.Normal
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password if normal else QLineEdit.EchoMode.Normal)

    def _set_status(self, text: str, ok: bool | None) -> None:
        self.key_status.setObjectName("SuccessText" if ok else "ErrorText" if ok is False else "Muted")
        self.key_status.setText(text)
        self.key_status.style().unpolish(self.key_status)
        self.key_status.style().polish(self.key_status)

    def _check_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self._set_status(tr("Введите ключ"), False)
            return
        self.check_button.setEnabled(False)
        self._set_status(tr("Проверяем…"), None)

        def done(text: str, ok: bool):
            if self.key_edit.text().strip() != key:
                return  # пока шла проверка, ключ успели изменить
            self.check_button.setEnabled(True)
            self._set_status(text, ok)

        ctx.providers["rawg"].validate_key(key, lambda: done(tr("✓ Ключ работает"), True),
                                           lambda message: done(message, False))

    def _update_cache_label(self) -> None:
        size_mb = ctx.images.disk_usage() / (1024 * 1024)
        # Точка в размере — как было в русском интерфейсе (i18n.decimal дал бы запятую)
        self.clear_cache.setText(tr("Очистить кэш обложек ({size} МБ)", size=f"{size_mb:.1f}"))

    def _clear_cache(self) -> None:
        ctx.images.clear_disk_cache()
        self._update_cache_label()

    def _show_tracking_status(self, text: str) -> None:
        self.tracking_status.setText(text)
        self.tracking_status.show()

    def _rescan(self) -> None:
        linked = ctx.linker.rescan()
        installed = len(ctx.linker.installed())
        in_library = sum(1 for g in ctx.library.games() if g.is_linked)
        self._show_tracking_status(
            tr("На компьютере найдено игр: {installed}. Из библиотеки привязано: {in_library} (новых: {new}).",
               installed=installed, in_library=in_library, new=linked) if linked
            else tr("На компьютере найдено игр: {installed}. Из библиотеки привязано: {in_library}.",
                    installed=installed, in_library=in_library))

    def _import_steam(self) -> None:
        updated = import_steam_hours(ctx.library)
        self._show_tracking_status(
            tr("Часы перенесены из Steam для игр: {count}.", count=updated) if updated
            else tr("В Steam не нашлось часов для игр из вашей библиотеки (или они уже перенесены)."))

    def _save_overlay_settings(self) -> None:
        ctx.settings.set("overlay_enabled", self.overlay_box.isChecked())
        ctx.settings.set("overlay_corner", self.overlay_corner.currentData())
        ctx.settings.set("overlay_seconds", self.overlay_seconds.currentData())
        ctx.settings.set("overlay_sound", self.overlay_sound.isChecked())
        ctx.settings.set("overlay_for_steam", self.overlay_steam.isChecked())

    def _test_overlay(self) -> None:
        # Пример — через 5 секунд, чтобы успеть переключиться в игру и увидеть плашку поверх неё. Отсчитывает сам
        # оверлей (окно настроек можно закрыть), а выбранное в форме не сохраняем — «Отмена» должна отменять всё
        ctx.overlay.test(delay_ms=TEST_DELAY_SECONDS * 1000,
                         overlay_corner=self.overlay_corner.currentData(),
                         overlay_seconds=self.overlay_seconds.currentData(),
                         overlay_sound=self.overlay_sound.isChecked())
        self.overlay_test.setEnabled(False)
        self._countdown = TEST_DELAY_SECONDS
        self._tick_overlay_countdown()

    def _tick_overlay_countdown(self) -> None:
        if self._countdown == 0:
            self.overlay_status.setText(tr("Плашка отправлена в оверлей"))
            self.overlay_test.setEnabled(True)
            return
        self.overlay_status.setText(tr("Через {seconds} с — переключитесь в игру", seconds=self._countdown))
        self._countdown -= 1
        QTimer.singleShot(1000, self._tick_overlay_countdown)

    def _save(self) -> None:
        self._save_overlay_settings()
        key = self.key_edit.text().strip()
        catalog = self._selected_catalog()
        self.catalog_changed = key != ctx.settings.rawg_key or catalog != ctx.settings.catalog
        ctx.settings.rawg_key = key
        ctx.settings.catalog = catalog
        ctx.settings.set("tracking_enabled", self.tracking_box.isChecked())
        ctx.settings.set("track_foreground_only", self.foreground_box.isChecked())
        ctx.settings.set("close_to_tray", self.tray_box.isChecked())
        ctx.settings.set("notify_sessions", self.notify_box.isChecked())
        ctx.settings.set("notify_achievements", self.achievements_box.isChecked())
        ctx.settings.set(ASK_UNSAVED_KEY, self.ask_unsaved_box.isChecked())
        ctx.settings.set(SHOW_INTRO_KEY, self.intro_box.isChecked())
        language = self.language_combo.currentData()
        ctx.settings.set(i18n.LANGUAGE_KEY, language)
        new_language = i18n.resolve(language)
        self.restart_language = new_language if new_language != i18n.lang() else ""
        if self.autostart_box.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart_box.isChecked())
            except OSError as error:
                QMessageBox.warning(self, tr("Автозапуск"),
                                    tr("Не удалось изменить автозапуск Windows: {error}", error=error))
        self.accept()
