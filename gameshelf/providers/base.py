"""Общий интерфейс каталогов игр (Steam, RAWG)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from ..config import Settings
from ..i18n import tr
from ..models import Game
from ..net import BAD_RESPONSE, Http, call_safely

PAGE_SIZE = 24


@dataclass
class SearchResult:
    games: list[Game]
    total: int | None = None
    has_more: bool = False


@dataclass
class GameDetails:
    game: Game                      # свежие данные об игре (без пользовательских полей)
    description: str = ""
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    official_genres: list[str] = field(default_factory=list)
    playtime: int | None = None     # среднее время прохождения, часов


OnResults = Callable[[SearchResult], None]
OnDetails = Callable[[GameDetails], None]
OnError = Callable[[str], None]


class CatalogProvider(QObject):
    name = ""
    title = ""
    needs_key = False
    popular_title = tr("Популярное")
    search_placeholder = tr("Название игры")
    subtitle = ""       # подзаголовок страницы поиска
    description = ""    # описание в настройках

    def __init__(self, http: Http, settings: Settings, parent=None):
        super().__init__(parent)
        self.http = http
        self.settings = settings

    def is_ready(self) -> bool:
        return True

    def search(self, query: str, page: int, on_ok: OnResults, on_err: OnError) -> None:
        raise NotImplementedError

    def popular(self, page: int, on_ok: OnResults, on_err: OnError) -> None:
        raise NotImplementedError

    def details(self, game: Game, on_ok: OnDetails, on_err: OnError) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------ помощники

    def error_message(self, status: int | None) -> str:
        if status == BAD_RESPONSE:
            return tr("Сервер {service} вернул некорректный ответ. Попробуйте позже.", service=self.title)
        if status == 429:
            return tr("{service} временно ограничил число запросов. Подождите минуту и повторите.", service=self.title)
        if status:
            return tr("Сервер {service} ответил ошибкой {status}. Попробуйте позже.", service=self.title, status=status)
        return tr("Нет связи с {service}. Проверьте подключение к интернету.", service=self.title)

    def fail_later(self, on_err: OnError, message: str) -> None:
        """Сообщает об ошибке асинхронно, как и настоящий запрос."""
        QTimer.singleShot(0, lambda: call_safely(on_err, message))
