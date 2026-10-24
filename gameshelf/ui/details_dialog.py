from __future__ import annotations

import os
from dataclasses import replace

from PySide6.QtCore import QFileInfo, QRegularExpression, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QRegularExpressionValidator
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import icons
from ..context import ctx
from ..i18n import tr
from ..models import Game, Status
from ..providers import GameDetails
from ..theme import C
from ..tracking.linking import is_launcher_exe, launch_game, read_url_shortcut, steam_appid_from_url
from .achievements_section import AchievementsSection
from .tracking_section import LAUNCHER_HINT, TrackingSection
from .widgets import (CollapsibleLabel, CoverImage, HeroImage, Spinner, StarRating, WrapLabel, confirm_remove, hbox,
                      make_scroll_area)

ASK_UNSAVED_KEY = "ask_unsaved_changes"  # спрашивать при закрытии окна с несохранёнными изменениями
HERO_HEIGHT = 300
MARGIN = 28
POSTER_WIDTH = 128
DESCRIPTION_LINES = 3   # описание свёрнуто до трёх строк — развернуть кнопкой «Показать полностью»

def format_date(value: str) -> str:
    try:
        year, month, day = (int(part) for part in value.split("-"))
        # Месяц в родительном падеже — целым шаблоном с числом и годом: по-английски другой порядок
        months = (tr("{day} января {year}", day=day, year=year), tr("{day} февраля {year}", day=day, year=year),
                  tr("{day} марта {year}", day=day, year=year), tr("{day} апреля {year}", day=day, year=year),
                  tr("{day} мая {year}", day=day, year=year), tr("{day} июня {year}", day=day, year=year),
                  tr("{day} июля {year}", day=day, year=year), tr("{day} августа {year}", day=day, year=year),
                  tr("{day} сентября {year}", day=day, year=year), tr("{day} октября {year}", day=day, year=year),
                  tr("{day} ноября {year}", day=day, year=year), tr("{day} декабря {year}", day=day, year=year))
        return months[month - 1]
    except (ValueError, IndexError):
        return value or "—"


def score_row(game: Game) -> tuple[str, str]:
    # Steam даёт долю положительных отзывов, RAWG — среднюю оценку из 5 (хранится как score / 20)
    if game.source == "rawg":
        return tr("Оценка игроков"), tr("★ {score} из 5 (RAWG)", score=f"{game.score / 20:.1f}")
    if game.score_label:  # надпись из Steam — данные, её не переводим
        return tr("Отзывы игроков"), tr("{percent}% положительных · {label}", percent=game.score,
                                        label=game.score_label)
    return tr("Отзывы игроков"), tr("{percent}% положительных", percent=game.score)


def hours_text(hours: float) -> str:
    return f"{round(hours, 1):g}" if hours else ""


def hours_value(text: str) -> float | None:
    """Часы из поля: «12,5» и «12.5» — одно и то же, пустое — 0. None — в поле не число (например, одна точка)."""
    try:
        return round(float(text.replace(",", ".") or 0), 1)
    except ValueError:
        return None


DROP_PRIORITY = ("exe", "steam", "launcher", "shortcut", "other")
DROP_TEXTS = {
    "steam": tr("Отпустите, чтобы привязать игру из Steam"),
    "launcher": tr("Это ярлык лаунчера, а не самой игры\nНужен .exe из папки игры"),
    "shortcut": tr("Этот ярлык не ведёт к файлу игры\nНажмите «Найти автоматически» или укажите .exe игры"),
    "other": tr("Нужен .exe игры или ярлык на него"),
}
DROP_TOASTS = {
    "launcher": LAUNCHER_HINT,
    "shortcut": tr("Этот ярлык не ведёт к файлу игры — нажмите «Найти автоматически» или укажите .exe игры"),
    "other": tr("Перетащите .exe игры или ярлык на него"),
}


def classify_drop(mime) -> tuple[str, str]:
    """Что перетаскивают в окно игры: ("exe", путь) — .exe или ярлык .lnk на него; ("steam", appid) — ярлык Steam
    (.url); ("launcher", путь) — ярлык лаунчера (GOG Galaxy и т. п.); ("shortcut", "") — другой интернет-ярлык;
    ("other", "") — любой другой файл; ("", "") — не файлы."""
    best = ("", "")
    for url in mime.urls() if mime.hasUrls() else []:
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        info = QFileInfo(path)
        if info.isShortcut() or info.isSymLink():
            info = QFileInfo(info.symLinkTarget())
        target = info.absoluteFilePath()
        if target.lower().endswith(".exe") and os.path.isfile(target):
            found = ("launcher" if is_launcher_exe(target) else "exe", target)
        elif path.lower().endswith(".url"):
            appid = steam_appid_from_url(read_url_shortcut(path))
            found = ("steam", appid) if appid else ("shortcut", "")
        else:
            found = ("other", "")
        if not best[0] or DROP_PRIORITY.index(found[0]) < DROP_PRIORITY.index(best[0]):
            best = found
    return best


