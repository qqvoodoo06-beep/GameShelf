"""Каталог Steam. Работает без регистрации и ключей: использует публичные запросы магазина."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Callable
from urllib.parse import quote, urlencode

from .. import i18n
from ..i18n import tr
from ..models import Game
from .base import CatalogProvider, GameDetails, OnDetails, OnError, OnResults, SearchResult

STORE = "https://store.steampowered.com"
API = "https://api.steampowered.com"
ASSET_ROOT = "https://shared.akamai.steamstatic.com/store_item_assets/"
# В российском регионе Steam прячет игры, которые там не продаются (например, «Ведьмак 3»),
# поэтому каталог запрашиваем для США при любом языке. Названия и описания всё равно приходят
# на языке интерфейса — его передаём в каждом запросе (i18n.steam_language()).
REGION = "US"
GAME_TYPE = 0
GENRES_ON_CARD = 3
# Поиск Steam отдаёт страницы блоками по 25 и округляет start вниз до кратного 25
STEAM_PAGE_SIZE = 25

# Метки Steam, которые описывают не жанр, а особенности: «Для одного игрока», «Отличный саундтрек»…
NON_GENRE_TAGS = {
    493, 1654, 1685, 1697, 1719, 1742, 1756, 1775, 3839, 3843, 3859, 3871, 3878, 3916, 3964, 3968, 3993,
    4004, 4026, 4094, 4136, 4166, 4175, 4182, 4191, 4195, 4252, 4305, 4342, 4345, 4559, 4667, 4726, 4747,
    4791, 5350, 6426, 6650, 6730, 6815, 6971, 7208, 7250, 7368, 7481, 12095,
}

_APPID_RE = re.compile(r'data-ds-appid="(\d+)"')
_RU_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8,
              "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
_EN_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
              "nov": 11, "dec": 12}


def _url(base: str, params: dict) -> str:
    return f"{base}?{urlencode(params, quote_via=quote)}"


def html_to_text(html: str) -> str:
    """Описания в Steam — HTML. Превращаем в обычный текст с абзацами."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)<li[^>]*>", "\n• ", text)
    text = re.sub(r"(?i)</(p|h[1-6]|ul|ol|div)>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n\n(?=• )", "\n", text)  # пункты списка — без пустых строк между ними
    return text.strip()


def parse_ru_date(text: str) -> str:
    """«24 фев. 2017 г.» -> «2017-02-24». Если не получилось — пустая строка."""
    match = re.search(r"(\d{1,2})\s+([а-яё]+)\.?\s+(\d{4})", text.lower())
    if not match:
        return ""
    month = _RU_MONTHS.get(match.group(2)[:3])
    if not month:
        return ""
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"


