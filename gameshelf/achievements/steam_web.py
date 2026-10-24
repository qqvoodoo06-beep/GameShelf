"""Список достижений Steam и их редкость — по сети, без ключа и без входа, с кэшем на диске.

Факта и времени получения здесь нет: их берём из файлов Steam или эмулятора и сопоставляем по API-имени.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer

from .. import i18n
from ..i18n import tr
from ..net import BAD_RESPONSE, Http, call_safely
from .models import Achievement, steam_icon_url

# Недокументированный метод клиента Steam. Единственный, что без ключа отдаёт сразу русские тексты,
# описания скрытых достижений, обе иконки и редкость (GetSchemaForGame без ключа отвечает 400).
API_URL = "https://api.steampowered.com/IPlayerService/GetGameAchievements/v1/"
# Без language Steam отвечает {"response":{}} — как будто у игры нет достижений. Язык — язык интерфейса
# (i18n.steam_language()), спрашиваем его в момент запроса, а не один раз при импорте
# Соединение со Steam иногда сбрасывается ещё при TLS-рукопожатии (WinError 10054), повтор помогает
RETRY_DELAY_MS = 1500
# Больше запросов разом не шлём: Steam не любит залпов, а Qt включает ожидание свободного соединения
# в таймаут — при большой библиотеке хвост очереди падал бы по таймауту, не дождавшись отправки
MAX_PARALLEL = 4
CACHE_VERSION = 1

OnAchievements = Callable[[list[Achievement]], None]
OnError = Callable[[str], None]


class SteamAchievementsWeb(QObject):
    """Загружает достижения игры Steam; повторные запросы одной игры объединяет в один."""

    def __init__(self, http: Http, cache_dir: Path, parent=None):
        super().__init__(parent)
        self._http = http
        self._cache_dir = Path(cache_dir)
        self._pending: dict[int, list[tuple[OnAchievements, OnError]]] = {}
        # Стопка ожидающих отправки (appid, это повтор): последний запрос уходит первым —
        # скорее всего, его ждёт открытая сейчас страница, а не фоновая загрузка всей библиотеки
        self._waiting: list[tuple[int, bool]] = []
        self._running = 0

    # ------------------------------------------------------------------ публичное

    def fetch(self, appid: int, on_ok: OnAchievements, on_err: OnError, max_age_hours: float = 72) -> None:
        """Достижения игры. Свежий кэш отдаётся без сети; max_age_hours <= 0 — всегда спросить Steam.

        Колбэки всегда вызываются асинхронно. Если Steam недоступен, но есть устаревший кэш, придёт он.
        """
        app = _appid(appid)
        if app is None:
            QTimer.singleShot(0, self, lambda: call_safely(on_err, tr("Неизвестный номер игры в Steam.")))
            return

        cache = self._read_cache(app)
        if cache is not None and _is_fresh(cache[0], max_age_hours):
            raw_items = cache[1]
            QTimer.singleShot(0, self, lambda: call_safely(on_ok, _achievements(app, raw_items)))
            return

        if app in self._pending:
            self._pending[app].append((on_ok, on_err))
            self._raise_priority(app)
            return
        self._pending[app] = [(on_ok, on_err)]
        self._request(app, retried=False)

    def cached(self, appid: int) -> list[Achievement] | None:
        """Кэш с диска независимо от его возраста — для быстрых значков в интерфейсе. None — кэша нет."""
        app = _appid(appid)
        cache = self._read_cache(app) if app is not None else None
        return _achievements(app, cache[1]) if cache is not None else None

    # ------------------------------------------------------------------ сеть

    def _request(self, appid: int, retried: bool) -> None:
        self._waiting.append((appid, retried))
        self._send_waiting()

    def _raise_priority(self, appid: int) -> None:
        for index, (waiting_appid, _retried) in enumerate(self._waiting):
            if waiting_appid == appid:
                self._waiting.append(self._waiting.pop(index))
                return

    def _send_waiting(self) -> None:
        while self._running < MAX_PARALLEL and self._waiting:
            appid, retried = self._waiting.pop()
            self._running += 1
            url = f"{API_URL}?{urlencode({'appid': appid, 'language': i18n.steam_language()})}"
            self._http.get_json(url, lambda payload, a=appid: self._on_reply(self._on_payload, a, payload),
                                lambda status, a=appid, r=retried: self._on_reply(self._on_error, a, status, r))

    def _on_reply(self, handler, *args) -> None:
        # Http зовёт ровно один колбэк на запрос — здесь место в очереди и освобождается
        self._running -= 1
        try:
            handler(*args)
        finally:
            self._send_waiting()

    def _on_payload(self, appid: int, payload) -> None:
        raw_items = _raw_items(payload)
        if raw_items is None:
            self._on_error(appid, BAD_RESPONSE, retried=True)
            return
        if not raw_items:
            # Пустой ответ не отличить от сбоя (так же Steam отвечает без language), а игра
            # не может просто потерять достижения — поэтому уже известный список не затираем
            old = self._read_cache(appid)
            if old is not None and old[1]:
                self._finish(appid, old[1])
                return
        self._write_cache(appid, raw_items)
        self._finish(appid, raw_items)

    def _on_error(self, appid: int, status: int | None, retried: bool) -> None:
        if not retried and (status is None or status >= 500):
            QTimer.singleShot(RETRY_DELAY_MS, self, lambda: self._request(appid, retried=True))
            return
        stale = self._read_cache(appid)
        if stale is not None:
            self._finish(appid, stale[1])
            return
        message = _error_message(status)
        for _on_ok, on_err in self._pending.pop(appid, []):
            call_safely(on_err, message)

    def _finish(self, appid: int, raw_items: list[dict]) -> None:
        # Каждому свой список: получатель может дописать в объекты прогресс игрока
        for on_ok, _on_err in self._pending.pop(appid, []):
            call_safely(on_ok, _achievements(appid, raw_items))

    # ------------------------------------------------------------------ кэш

    def _cache_path(self, appid: int) -> Path:
        # Язык в имени файла: после смены языка не покажем тексты на прежнем
        return self._cache_dir / f"{appid}_{i18n.steam_language()}.json"

    def _read_cache(self, appid: int) -> tuple[float, list[dict]] | None:
        """(время загрузки, сырые элементы ответа) или None, если кэша нет или он испорчен."""
        try:
            data = json.loads(self._cache_path(appid).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
                return None
            fetched_at, items = data.get("fetched_at"), data.get("items")
            if not isinstance(fetched_at, (int, float)) or not isinstance(items, list):
                return None
            return float(fetched_at), [item for item in items if isinstance(item, dict)]
        except (OSError, ValueError, OverflowError, RecursionError):
            return None

    def _write_cache(self, appid: int, raw_items: list[dict]) -> None:
        data = {"version": CACHE_VERSION, "appid": appid, "language": i18n.steam_language(),
                "fetched_at": time.time(), "items": raw_items}
        path = self._cache_path(appid)
        # Через временный файл: если компьютер выключится посреди записи, старый кэш останется целым
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        # ValueError — одиночный суррогат «\ud800» из ответа не кодируется в UTF-8. Без кэша обойдёмся,
        # а вот исключение отсюда оставило бы запрос незавершённым, и игра «зависла» бы до перезапуска
        except (OSError, ValueError, RecursionError):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------- разбор ответа

def _appid(value) -> int | None:
    try:
        appid = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return appid if appid > 0 else None


def _is_fresh(fetched_at: float, max_age_hours: float) -> bool:
    try:
        age = time.time() - fetched_at
        # Время из будущего — часы компьютера перевели назад; такому кэшу не доверяем
        return max_age_hours > 0 and 0 <= age <= max_age_hours * 3600
    except TypeError:
        return False


def _raw_items(payload) -> list[dict] | None:
    """Элементы ответа; [] — у игры нет достижений; None — ответ не похож на ответ Steam."""
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        return None
    items = payload["response"].get("achievements", [])
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict) and _text(item.get("internal_name"))]


def _achievements(appid: int, raw_items: list[dict]) -> list[Achievement]:
    result = []
    for raw in raw_items:
        api = _text(raw.get("internal_name"))
        if not api:
            continue
        result.append(Achievement(
            api=api,
            # У некоторых игр (Hollow Knight) в текстах хвостовые пробелы: «Зверь »
            name=_text(raw.get("localized_name")).strip(),
            description=_text(raw.get("localized_desc")).strip(),
            icon=steam_icon_url(appid, _text(raw.get("icon"))),
            icon_gray=steam_icon_url(appid, _text(raw.get("icon_gray"))),
            hidden=raw.get("hidden") in (True, 1, "1", "true"),
            rarity=_percent(raw.get("player_percent_unlocked")),
        ))
    return result


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _percent(value) -> float | None:
    """Steam присылает процент строкой: "76.7"."""
    try:
        percent = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return percent if math.isfinite(percent) else None


def _error_message(status: int | None) -> str:
    if status is None:
        return tr("Нет связи со Steam — список достижений не загрузился. Проверьте подключение к интернету.")
    if status == BAD_RESPONSE:
        return tr("Steam прислал непонятный ответ вместо списка достижений. Попробуйте позже.")
    if status == 429:
        return tr("Steam временно ограничил число запросов. Подождите несколько минут и повторите.")
    return tr("Сервер Steam ответил ошибкой {status} при загрузке достижений. Попробуйте позже.", status=status)