def dropped_exe(mime) -> str:
    """Путь к .exe игры из перетаскиваемых файлов (сам .exe или ярлык на него). "" — не нашли."""
    kind, value = classify_drop(mime)
    return value if kind == "exe" else ""


def accept_as_copy(event) -> bool:
    """Принимаем перетаскивание только как «копирование» или «ссылку». С Shift Windows предлагает «перемещение» —
    если его принять, Проводник удалит исходный файл (exe игры или ярлык), ведь «копия» якобы уже у нас."""
    for action in (Qt.DropAction.CopyAction, Qt.DropAction.LinkAction):
        if event.possibleActions() & action:
            event.setDropAction(action)
            event.accept()
            return True
    event.ignore()
    return False


def caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("Caption")
    return label


class GameDetailsDialog(QDialog):
    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        stored = ctx.library.get(game.key)
        self.in_library = stored is not None
        self.game = stored or replace(game)
        self._details: GameDetails | None = None

        self.setWindowTitle(self.game.name)
        self.resize(1080, 780)
        self.setMinimumSize(1060, 700)  # уже — не помещаются «Играть», «Указать файл игры…» и «Отвязать» в шапке

        # Картинка-шапка лежит фоном, а колонки поверх неё
        self.hero = HeroImage(self)
        self.hero.set_url(self.game.hero_url or self.game.cover_url)
        self.hero.lower()

        root = QHBoxLayout(self)
        root.setContentsMargins(36, MARGIN, 28, MARGIN)
        root.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(self._build_title_block())
        left.addWidget(self._build_info(), 1)
        root.addLayout(left, 1)
        root.addWidget(self._build_panel())

        self._fill_info()
        self._load_details()
        ctx.library.playtime_changed.connect(self._on_playtime)
        # Пока окно открыто, поиск игр в фоне может найти игру (её только что установили) или потерять её
        ctx.library.changed.connect(self._on_library_changed)

        # Файл игры можно просто перетащить в окно — exe или ярлык с рабочего стола
        self.drop_zone = QLabel(self)
        self.drop_zone.setObjectName("DropZone")
        self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setWordWrap(True)
        self.drop_zone.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_zone.hide()
        self.setAcceptDrops(self.in_library)
        for field in (self.hours, self.notes):
            field.setAcceptDrops(False)  # иначе файл, брошенный на поле, вставится в него текстом пути

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.hero.setGeometry(0, 0, self.width(), HERO_HEIGHT)
        self.drop_zone.setGeometry(self.rect().adjusted(18, 18, -18, -18))

    def dragEnterEvent(self, event):
        kind, value = classify_drop(event.mimeData()) if ctx.library.get(self.game.key) else ("", "")
        if not kind or not accept_as_copy(event):
            event.ignore()
            return
        # Неподходящий файл тоже принимаем: пусть подсказка объяснит, что нужно, вместо молчаливого «нельзя»
        text = (tr("Отпустите, чтобы указать файл игры\n{file}", file=os.path.basename(value)) if kind == "exe"
                else DROP_TEXTS[kind])
        self.drop_zone.setText(text)
        self.drop_zone.setProperty("warn", kind not in ("exe", "steam"))
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)
        self.drop_zone.show()
        self.drop_zone.raise_()

    def dragMoveEvent(self, event):
        accept_as_copy(event)  # Shift можно нажать и посреди перетаскивания

    def dragLeaveEvent(self, event):
        self.drop_zone.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drop_zone.hide()
        kind, value = classify_drop(event.mimeData())
        if not kind or ctx.library.get(self.game.key) is None or not accept_as_copy(event):
            event.ignore()
            return
        if kind == "exe":
            self.tracking.link_file(value)
        elif kind == "steam":
            self.tracking.link_steam(value)
        else:
            ctx.toast(DROP_TOASTS[kind])

    # ------------------------------------------------------------------ шапка

    def _build_title_block(self) -> QWidget:
        block = QWidget()
        block.setFixedHeight(HERO_HEIGHT - MARGIN - 6)
        layout = QHBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)

        self.poster = CoverImage(block, radius=12, request_width=320)
        self.poster.setFixedSize(POSTER_WIDTH, round(POSTER_WIDTH * 1.5))
        self.poster.set_url(self.game.cover_url)
        layout.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignBottom)

        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addStretch()
        self.hero_title = WrapLabel(self.game.name, object_name="HeroTitle")
        self.hero_meta = QLabel()
        self.hero_meta.setObjectName("HeroMeta")
        texts.addWidget(self.hero_title)
        texts.addWidget(self.hero_meta)
        self.play_button = QPushButton(tr("  Играть"))
        self.play_button.setObjectName("Primary")
        self.play_button.setIcon(icons.icon("play", "#ffffff", 16))
        self.play_button.setMinimumSize(150, 40)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.clicked.connect(self._play)
        # Игра не найдена на компьютере — на месте «Играть» сразу то, что нужно сделать
        self.pick_button = QPushButton(tr("  Указать файл игры…"))
        self.pick_button.setIcon(icons.icon("folder", C["text"], 16))
        self.pick_button.setMinimumSize(150, 40)
        self.pick_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_button.setToolTip(tr("Выберите .exe игры — или просто перетащите его (или ярлык игры) в это окно"))
        self.pick_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)  # надпись не обрезается
        self.pick_button.clicked.connect(lambda: self.tracking.pick())
        # У игр с файлом, указанным вручную (пиратские копии), «Отвязать» тоже здесь, рядом с «Играть»
        self.unlink_button = QPushButton(tr("  Отвязать"))
        self.unlink_button.setObjectName("Danger")
        self.unlink_button.setIcon(icons.icon("unlink", C["danger"], 16))
        self.unlink_button.setMinimumHeight(40)
        self.unlink_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlink_button.setToolTip(tr("Не считать время для этой игры и забыть указанный файл"))
        self.unlink_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.unlink_button.clicked.connect(lambda: self.tracking.unlink())
        self.untracked_icon = QLabel()
        self.untracked_icon.setStyleSheet("background: transparent;")
        self.untracked_hint = QLabel()
        texts.addSpacing(6)
        texts.addLayout(hbox(self.play_button, self.pick_button, self.unlink_button, None, spacing=10))
        texts.addLayout(hbox(self.untracked_icon, self.untracked_hint, None, spacing=8))  # предупреждение — под кнопками
        layout.addLayout(texts, 1)
        self._update_play_button()
        return block

    def _update_play_button(self) -> None:
        game = ctx.library.get(self.game.key)
        manual = game is not None and game.install_source == "manual"  # пиратская копия и т. п.: exe указан вручную
        missing = manual and not os.path.isfile(game.exe_path)       # файл перенесли или удалили
        can_play = game is not None and game.is_linked and not missing
        untracked = game is not None and not can_play
        self.play_button.setVisible(can_play)
        # У игр с файлом, указанным вручную, кнопка смены файла — рядом с «Играть», а не внизу окна.
        # Игры из Steam, Epic и GOG находятся сами — им она наверху не нужна
        self.pick_button.setVisible(untracked or manual)
        self.unlink_button.setVisible(manual)
        self.pick_button.setToolTip(
            tr("Сейчас: {path}\nВыберите другой .exe — или перетащите его (или ярлык игры) в это окно",
               path=game.exe_path)
            if manual and not missing else
            tr("Выберите .exe игры — или просто перетащите его (или ярлык игры) в это окно"))
        self.untracked_icon.setVisible(untracked)
        self.untracked_hint.setVisible(untracked)
        if not untracked:
            return
        if missing:
            text, color = tr("Файл игры не найден"), "#fbbf24"
            tip = tr("{path}\nФайл перенесли или удалили — укажите его заново, и время снова начнёт считаться",
                     path=game.exe_path)
        elif game.install_source == "none":  # пользователь сам отвязал игру — это не ошибка, просто напоминание
            text, color = tr("Учёт времени выключен"), C["text_muted"]
            tip = tr("Вы отвязали эту игру от файла на компьютере")
        else:
            text, color = tr("Время не считается"), "#fbbf24"
            tip = tr("GameShelf не нашёл эту игру на компьютере. Если она установлена без лаунчера (например, "
                     "скачана с другого сайта), укажите её .exe — или перетащите его или ярлык игры в это окно")
        self.untracked_icon.setPixmap(icons.pixmap("alert", color, 16))
        self.untracked_hint.setText(text)
        self.untracked_hint.setToolTip(tip)
        self.untracked_hint.setStyleSheet(f"color: {color}; background: transparent; font-weight: 600;")

    def _on_library_changed(self) -> None:
        self._update_play_button()
        self.tracking.refresh()

    def _play(self) -> None:
        game = ctx.library.get(self.game.key)
        if game is None:
            return
        error = launch_game(game)
        if error:
            ctx.toast(error)
        else:
            ctx.toast(tr("Запускаем «{name}» — время начнёт считаться само", name=game.name)
                      if ctx.settings.tracking_enabled else tr("Запускаем «{name}»", name=game.name))

    # ------------------------------------------------------------------ информация

    def _build_info(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 18, 8, 8)
        layout.setSpacing(12)

        about = QLabel(tr("Об игре"))
        about.setObjectName("SectionTitle")
        layout.addWidget(about)

        self.description_spinner = Spinner(28)
        layout.addWidget(self.description_spinner)
        self.description = CollapsibleLabel(DESCRIPTION_LINES, object_name="Description")
        self.description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.description)
        self.description_toggle = QPushButton()
        self.description_toggle.setObjectName("Link")
        self.description_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.description_toggle.clicked.connect(self._toggle_description)
        self.description_toggle.hide()
        self.description.overflow_changed.connect(self._update_description_toggle)
        layout.addWidget(self.description_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(10)
        details = QLabel(tr("Подробности"))
        details.setObjectName("SectionTitle")
        layout.addWidget(details)
        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(24)
        self.info_grid.setVerticalSpacing(10)
        self.info_grid.setColumnStretch(1, 1)
        layout.addLayout(self.info_grid)

        layout.addSpacing(10)
        self.achievements = AchievementsSection(self.game, self.in_library)
        layout.addWidget(self.achievements)

        layout.addSpacing(10)
        self.tracking = TrackingSection(self.game.key if self.in_library else None)
        self.tracking.changed.connect(self._update_play_button)
        layout.addWidget(self.tracking)

        layout.addSpacing(6)
        link = QPushButton(tr("  Открыть страницу в {source}", source=self.game.source_title))
        link.setObjectName("Link")
        link.setIcon(icons.icon("external", C["primary_light"], 16))
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.game.store_url)))
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self.info_scroll = make_scroll_area(content)
        return self.info_scroll

    def _toggle_description(self) -> None:
        collapse = not self.description.collapsed()
        self.description.set_collapsed(collapse)
        self._update_description_toggle()
        if collapse:  # текст стал короче — возвращаем «Об игре» в поле зрения, а не оставляем пользователя ниже
            self.info_scroll.widget().layout().activate()
            bar = self.info_scroll.verticalScrollBar()
            bar.setValue(min(bar.value(), max(0, self.description.y() - 40)))

    def _update_description_toggle(self) -> None:
        collapsed = self.description.collapsed()
        self.description_toggle.setText(tr("  Показать полностью") if collapsed else tr("  Свернуть"))
        self.description_toggle.setIcon(icons.icon("chevron-down" if collapsed else "chevron-up",
                                                   C["primary_light"], 16))
        self.description_toggle.setVisible(not self.description.isHidden() and self.description.overflows())

    def _fill_info(self) -> None:
        g, d = self.game, self._details
        meta = [part for part in (g.year, " · ".join(g.genres[:3])) if part]
        self.hero_meta.setText("   •   ".join(meta))

        rows = [
            (tr("Дата выхода"), format_date(g.released) if g.released else "—"),
            (tr("Платформы"), ", ".join(g.platforms) or "—"),
        ]
        if d and d.official_genres:
            rows.append((tr("Жанры"), ", ".join(d.official_genres)))
        if g.genres and g.source == "steam":
            rows.append((tr("Метки"), ", ".join(g.genres)))
        elif g.genres and not (d and d.official_genres):
            rows.append((tr("Жанры"), ", ".join(g.genres)))
        if d and d.developers:
            rows.append((tr("Разработчик"), ", ".join(d.developers)))
        if d and d.publishers:
            rows.append((tr("Издатель"), ", ".join(d.publishers)))
        if g.score is not None:
            rows.append(score_row(g))
        if g.metacritic:
            rows.append(("Metacritic", str(g.metacritic)))
        if d and d.playtime:
            rows.append((tr("Среднее время"), tr("около {hours} ч", hours=d.playtime)))

        while self.info_grid.count():
            item = self.info_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row, (key, value) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setObjectName("InfoKey")
            value_label = WrapLabel(value)
            self.info_grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            self.info_grid.addWidget(value_label, row, 1)

    def _load_details(self) -> None:
        provider = ctx.provider_for(self.game)
        if provider is None or not provider.is_ready():
            self.description_spinner.hide()
            title = provider.title if provider else self.game.source_title
            self.description.setText(tr("Добавьте API-ключ {title} в настройках, чтобы увидеть описание игры.",
                                        title=title))
            return
        self.description.hide()
        provider.details(self.game, self._on_details, self._on_details_error)

    def _on_details(self, details: GameDetails) -> None:
        self._details = details
        self.game.merge_metadata(details.game)
        if self.in_library:
            ctx.library.update_metadata(self.game.key, details.game)
        self.description_spinner.hide()
        self.description.setText(details.description or tr("Описание пока отсутствует."))
        self.description.show()
        self._update_description_toggle()
        self.hero.set_url(self.game.hero_url or self.game.cover_url)
        self.poster.set_url(self.game.cover_url)
        self._fill_info()

    def _on_details_error(self, message: str) -> None:
        self.description_spinner.hide()
        self.description.setText(message)
        self.description.show()
        self._update_description_toggle()

    # ------------------------------------------------------------------ моя запись

    def _build_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedWidth(330)
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 140))
        panel.setGraphicsEffect(shadow)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        title = QLabel(tr("Моя запись") if self.in_library else tr("Добавить в библиотеку"))
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        layout.addSpacing(6)

        layout.addWidget(caption(tr("Статус")))
        self.status_group = QButtonGroup(self)
        current = self.game.status or Status.PLANNED
        status_grid = QGridLayout()
        status_grid.setSpacing(8)
        for index, status in enumerate(Status):
            button = QPushButton("  " + status.label)
            button.setObjectName("StatusOption")
            button.setIcon(icons.icon(status.icon, status.color, 16))
            button.setMinimumHeight(38)
            button.setCheckable(True)
            button.setChecked(status == current)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("status", status.value)
            self.status_group.addButton(button)
            status_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(status_grid)

        layout.addSpacing(8)
        layout.addWidget(caption(tr("Моя оценка")))
        self.rating = StarRating(self.game.user_rating, size=24)
        layout.addWidget(self.rating)

        layout.addSpacing(8)
        layout.addWidget(caption(tr("Часов в игре")))
        self.hours = QLineEdit(hours_text(self.game.hours))
        self._hours_shown = self.hours.text()  # если пользователь не правил поле, часы обновляются сами
        self.hours.setPlaceholderText(tr("Например, 12.5"))
        self.hours.setToolTip(tr("Считается автоматически, пока игра запущена. Можно поправить вручную."))
        self.hours.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,5}([.,]\d?)?"), self.hours))
        self.hours.addAction(icons.icon("clock", C["text_dim"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.hours.setMinimumHeight(40)
        layout.addWidget(self.hours)

        layout.addSpacing(8)
        layout.addWidget(caption(tr("Заметки")))
        self.notes = QPlainTextEdit(self.game.notes)
        self.notes.setPlaceholderText(tr("Впечатления, на чём остановились, ачивки…"))
        self.notes.setMinimumHeight(80)
        layout.addWidget(self.notes, 1)

        layout.addSpacing(6)
        save = QPushButton(tr("Сохранить") if self.in_library else tr("Добавить в библиотеку"))
        save.setObjectName("Primary")
        save.setMinimumHeight(42)
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setDefault(True)
        save.clicked.connect(self._save)
        layout.addWidget(save)

        if self.in_library:
            remove = QPushButton(tr("  Удалить из библиотеки"))
            remove.setObjectName("Danger")
            remove.setIcon(icons.icon("trash", C["danger"], 16))
            remove.setMinimumHeight(36)
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(self._remove)
            layout.addWidget(remove)
        # С чем сравнивать при закрытии: изменения записываются только кнопкой «Сохранить»
        self._initial_status = current
        self._initial_rating = self.game.user_rating
        self._initial_notes = (self.game.notes or "").strip()
        return panel

    # ------------------------------------------------------------------ закрытие без «Сохранить»

    def _hours_edited(self) -> bool:
        """Пользователь поменял часы — сравниваем числа, а не текст: «12,5» вместо «12.5» — не изменение."""
        return hours_value(self.hours.text()) != hours_value(self._hours_shown)

    def _changed_fields(self) -> list[str]:
        changed = []
        if Status(self.status_group.checkedButton().property("status")) != self._initial_status:
            changed.append(tr("статус"))
        if self.rating.value() != self._initial_rating:
            changed.append(tr("оценку"))
        if self._hours_edited():
            changed.append(tr("часы"))
        if self.notes.toPlainText().strip() != self._initial_notes:
            changed.append(tr("заметки"))
        return changed

    def reject(self) -> None:
        """Крестик и Esc. Сохраняет только кнопка «Сохранить» — это защита от случайных правок. Но если что-то
        изменили и закрывают окно, спрашиваем (пока пользователь не попросил больше не спрашивать)."""
        changed = self._changed_fields()
        if changed and ctx.settings.get(ASK_UNSAVED_KEY, True):
            answer = self._ask_unsaved(changed)
            if answer == "cancel":
                return
            if answer == "save":
                self._save()  # сохраняет и закрывает окно
                return
        super().reject()

    def _ask_unsaved(self, changed: list[str]) -> str:
        """«save», «discard» или «cancel»."""
        what = (changed[0] if len(changed) == 1 else
                tr("{items} и {last}", items=", ".join(changed[:-1]), last=changed[-1]))
        box = QMessageBox(self)
        box.setWindowTitle(tr("Несохранённые изменения"))
        if self.in_library:
            box.setText(tr("Сохранить изменения в «{name}»?", name=self.game.name))
            box.setInformativeText(tr("Вы изменили {what}. Изменения записываются только кнопкой «Сохранить».",
                                      what=what))
            labels = (tr("Сохранить"), tr("Не сохранять"))
        else:
            box.setText(tr("Добавить «{name}» в библиотеку?", name=self.game.name))
            box.setInformativeText(tr("Вы изменили {what}. Игра попадёт в библиотеку только кнопкой "
                                      "«Добавить в библиотеку».", what=what))
            labels = (tr("Добавить"), tr("Не добавлять"))
        save = QPushButton(labels[0])
        save.setObjectName("Primary")  # до addButton: иначе стиль кнопки уже не подхватит имя
        discard = QPushButton(labels[1])
        box.addButton(save, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(discard, QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(tr("Отмена"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        dont_ask = QCheckBox(tr("Больше не спрашивать"))
        dont_ask.setCursor(Qt.CursorShape.PointingHandCursor)
        box.setCheckBox(dont_ask)
        box.exec()
        clicked = box.clickedButton()
        answer = "save" if clicked is save else "discard" if clicked is discard else "cancel"
        if dont_ask.isChecked() and answer != "cancel":
            ctx.settings.set(ASK_UNSAVED_KEY, False)
            ctx.toast(tr("Больше не спрашиваю — сохраняйте кнопкой «Сохранить». "
                         "Вернуть вопрос: «Настройки» → «Данные»"))
        return answer

    def _on_playtime(self, key: str) -> None:
        """Пока окно открыто, учёт времени мог добавить часов — показываем свежее значение."""
        if key != self.game.key or self._hours_edited():
            return
        latest = ctx.library.get(key)
        if latest:
            self.hours.setText(hours_text(latest.hours))
            self._hours_shown = self.hours.text()

    def _save(self) -> None:
        # Берём свежую версию из библиотеки: пока окно было открыто, могли измениться часы и привязка к диску
        latest = ctx.library.get(self.game.key)
        g = latest or self.game
        if latest:
            g.merge_metadata(self.game)
        g.status = Status(self.status_group.checkedButton().property("status"))
        g.user_rating = self.rating.value()
        if latest is None or self._hours_edited():
            value = hours_value(self.hours.text())
            if value is not None:  # в поле не число (одна точка) — накопленное время не обнуляем
                g.hours = value
        g.notes = self.notes.toPlainText().strip()
        ctx.library.save(g)
        if self.in_library:
            ctx.toast(tr("Изменения в «{name}» сохранены", name=g.name))
        else:
            ctx.toast(tr("«{name}» добавлена в «{status}»", name=g.name, status=g.status.label))
        self.accept()

    def _remove(self) -> None:
        if confirm_remove(self, self.game):
            ctx.library.remove(self.game.key)
            ctx.toast(tr("«{name}» удалена из библиотеки", name=self.game.name))
            self.accept()