def parse_en_date(text: str) -> str:
    """«24 Feb, 2017» или «Feb 24, 2017» (Steam на английском) -> «2017-02-24». Если не получилось — пустая строка."""
    lowered = text.lower()
    match = re.search(r"(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})", lowered)
    if match:
        day, name, year = match.groups()
    else:
        match = re.search(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", lowered)
        if not match:
            return ""
        name, day, year = match.groups()
    month = _EN_MONTHS.get(name[:3])
    if not month:
        return ""
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def parse_release_text(text: str) -> str:
    """Дата из appdetails: точная — «ГГГГ-ММ-ДД», расплывчатая («2027», «I кв. 2027») — только год."""
    exact = parse_ru_date(text) or parse_en_date(text)
    if exact:
        return exact
    year = re.search(r"\b(19|20)\d{2}\b", text)
    return year.group(0) if year else ""


class SteamProvider(CatalogProvider):
    name = "steam"
    title = "Steam"
    popular_title = tr("Лидеры продаж Steam")
    search_placeholder = tr("Название игры — можно по-русски, например «Ведьмак»")
    subtitle = tr("Каталог Steam — без регистрации. Найдите игру и добавьте её в библиотеку")
    description = tr("Без регистрации, описания на русском, обложки как у коробок. Только игры из Steam.")

    def __init__(self, http, settings, parent=None):
        super().__init__(http, settings, parent)
        self._tag_names: dict[int, str] | None = None
        self._tag_waiters: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ списки игр

    def search(self, query: str, page: int, on_ok: OnResults, on_err: OnError) -> None:
        self._search_page({"term": query}, page, on_ok, on_err)

    def popular(self, page: int, on_ok: OnResults, on_err: OnError) -> None:
        self._search_page({"filter": "topsellers"}, page, on_ok, on_err)

    def _search_page(self, extra: dict, page: int, on_ok: OnResults, on_err: OnError) -> None:
        # Тот же запрос, что делает сайт Steam при бесконечной прокрутке; category1=998 — только игры
        params = {"query": "", "start": page * STEAM_PAGE_SIZE, "count": STEAM_PAGE_SIZE, "dynamic_data": "",
                  "sort_by": "_ASC", **extra, "category1": 998, "force_infinite": 1, "infinite": 1,
                  "l": i18n.steam_language(), "cc": REGION}
        self.http.get_json(_url(f"{STORE}/search/results/", params),
                           lambda payload: self._on_search_page(payload, page, on_ok, on_err),
                           lambda status: on_err(self.error_message(status)))

    def _on_search_page(self, payload, page: int, on_ok: OnResults, on_err: OnError) -> None:
        if not isinstance(payload, dict) or not payload.get("success", 1):
            on_err(self.error_message(-1))
            return
        total = int(payload.get("total_count") or 0)
        start = int(payload.get("start") or page * STEAM_PAGE_SIZE)
        ids = list(dict.fromkeys(int(x) for x in _APPID_RE.findall(payload.get("results_html") or "")))
        if not ids:
            on_ok(SearchResult([], total, False))
            return
        has_more = start + STEAM_PAGE_SIZE < total
        self._load_items(ids, lambda games: on_ok(SearchResult(games, total, has_more)), on_err)

    def _load_items(self, ids: list[int], on_games: Callable[[list[Game]], None], on_err: OnError) -> None:
        """Одним запросом получает названия, обложки, метки, дату выхода и отзывы для списка игр."""
        def request():
            body = {
                "ids": [{"appid": appid} for appid in ids],
                "context": {"language": i18n.steam_language(), "country_code": REGION, "steam_realm": 1},
                "data_request": {"include_basic_info": True, "include_release": True, "include_reviews": True,
                                 "include_assets": True, "include_platforms": True, "include_tag_count": 12},
            }
            url = _url(f"{API}/IStoreBrowseService/GetItems/v1/",
                       {"input_json": json.dumps(body, separators=(",", ":"))})
            self.http.get_json(url, lambda payload: on_games(self._games_from_items(payload, ids)),
                               lambda status: on_err(self.error_message(status)))

        self._ensure_tags(request)

    def _games_from_items(self, payload, ids: list[int]) -> list[Game]:
        items = ((payload or {}).get("response") or {}).get("store_items") or []
        by_id: dict[int, Game] = {}
        for item in items:
            if item.get("success") != 1 or item.get("type", GAME_TYPE) != GAME_TYPE:
                continue
            game = self._game_from_item(item)
            by_id[game.id] = game
        return [by_id[appid] for appid in ids if appid in by_id]

    def _game_from_item(self, item: dict) -> Game:
        appid = int(item.get("appid") or item.get("id"))
        assets = item.get("assets") or {}
        url_format = assets.get("asset_url_format") or f"steam/apps/{appid}/${{FILENAME}}"

        def asset(*names: str) -> str:
            for name in names:
                if assets.get(name):
                    return ASSET_ROOT + url_format.replace("${FILENAME}", assets[name])
            return ""

        fallback = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
        cover = asset("library_capsule", "header", "main_capsule") or fallback
        hero = asset("library_hero", "header", "main_capsule") or cover

        released = ""
        release = item.get("release") or {}
        timestamp = release.get("steam_release_date") or release.get("original_release_date")
        if timestamp:
            released = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date().isoformat()
            # У невышедших игр с датой «2027» или «I кв. 2027» Steam присылает конец периода — оставляем только год
            if release.get("is_coming_soon") and release.get("coming_soon_display", "date_full") != "date_full":
                released = released[:4]

        reviews = (item.get("reviews") or {}).get("summary_filtered") or {}
        score = reviews.get("percent_positive") if reviews.get("review_count") else None

        tag_ids = [t.get("tagid") for t in item.get("tags") or []] or item.get("tagids") or []
        names = self._tag_names or {}
        genres = [names[t] for t in tag_ids if t in names and t not in NON_GENRE_TAGS][:GENRES_ON_CARD]

        platforms_info = item.get("platforms") or {}
        platforms = [label for key, label in (("windows", "Windows"), ("mac", "macOS"), ("steamos_linux", "Linux"))
                     if platforms_info.get(key)]
        if platforms_info.get("steam_deck_compat_category") in (2, 3):
            platforms.append("Steam Deck")

        return Game(
            id=appid, name=item.get("name") or tr("Без названия"), source=self.name,
            slug=item.get("store_url_slug") or "", cover_url=cover, hero_url=hero, released=released,
            genres=genres, platforms=platforms, score=score, score_label=reviews.get("review_score_label") or "",
        )

    def _ensure_tags(self, then: Callable[[], None]) -> None:
        """Метки приходят числами — один раз за сеанс загружаем их названия на языке интерфейса."""
        if self._tag_names is not None:
            then()
            return
        self._tag_waiters.append(then)
        if len(self._tag_waiters) > 1:
            return  # загрузка уже идёт

        def finish(names: dict[int, str] | None) -> None:
            self._tag_names = names
            waiters, self._tag_waiters = self._tag_waiters, []
            for waiter in waiters:
                waiter()

        def on_tags(payload) -> None:
            if isinstance(payload, list):
                finish({t["tagid"]: t["name"] for t in payload if "tagid" in t and "name" in t})
            else:
                finish(None)

        # Без названий меток игры всё равно показываем, просто без жанров (и попробуем снова позже)
        self.http.get_json(f"{STORE}/tagdata/populartags/{i18n.steam_language()}", on_tags,
                           lambda _status: finish(None))

    # ------------------------------------------------------------------ подробности

    def details(self, game: Game, on_ok: OnDetails, on_err: OnError) -> None:
        url = _url(f"{STORE}/api/appdetails", {"appids": game.id, "l": i18n.steam_language(), "cc": REGION})
        self.http.get_json(url, lambda payload: self._on_details(payload, game, on_ok, on_err),
                           lambda status: on_err(self.error_message(status)))

    def _on_details(self, payload, game: Game, on_ok: OnDetails, on_err: OnError) -> None:
        entry = (payload or {}).get(str(game.id)) if isinstance(payload, dict) else None
        if not entry or not entry.get("success") or not isinstance(entry.get("data"), dict):
            on_err(tr("Steam не отдал данные об этой игре — возможно, её убрали из магазина."))
            return
        data = entry["data"]
        fresh = Game(id=game.id, name=data.get("name") or game.name, source=self.name,
                     metacritic=(data.get("metacritic") or {}).get("score"))
        # Дата могла поменяться (перенос релиза, объявили точный день) — берём актуальную
        fresh.released = parse_release_text((data.get("release_date") or {}).get("date") or "")
        if not game.platforms:
            info = data.get("platforms") or {}
            fresh.platforms = [label for key, label in (("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux"))
                               if info.get(key)]
        if not game.cover_url and data.get("header_image"):
            fresh.cover_url = fresh.hero_url = data["header_image"]
        details = GameDetails(
            game=fresh,
            description=self._description(data),
            developers=data.get("developers") or [],
            publishers=data.get("publishers") or [],
            official_genres=[g["description"] for g in data.get("genres") or [] if g.get("description")],
        )
        # Метки и подпись отзывов — тоже свежие: жанров могло не быть (не загрузились названия меток при добавлении),
        # а сохранённые могли остаться на прежнем языке интерфейса
        def with_tags(games: list[Game]) -> None:
            if games:
                fresh.score, fresh.score_label = games[0].score, games[0].score_label
            fresh.genres = (games[0].genres if games else []) or details.official_genres[:GENRES_ON_CARD]
            on_ok(details)

        def without_tags(_message: str) -> None:
            if not game.genres:  # уже сохранённые жанры не заменяем жанрами-категориями магазина
                fresh.genres = details.official_genres[:GENRES_ON_CARD]
            on_ok(details)

        self._load_items([game.id], with_tags, without_tags)

    def refresh(self, ids: list[int], on_games: Callable[[list[Game]], None], on_done: Callable[[bool], None]) -> None:
        """Свежие данные магазина для игр библиотеки, пачками (после смены языка интерфейса).
        on_done(True) — получено всё, вместе с названиями меток."""
        chunks = [ids[i:i + STEAM_PAGE_SIZE] for i in range(0, len(ids), STEAM_PAGE_SIZE)]
        complete = True

        def next_chunk() -> None:
            nonlocal complete
            if not chunks:
                on_done(complete and self._tag_names is not None)
                return

            def got(games: list[Game]) -> None:
                on_games(games)
                next_chunk()

            def failed(_message: str) -> None:
                nonlocal complete
                complete = False
                next_chunk()

            self._load_items(chunks.pop(0), got, failed)

        next_chunk()

    @staticmethod
    def _description(data: dict) -> str:
        # У некоторых игр «Об игре» — одни картинки, а полное описание — только заголовок.
        # Берём самый содержательный из трёх вариантов (при равенстве — «Об игре»).
        texts = [html_to_text(data.get(field) or "")
                 for field in ("about_the_game", "detailed_description", "short_description")]
        return max(texts, key=len)
