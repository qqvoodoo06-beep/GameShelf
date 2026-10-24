"""Каталог RAWG (https://rawg.io/apidocs). Нужен бесплатный API-ключ, зато есть консольные игры."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable
from urllib.parse import quote, urlencode

from ..i18n import tr
from ..models import Game
from .base import PAGE_SIZE, CatalogProvider, GameDetails, OnDetails, OnError, OnResults, SearchResult

API_ROOT = "https://api.rawg.io/api"
NO_KEY_MESSAGE = tr("Для каталога RAWG нужен API-ключ. Добавьте его в настройках или выберите каталог Steam.")


class RawgProvider(CatalogProvider):
    name = "rawg"
    title = "RAWG"
    needs_key = True
    popular_title = tr("Популярное за последний год")
    search_placeholder = tr("Название игры, например «Hollow Knight»")
    subtitle = tr("Каталог RAWG — игры для ПК и консолей")
    description = tr("Есть консольные игры, но нужен бесплатный API-ключ с rawg.io. Описания на английском.")

    def is_ready(self) -> bool:
        return bool(self.settings.rawg_key)

    def search(self, query: str, page: int, on_ok: OnResults, on_err: OnError) -> None:
        self._list({"search": query, "search_precise": "true", "exclude_additions": "true"}, page, on_ok, on_err)

    def popular(self, page: int, on_ok: OnResults, on_err: OnError) -> None:
        today = date.today()
        dates = f"{today - timedelta(days=365)},{today}"
        self._list({"dates": dates, "ordering": "-added", "exclude_additions": "true"}, page, on_ok, on_err)

    def details(self, game: Game, on_ok: OnDetails, on_err: OnError) -> None:
        def parse(data: dict) -> None:
            on_ok(GameDetails(
                game=self._game(data),
                description=(data.get("description_raw") or "").strip(),
                developers=[x["name"] for x in data.get("developers") or [] if x.get("name")],
                publishers=[x["name"] for x in data.get("publishers") or [] if x.get("name")],
                official_genres=[x["name"] for x in data.get("genres") or [] if x.get("name")],
                playtime=data.get("playtime") or None,
            ))

        self._get(f"/games/{game.id}", {}, parse, on_err)

    def validate_key(self, key: str, on_ok: Callable[[], None], on_err: OnError) -> None:
        self._get("/games", {"page_size": 1}, lambda _data: on_ok(), on_err, key=key)

    def error_message(self, status: int | None) -> str:
        if status in (401, 403):
            return tr("API-ключ RAWG не подошёл. Проверьте его в настройках.")
        if status == 404:
            return tr("Игра не найдена в каталоге RAWG.")
        return super().error_message(status)

    # ------------------------------------------------------------------ внутреннее

    def _list(self, params: dict, page: int, on_ok: OnResults, on_err: OnError) -> None:
        def parse(data: dict) -> None:
            games = [self._game(item) for item in data.get("results") or []]
            on_ok(SearchResult(games, data.get("count"), bool(data.get("next"))))

        self._get("/games", {**params, "page": page + 1, "page_size": PAGE_SIZE}, parse, on_err)

    def _get(self, path: str, params: dict, on_ok: Callable[[dict], None], on_err: OnError,
             key: str | None = None) -> None:
        key = key or self.settings.rawg_key
        if not key:
            self.fail_later(on_err, NO_KEY_MESSAGE)
            return

        def check(payload) -> None:
            if isinstance(payload, dict):
                on_ok(payload)
            else:
                on_err(self.error_message(-1))

        url = f"{API_ROOT}{path}?{urlencode({'key': key, **params}, quote_via=quote)}"
        self.http.get_json(url, check, lambda status: on_err(self.error_message(status)))

    def _game(self, data: dict) -> Game:
        platforms = [p["platform"]["name"] for p in data.get("parent_platforms") or [] if p.get("platform")]
        if not platforms:
            platforms = [p["platform"]["name"] for p in data.get("platforms") or [] if p.get("platform")]
        rating = float(data.get("rating") or 0)
        image = data.get("background_image") or ""
        return Game(
            id=int(data["id"]), name=data.get("name") or tr("Без названия"), source=self.name,
            slug=data.get("slug") or "", cover_url=image, hero_url=image, released=data.get("released") or "",
            genres=[g["name"] for g in data.get("genres") or [] if g.get("name")], platforms=platforms,
            metacritic=data.get("metacritic"), score=round(rating * 20) if rating else None,
        )
