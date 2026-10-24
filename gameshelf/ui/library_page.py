from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QListView, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .. import autostart, icons
from ..context import ctx
from ..i18n import tr
from ..models import Game, Status
from .cards import CARD_MIN_WIDTH, GameCard, status_menu
from .widgets import CardGrid, EmptyState, SearchLineEdit, WrapLabel, confirm_remove, games_count_text, hbox, \
    make_scroll_area, no_wheel, page_header

SORTS = [
    ("added", tr("Недавно добавленные")),
    ("played", tr("Недавно играли")),
    ("hours", tr("Больше всего часов")),
    ("updated", tr("Недавно изменённые")),
    ("name", tr("По названию")),
    ("rating", tr("По моей оценке")),
    ("released", tr("По дате выхода")),
]


def sort_games(games: list[Game], mode: str) -> list[Game]:
    if mode == "name":
        return sorted(games, key=lambda g: g.name.lower())
    if mode == "rating":
        return sorted(games, key=lambda g: (-g.user_rating, g.name.lower()))
    if mode == "released":
        return sorted(games, key=lambda g: g.released or "0000", reverse=True)
    if mode == "updated":
        return sorted(games, key=lambda g: g.updated_at, reverse=True)
    if mode == "hours":
        return sorted(games, key=lambda g: (-g.hours, g.name.lower()))
    if mode == "played":
        # Сначала то, во что играли недавно; игры без сеансов — в конце, по дате добавления
        return sorted(games, key=lambda g: (g.last_played, g.added_at), reverse=True)
    return sorted(games, key=lambda g: g.added_at, reverse=True)


