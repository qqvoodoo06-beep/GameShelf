from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .. import icons
from ..context import ctx
from ..i18n import tr
from ..models import Game, Status
from ..providers import SearchResult
from ..theme import C
from .cards import CARD_MIN_WIDTH, GameCard
from .widgets import (CardGrid, EmptyState, LoadingView, SearchLineEdit, games_count_text, hbox, make_scroll_area,
                      page_header)

PAGE_RESULTS, PAGE_LOADING, PAGE_MESSAGE = range(3)

# fetch(page, on_ok, on_err) — загрузка одной страницы результатов из каталога
Fetch = Callable[[int, Callable[[SearchResult], None], Callable[[str], None]], None]


class SearchPage(QWidget):
    open_game = Signal(object)
    open_settings = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._token = 0
        self._page = 0
        self._fetch: Fetch | None = None
        self._shown: set[str] = set()
        self._loaded_once = False
        self._last_request: Callable[[], None] | None = None
        self._message_action: Callable[[], None] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 24, 0)
        root.setSpacing(20)

        titles, _, self.subtitle = page_header(tr("Поиск игр"))
        root.addLayout(hbox(titles, None, margins=(0, 0, 12, 0)))

        self.search_edit = SearchLineEdit("")
        self.search_edit.setObjectName("BigSearch")
        self.search_edit.setMinimumHeight(48)
        self.search_edit.returnPressed.connect(self.search)
        self.search_button = QPushButton(tr("Найти"))
        self.search_button.setObjectName("Primary")
        self.search_button.setMinimumSize(120, 48)
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.clicked.connect(self.search)
        root.addLayout(hbox(self.search_edit, self.search_button, spacing=10, margins=(0, 0, 12, 0)))

        self.section_icon = QLabel()
        self.section_title = QLabel()
        self.section_title.setObjectName("SectionTitle")
        self.section_hint = QLabel()
        self.section_hint.setObjectName("SectionHint")
        root.addLayout(hbox(self.section_icon, self.section_title, self.section_hint, None, spacing=10))

        self.stack = QStackedWidget()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 2, 8, 32)
        content_layout.setSpacing(24)
        self.grid = CardGrid(CARD_MIN_WIDTH)
        content_layout.addWidget(self.grid)
        self.more_button = QPushButton(tr("Показать ещё"))
        self.more_button.setMinimumWidth(200)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self._load_more)
        content_layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignHCenter)
        content_layout.addStretch()
        self.scroll = make_scroll_area(content)
        self.stack.addWidget(self.scroll)
        self.stack.addWidget(LoadingView(tr("Загружаем игры…")))
        self.message = EmptyState()
        self.message.action.connect(lambda: self._message_action and self._message_action())
        self.stack.addWidget(self.message)
        root.addWidget(self.stack, 1)

        self._apply_catalog_texts()
        ctx.library.changed.connect(self._refresh_cards)

    # ------------------------------------------------------------------ публичное

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self.search()  # с пустым запросом покажет популярное

    def focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def on_catalog_changed(self) -> None:
        """Сменили каталог или ключ в настройках — перезагружаем выдачу."""
        self._apply_catalog_texts()
        self._token += 1  # ответы на старые запросы больше не нужны
        self._loaded_once = False
        self.grid.clear()
        self._shown.clear()
        if self.isVisible():
            self.search()

    def search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            self.load_popular()
            return
        provider = ctx.catalog()
        self._start("search", tr("Результаты по запросу «{query}»", query=query),
                    lambda page, ok, err: provider.search(query, page, ok, err))

    def load_popular(self) -> None:
        provider = ctx.catalog()
        self._start("flame", provider.popular_title, provider.popular)

    # ------------------------------------------------------------------ загрузка

    def _apply_catalog_texts(self) -> None:
        provider = ctx.catalog()
        self.subtitle.setText(provider.subtitle)
        self.search_edit.setPlaceholderText(provider.search_placeholder)

    def _set_section(self, icon_name: str, title: str, hint: str = "") -> None:
        self.section_icon.setPixmap(icons.pixmap(icon_name, C["primary_light"], 20))
        self.section_title.setText(title)
        self.section_hint.setText(hint)

    def _start(self, icon_name: str, title: str, fetch: Fetch) -> None:
        self._loaded_once = True
        self._token += 1
        token = self._token
        provider = ctx.catalog()
        if not provider.is_ready():
            self._set_section("key", tr("Нужен API-ключ {catalog}", catalog=provider.title))
            self._show_message(
                "key", tr("Добавьте API-ключ {catalog}", catalog=provider.title),
                tr("Для каталога {catalog} нужен ключ. Добавьте его в настройках — "
                   "или переключитесь на каталог Steam: он работает без регистрации.", catalog=provider.title),
                tr("Открыть настройки"), self.open_settings.emit,
            )
            return
        self._fetch = fetch
        self._page = 0
        self._set_section(icon_name, title)
        self.stack.setCurrentIndex(PAGE_LOADING)
        self._last_request = lambda: self._start(icon_name, title, fetch)
        fetch(0, lambda result: self._on_results(token, result, append=False),
              lambda message: self._on_error(token, message))

    def _load_more(self) -> None:
        if self._fetch is None:
            return
        token = self._token
        self.more_button.setEnabled(False)
        self.more_button.setText(tr("Загрузка…"))
        self._fetch(self._page + 1, lambda result: self._on_results(token, result, append=True),
                    lambda message: self._on_more_error(token, message))

    def _on_results(self, token: int, result: SearchResult, append: bool) -> None:
        if token != self._token:
            return
        if append:
            self._page += 1
        else:
            self.grid.clear()
            self._shown.clear()
        for game in result.games:
            if game.key in self._shown:
                continue  # при переходе на следующую страницу выдача иногда повторяется
            self._shown.add(game.key)
            card = GameCard(game, "search")
            card.clicked.connect(self.open_game)
            card.add_requested.connect(self._quick_add)
            self.grid.add(card)
        self.grid.finish()

        if result.total:
            self.section_hint.setText(games_count_text(result.total))
        self.more_button.setVisible(result.has_more)
        self.more_button.setEnabled(True)
        self.more_button.setText(tr("Показать ещё"))

        if self.grid.is_empty():
            self._show_message("search", tr("Ничего не найдено"),
                               tr("Проверьте написание или попробуйте другое название — например, на английском."))
        else:
            self.stack.setCurrentIndex(PAGE_RESULTS)
            if not append:
                self.scroll.verticalScrollBar().setValue(0)  # новая выдача — с начала

    def _on_error(self, token: int, message: str) -> None:
        if token != self._token:
            return
        self._show_message("alert", tr("Не удалось загрузить игры"), message, tr("Повторить"), self._retry)

    def _on_more_error(self, token: int, message: str) -> None:
        if token != self._token:
            return
        self.more_button.setEnabled(True)
        self.more_button.setText(tr("Показать ещё"))
        ctx.toast(message)

    def _retry(self) -> None:
        if self._last_request:
            self._last_request()

    def _show_message(self, icon_name: str, title: str, text: str, button: str = "", action=None) -> None:
        self.message.set_content(icon_name, title, text, button)
        self._message_action = action
        self.stack.setCurrentIndex(PAGE_MESSAGE)

    # ------------------------------------------------------------------ библиотека

    def _quick_add(self, game: Game, status: Status) -> None:
        ctx.library.add(game, status)
        ctx.toast(tr("«{name}» добавлена в «{status}»", name=game.name, status=status.label))

    def _refresh_cards(self) -> None:
        for card in self.grid.widgets():
            card.refresh_state()