class LibraryPage(QWidget):
    open_game = Signal(object)
    go_search = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status: Status | None = None
        self._dirty = True
        self._refresh_pending = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 24, 0)
        root.setSpacing(22)

        titles, self.title_label, self.subtitle_label = page_header(tr("Все игры"))
        self.filter_edit = SearchLineEdit(tr("Фильтр по названию…"))
        self.filter_edit.setFixedWidth(260)
        self.filter_edit.textChanged.connect(self.refresh)
        self.sort_combo = no_wheel(QComboBox())
        self.sort_combo.setView(QListView())
        self.sort_combo.setFixedWidth(220)
        for key, label in SORTS:
            self.sort_combo.addItem(label, key)
        saved = ctx.settings.get("library_sort", "added")
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(saved)))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        root.addLayout(hbox(titles, None, self.filter_edit, self.sort_combo, spacing=10, margins=(0, 0, 12, 0)))

        self.banner = self._build_autostart_banner()
        root.addWidget(self.banner)

        self.stack = QStackedWidget()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 2, 8, 32)
        self.grid = CardGrid(CARD_MIN_WIDTH)
        content_layout.addWidget(self.grid)
        content_layout.addStretch()
        self.stack.addWidget(make_scroll_area(content))
        self.empty = EmptyState()
        self.empty.action.connect(self.go_search)
        self.stack.addWidget(self.empty)
        root.addWidget(self.stack, 1)

        ctx.library.changed.connect(self._on_library_changed)
        ctx.library.playtime_changed.connect(self._on_playtime_changed)
        ctx.tracker.active_changed.connect(self.refresh_cards)
        ctx.achievements.changed.connect(self._refresh_card)

    # ------------------------------------------------------------------ автозапуск

    def _build_autostart_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("Banner")
        text = WrapLabel(tr("Вы можете включить автоматический запуск вместе с Windows сейчас или позже в настройках."))
        text.setStyleSheet("background: transparent;")
        text.setToolTip(tr("GameShelf будет тихо запускаться вместе с Windows и ждать в значке у часов, "
                           "чтобы время в играх считалось всегда."))
        enable = QPushButton(tr("Включить"))
        enable.setObjectName("Primary")
        enable.setCursor(Qt.CursorShape.PointingHandCursor)
        enable.clicked.connect(self._enable_autostart)
        later = QPushButton(tr("Сам поставлю"))  # обычная кнопка с рамкой — чтобы не выглядела простой надписью
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        later.setToolTip(tr("Скрыть подсказку. Автозапуск включается в «Настройках» → «Учёт времени»"))
        later.clicked.connect(self._dismiss_banner)
        icon = QLabel()
        icon.setPixmap(icons.pixmap("clock", "#c4b5fd", 22))
        icon.setStyleSheet("background: transparent;")
        layout = hbox(icon, text, enable, later, spacing=14, margins=(16, 12, 16, 12))
        layout.setStretch(1, 1)
        banner.setLayout(layout)
        banner.hide()
        return banner

    def update_banner(self) -> None:
        # Предлагаем, только когда есть что считать: хотя бы одна игра найдена на компьютере
        show = (not ctx.settings.get("autostart_offer_done") and ctx.settings.tracking_enabled
                and any(g.is_linked for g in ctx.library.games()) and not autostart.is_enabled())
        self.banner.setVisible(show)

    def _enable_autostart(self) -> None:
        try:
            autostart.set_enabled(True)
        except OSError as error:
            ctx.toast(tr("Не удалось включить автозапуск: {error}", error=error))
            return
        ctx.settings.set("autostart_offer_done", True)
        self.banner.hide()
        ctx.toast(tr("Готово: GameShelf будет запускаться вместе с Windows"))

    def _dismiss_banner(self) -> None:
        ctx.settings.set("autostart_offer_done", True)
        self.banner.hide()
        ctx.toast(tr("Автозапуск можно включить позже в настройках"))

    # ------------------------------------------------------------------ учёт времени

    def _on_playtime_changed(self, key: str) -> None:
        # Пока идёт игра, часы прибавляются раз в минуту. Перестраиваем сетку, только если от этого поменялся
        # порядок карточек («Недавно играли», «Больше всего часов»), — иначе просто обновляем одну карточку
        game = ctx.library.get(key)
        if game is None:
            return
        cards = self.grid.widgets()
        shown = [card.game.key for card in cards]
        if self.sort_combo.currentData() in ("played", "hours") and key in shown:
            if not self.isVisible() or self.window().isMinimized():
                self._dirty = True  # порядок проверим, когда страницу снова покажут
            else:
                fresh = {g.key: g for g in ctx.library.games()}
                order = [g.key for g in sort_games([fresh[k] for k in shown if k in fresh],
                                                   self.sort_combo.currentData())]
                if order != shown:
                    self._on_library_changed()
                    return
        for card in cards:
            if card.game.key == key:
                card.update_game(game)

    def _refresh_card(self, key: str) -> None:
        """Достижения игры перечитаны — обновляем значок 🏆 на её карточке."""
        for card in self.grid.widgets():
            if card.game.key == key:
                card.refresh_state()

    def refresh_cards(self) -> None:
        """Игра запустилась или закрылась — обновляем плашки «Играете сейчас»."""
        for card in self.grid.widgets():
            card.refresh_state()

    def set_status_filter(self, status: Status | None) -> None:
        self._status = status
        self.filter_edit.clear()
        self.refresh()

    def _on_sort_changed(self) -> None:
        ctx.settings.set("library_sort", self.sort_combo.currentData())
        self.refresh()

    def _on_library_changed(self) -> None:
        if not self.isVisible():
            self._dirty = True
            return
        # Несколько изменений подряд — одна перестройка, когда управление вернётся в цикл событий
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._deferred_refresh)

    def _deferred_refresh(self) -> None:
        self._refresh_pending = False
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        self.update_banner()
        games = ctx.library.games()
        if self._status:
            games = [g for g in games if g.status == self._status]
        section_total = len(games)
        query = self.filter_edit.text().strip().lower()
        if query:
            games = [g for g in games if query in g.name.lower()]
        games = sort_games(games, self.sort_combo.currentData())

        self.title_label.setText(self._status.label if self._status else tr("Все игры"))
        if query:
            self.subtitle_label.setText(tr("Найдено {found} из {total}", found=len(games), total=section_total))
        else:
            self.subtitle_label.setText(games_count_text(section_total) if section_total else tr("Пока пусто"))

        self.grid.clear()
        for game in games:
            card = GameCard(game, "library")
            card.clicked.connect(self.open_game)
            card.context_requested.connect(self._show_context_menu)
            self.grid.add(card)
        self.grid.finish()

        if games:
            self.stack.setCurrentIndex(0)
            return
        if query:
            self.empty.set_content("search", tr("Ничего не найдено"),
                                   tr("В этом разделе нет игр, похожих на «{query}».", query=query))
        elif self._status is None:
            self.empty.set_content(
                "gamepad", tr("Ваша библиотека пуста"),
                tr("Найдите игры в каталоге и добавьте их, чтобы отмечать, во что вы играете, что прошли и что хотите попробовать."),
                tr("Найти игры"),
            )
        else:
            self.empty.set_content(
                self._status.icon, tr("В разделе «{section}» пока ничего нет", section=self._status.label),
                tr("Измените статус игры в её карточке или добавьте новую из каталога."),
                tr("Найти игры"),
            )
        self.stack.setCurrentIndex(1)

    def _show_context_menu(self, game: Game, pos: QPoint) -> None:
        menu = status_menu(self, game.status)
        menu.insertSection(menu.actions()[0], tr("Статус"))
        menu.addSeparator()
        open_action = menu.addAction(icons.icon("eye", "#c4b5fd", 16), tr("Открыть"))
        remove_action = menu.addAction(icons.icon("trash", "#f87171", 16), tr("Удалить из библиотеки"))
        action = menu.exec(pos)
        if action is None:
            return
        if action is open_action:
            self.open_game.emit(game)
        elif action is remove_action:
            if confirm_remove(self, game):
                ctx.library.remove(game.key)
                ctx.toast(tr("«{name}» удалена из библиотеки", name=game.name))
        elif action.data():
            status = Status(action.data())
            if status != game.status:
                ctx.library.set_status(game.key, status)
                ctx.toast(tr("«{name}» → {status}", name=game.name, status=status.label))
