"""Достижения Steam из локальных файлов клиента: без сети и без ключа API.

Схема (названия на всех языках, иконки, прогресс-бары) — appcache/stats/UserGameStatsSchema_<appid>.bin,
она общая для всех аккаунтов. Прогресс аккаунта — appcache/stats/UserGameStats_<account>_<appid>.bin,
но Steam переписывает его только при выходе из аккаунта. Поэтому сверху накладываем
userdata/<account>/config/librarycache/<appid>.json: его клиент обновляет прямо во время игры,
зато списки в нём обрезаны (не больше 12 + 12 + 25 записей).

Всё только читается. На отсутствующих или битых файлах функции возвращают None/пустой результат.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import i18n
from ..tracking.installed import find_steam_path, parse_vdf, vdf_get
from .binvdf import parse_binary_vdf
from .models import Achievement, AchievementSet, steam_icon_url

_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import winreg

SOURCE = "Steam"

# SteamID64 = эта база + 32-битный номер аккаунта (так называются папки в userdata)
_STEAMID64_BASE = 76561197960265728
# 01.01.3000: дальше datetime.fromtimestamp() на Windows падает
_MAX_UNIX_TIME = 32_503_680_000
_FLOAT_STAT_TYPES = frozenset({"2", "FLOAT", "3", "AVGRATE"})
_INT_STAT_TYPES = frozenset({"1", "INT"})
_FLOAT32 = struct.Struct("<f")
_UINT32 = struct.Struct("<I")


# ---------------------------------------------------------------- мелкие помощники

def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label(value) -> str:
    """Текст для показа: разработчики иногда вводят название числом (2807960: "1200" записано как int)."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(_plain(value)) if math.isfinite(value) else ""
    return _text(value)


def _to_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        digits = text[1:] if text.startswith("-") else text
        # Только 0-9: str.isdigit() пропускает и «²», на котором int() падает
        if digits.isascii() and digits.isdigit():
            try:
                return int(text)
            except ValueError:  # больше 4300 цифр — Python отказывается переводить
                return None
    return None


def _to_float(value) -> float | None:
    """Число из схемы: min_val/max_val бывают и int, и строкой ("100", "2.5")."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _plain(number: float) -> float:
    """130.0 → 130, а float32-шум 70.55000305175781 → 70.55: в интерфейсе это «130 / 146» и «70.55 / 100»."""
    number = float(number)
    if number.is_integer():
        return int(number)
    return float(f"{number:.7g}")  # у float32 только ~7 значащих цифр, дальше мусор


def _progress_value(current: float, low: float, high: float) -> float:
    """Текущее значение для «сейчас / нужно»: не больше max, но и не подтягиваем до min.

    У ступенчатых достижений min_val бывает 15000 при опыте игрока 602 — Steam (librarycache)
    тоже показывает настоящее значение, а не min. Ниже нуля опускаемся, только если так задан сам диапазон.
    """
    return _plain(max(min(current, high), min(low, 0.0)))


def _unix_time(value) -> int | None:
    stamp = _to_int(value)
    return stamp if stamp is not None and 0 < stamp < _MAX_UNIX_TIME else None


def _account_id(value) -> int | None:
    """Номер аккаунта Steam (32 бита, не 0) или None."""
    number = _to_int(value)
    return number if number is not None and 0 < number < 2 ** 32 else None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except (OSError, ValueError):
        return None


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (OSError, ValueError):  # ValueError — нулевой символ в пути
        return None


def _read_text(path: Path) -> str:
    data = _read_bytes(path)
    return data.decode("utf-8", "replace") if data is not None else ""


def _load_binary(path: Path) -> dict | None:
    """Бинарный VDF или None: файл мог исчезнуть или быть недописан, пока Steam его сохраняет."""
    data = _read_bytes(path)
    if not data:
        return None
    try:
        return parse_binary_vdf(data)
    except ValueError:
        return None


def _steam_dir(steam_path: Path | str | None) -> Path | None:
    if steam_path:
        try:
            return Path(steam_path)
        except TypeError:
            return None
    return find_steam_path()


def _appid(value) -> int | None:
    number = _to_int(value)
    return number if number is not None and number > 0 else None


# ---------------------------------------------------------------- активный аккаунт

def _registry_active_user() -> int | None:
    """Аккаунт, под которым сейчас запущен Steam (без запущенного клиента там 0)."""
    if not _IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess",
                            0, winreg.KEY_QUERY_VALUE) as key:
            value, _kind = winreg.QueryValueEx(key, "ActiveUser")
    except OSError:
        return None
    return _account_id(value)


def _loginusers_account(steam: Path) -> int | None:
    """Аккаунт с самым свежим входом из config/loginusers.vdf."""
    users = vdf_get(parse_vdf(_read_text(steam / "config" / "loginusers.vdf")), "users", default={})
    if not isinstance(users, dict):
        return None
    best: tuple[int, int, int] | None = None
    for steam_id, info in users.items():
        if not isinstance(info, dict):
            continue
        id64 = _to_int(steam_id)
        account = _account_id(id64 - _STEAMID64_BASE) if id64 is not None else None
        if account is None:
            continue
        # MostRecent — у старых версий клиента, где timestamp бывает пустым
        rank = (_to_int(vdf_get(info, "Timestamp")) or 0, _to_int(vdf_get(info, "MostRecent")) or 0, account)
        if best is None or rank[:2] > best[:2]:
            best = rank
    return best[2] if best else None


def _localconfig_account(steam: Path) -> int | None:
    """Аккаунт, чей localconfig.vdf менялся последним."""
    best: tuple[float, int] | None = None
    try:
        folders = list((steam / "userdata").iterdir())
    except (OSError, ValueError):
        return None
    for folder in folders:
        account = _account_id(folder.name)
        if account is None:
            continue
        mtime = _mtime(folder / "config" / "localconfig.vdf")
        if mtime is not None and (best is None or mtime > best[0]):
            best = (mtime, account)
    return best[1] if best else None


def find_active_account(steam_path: Path | None = None) -> int | None:
    """Номер аккаунта Steam (имя папки в userdata), чей прогресс показывать, или None."""
    steam = _steam_dir(steam_path)
    active = _registry_active_user()
    # Реестр относится к Steam этой системы; для другой папки Steam его аккаунт может не подходить
    if active is not None and (steam is None or _is_dir(steam / "userdata" / str(active))):
        return active
    if steam is None:
        return None
    return _loginusers_account(steam) or _localconfig_account(steam)


# ---------------------------------------------------------------- пути

def _schema_path(steam: Path, appid: int) -> Path:
    return steam / "appcache" / "stats" / f"UserGameStatsSchema_{appid}.bin"


def _stats_path(steam: Path, account: int, appid: int) -> Path:
    return steam / "appcache" / "stats" / f"UserGameStats_{account}_{appid}.bin"


def _librarycache_path(steam: Path, account: int, appid: int) -> Path:
    return steam / "userdata" / str(account) / "config" / "librarycache" / f"{appid}.json"


def progress_paths(appid: int, steam_path: Path | None = None, account: int | None = None) -> list[str]:
    """Файлы, изменение которых означает новый прогресс (могут ещё не существовать)."""
    appid = _appid(appid)
    steam = _steam_dir(steam_path)
    if appid is None or steam is None:
        return []
    account = _account_id(account) or find_active_account(steam)
    if account is None:
        return []
    return [str(_stats_path(steam, account, appid)), str(_librarycache_path(steam, account, appid))]


# ---------------------------------------------------------------- схема

@dataclass
class _Entry:
    """Достижение из схемы вместе с тем, где искать его прогресс."""
    achievement: Achievement
    stat_id: str
    bit: int | None
    progress: tuple[str, float, float] | None  # (имя стата-счётчика, min, max)


def _localized(value, language: str) -> tuple[str, str, bool]:
    """(текст на языке с откатом на английский и token, английский текст, нашёлся ли нужный язык)."""
    if not isinstance(value, dict):  # у app 480 вместо словаря языков просто строка
        text = _label(value)
        return text, text, False
    english = _label(value.get("english"))
    wanted = _label(value.get(language))
    if not wanted:
        # Регистр кодов языков у разработчиков гуляет: "latam" и "LATAM"
        folded = language.casefold()
        wanted = next((_label(text) for code, text in value.items()
                       if isinstance(code, str) and code.casefold() == folded and _label(text)), "")
    if wanted:
        return wanted, english, True
    return english or _text(value.get("token")), english, False


def _is_hidden(value) -> bool:
    return isinstance(value, (int, str)) and str(value).strip().casefold() in ("1", "true")


def _bit(value) -> int | None:
    number = _to_int(value)
    return number if number is not None and 0 <= number < 32 else None


def _progress_spec(value) -> tuple[str, float, float] | None:
    """Блок progress: {min_val, max_val, value: {operation: "statvalue", operand1: <имя стата>}}."""
    if not isinstance(value, dict):
        return None
    formula = value.get("value")
    if not isinstance(formula, dict):
        return None
    operation = _text(formula.get("operation")).casefold()
    stat_name = _text(formula.get("operand1"))
    low, high = _to_float(value.get("min_val")), _to_float(value.get("max_val"))
    if operation not in ("", "statvalue") or not stat_name or low is None or high is None or high <= low:
        return None
    return stat_name, low, high


def _read_entry(appid: int, stat_id: str, key: str, raw: dict, language: str) -> _Entry | None:
    api = _label(raw.get("name"))
    if not api:
        return None
    display = raw.get("display")
    display = display if isinstance(display, dict) else {}
    name, name_en, _ = _localized(display.get("name"), language)
    description, description_en, localized = _localized(display.get("desc"), language)
    # Darkwood: русское описание просто повторяет название, а английское — настоящее
    if localized and description == name and description_en and description_en != name_en:
        description = description_en
    achievement = Achievement(
        api=api,
        name=name or api,
        description=description,
        # Относительные имена вроде "hl2_get_crowbar.jpg" на этом пути тоже работают
        icon=steam_icon_url(appid, _text(display.get("icon"))),
        icon_gray=steam_icon_url(appid, _text(display.get("icon_gray"))),
        hidden=_is_hidden(display.get("hidden")),
    )
    return _Entry(achievement, stat_id, _bit(raw.get("bit", key)), _progress_spec(raw.get("progress")))


def _read_schema(path: Path, appid: int, language: str) -> tuple[list[_Entry], dict[str, dict]] | None:
    """(достижения в порядке схемы, stat_id → описание стата) или None."""
    root = _load_binary(path)
    if not root:
        return None
    body = root.get(str(appid))
    if not isinstance(body, dict) and len(root) == 1:
        body = next(iter(root.values()))
    stats = body.get("stats") if isinstance(body, dict) else None
    if not isinstance(stats, dict):
        return None
    entries: list[_Entry] = []
    for stat_id, stat in stats.items():
        # Достижения лежат только в статах типа ACHIEVEMENTS ("4"), по 32 бита на стат;
        # пустой "bits" там встречается (157 штук на этой машине) — просто пропускаем
        bits = stat.get("bits") if isinstance(stat, dict) else None
        if not isinstance(bits, dict):
            continue
        for key, raw in bits.items():
            entry = _read_entry(appid, stat_id, key, raw, language) if isinstance(raw, dict) else None
            if entry:
                entries.append(entry)
    return entries, {stat_id: stat for stat_id, stat in stats.items() if isinstance(stat, dict)}


# ---------------------------------------------------------------- прогресс из UserGameStats_*.bin

def _stats_by_name(stats: dict[str, dict]) -> tuple[dict[str, tuple[str, dict]], dict[str, tuple[str, dict]]]:
    """Прогресс-бары ссылаются на стат по имени, а в файле пользователя статы лежат по stat_id.

    (точное имя → (stat_id, стат), имя в casefold → то же) — регистр в operand1 бывает другим.
    """
    exact: dict[str, tuple[str, dict]] = {}
    for stat_id, stat in stats.items():
        name = _text(stat.get("name"))
        if name:
            exact.setdefault(name, (stat_id, stat))
    folded: dict[str, tuple[str, dict]] = {}
    for name, found in exact.items():
        folded.setdefault(name.casefold(), found)
    return exact, folded


def _stat_value(stat_id: str, stat: dict, cache: dict) -> float | None:
    """Текущее значение INT/FLOAT-стата пользователя."""
    kind = stat.get("type")
    kind = str(kind if kind is not None else stat.get("type_int")).strip().upper()
    if kind not in _INT_STAT_TYPES and kind not in _FLOAT_STAT_TYPES:
        return None
    record = cache.get(stat_id)
    data = record.get("data") if isinstance(record, dict) else None
    if data is None:
        # Стата нет в файле — у пользователя всё ещё значение по умолчанию
        return _to_float(stat.get("default")) or 0.0
    if isinstance(data, float):
        return data if math.isfinite(data) else None
    data = _to_int(data)
    if data is None:
        return None
    if kind in _FLOAT_STAT_TYPES:
        # FLOAT-стат хранится как биты float внутри int32
        value = _FLOAT32.unpack(_UINT32.pack(data & 0xFFFFFFFF))[0]
        return value if math.isfinite(value) else None
    return _to_float(data)  # не float(): строка из сотен цифр дала бы OverflowError


def _apply_stats_file(entries: list[_Entry], stats: dict[str, dict], cache: dict) -> None:
    exact, folded = _stats_by_name(stats)
    for entry in entries:
        item = entry.achievement
        record = cache.get(entry.stat_id)
        record = record if isinstance(record, dict) else {}
        bits = _to_int(record.get("data"))
        item.unlocked = bits is not None and entry.bit is not None and bool((bits & 0xFFFFFFFF) >> entry.bit & 1)
        if item.unlocked:
            # Время берём только для поднятого бита: у сброшенных достижений оно остаётся в файле
            times = record.get("AchievementTimes")
            item.unlocked_at = _unix_time(times.get(str(entry.bit))) if isinstance(times, dict) else None
        if entry.progress:
            stat_name, low, high = entry.progress
            found = exact.get(stat_name) or folded.get(stat_name.casefold())
            value = _stat_value(found[0], found[1], cache) if found else None
            if value is not None:
                item.progress = (_progress_value(value, low, high), _plain(high))


# ---------------------------------------------------------------- librarycache/<appid>.json

@dataclass
class _Library:
    """Достижения из librarycache. Ключи — API-имена в casefold: регистр в разных файлах Steam может отличаться."""
    achieved: dict[str, int | None] = field(default_factory=dict)  # api → время получения
    unachieved: set[str] = field(default_factory=set)
    rarity: dict[str, float] = field(default_factory=dict)
    progress: dict[str, tuple[float, float]] = field(default_factory=dict)
    total: int = 0
    achieved_count: int = 0

    @property
    def has_progress(self) -> bool:
        """Файл бывает с пустыми данными (nTotal=0) — тогда о прогрессе он ничего не говорит."""
        return self.total > 0 or bool(self.achieved) or bool(self.unachieved)


def _rarity(value) -> float | None:
    number = _to_float(value)
    # flAchieved — float32: 65.9000015258789 → 65.9
    return round(number, 2) if number is not None and 0 <= number <= 100 else None


def _library_item(library: _Library, item, list_name: str) -> None:
    if not isinstance(item, dict):
        return
    api = _text(item.get("strID")).casefold()
    if not api:  # в vecHighlight бывают пустые заглушки
        return
    if item.get("bAchieved") is True:
        # Одно достижение бывает в двух списках; время берём из того, где оно есть
        if library.achieved.get(api) is None:
            library.achieved[api] = _unix_time(item.get("rtUnlocked"))
    elif item.get("bAchieved") is False:
        library.unachieved.add(api)
    # В vecAchievedHidden flAchieved всегда 0 — это не настоящая редкость
    if list_name != "vecAchievedHidden":
        rarity = _rarity(item.get("flAchieved"))
        if rarity is not None:
            library.rarity.setdefault(api, rarity)
    low = _to_float(item.get("flMinProgress")) or 0.0
    current, high = _to_float(item.get("flCurrentProgress")), _to_float(item.get("flMaxProgress"))
    if current is not None and high is not None and high > low:
        library.progress[api] = (_progress_value(current, low, high), _plain(high))


def _achievement_map_rarity(library: _Library, section, appid: int) -> None:
    """achievementmap.data — JSON-строка [[appid, [[strID, {flAchieved…}], …]]]: ещё один источник редкости."""
    data = section.get("data") if isinstance(section, dict) else None
    if not isinstance(data, str) or len(data) < 3:
        return
    try:
        games = json.loads(data)
    except (ValueError, RecursionError):
        return
    for game in games if isinstance(games, list) else ():
        if not isinstance(game, list) or len(game) != 2 or _to_int(game[0]) != appid or not isinstance(game[1], list):
            continue
        for pair in game[1]:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[1], dict):
                continue
            api = _text(pair[0]).casefold()
            rarity = _rarity(pair[1].get("flAchieved"))
            if api and rarity is not None:
                library.rarity.setdefault(api, rarity)


def _read_library(path: Path, appid: int) -> _Library | None:
    raw = _read_bytes(path)
    if not raw or not raw.strip():  # пустые файлы по 0 байт встречаются
        return None
    try:
        pairs = json.loads(raw.decode("utf-8-sig", "replace"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(pairs, list):
        return None
    # Файл — список пар [["achievements", {...}], ["achievementmap", {...}], …]
    sections = {pair[0]: pair[1] for pair in pairs
                if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)}
    library = _Library()
    achievements = sections.get("achievements")
    data = achievements.get("data") if isinstance(achievements, dict) else None
    if isinstance(data, dict):
        library.total = max(_to_int(data.get("nTotal")) or 0, 0)
        library.achieved_count = max(_to_int(data.get("nAchieved")) or 0, 0)
        for list_name in ("vecHighlight", "vecUnachieved", "vecAchievedHidden"):
            items = data.get(list_name)
            for item in items if isinstance(items, list) else ():
                _library_item(library, item, list_name)
    _achievement_map_rarity(library, sections.get("achievementmap"), appid)
    return library


def _apply_library(entries: list[_Entry], library: _Library, stats_known: bool) -> None:
    """Накладывает librarycache поверх .bin: только добавляет полученное, ничего не отбирает."""
    schema_apis = {entry.achievement.api.casefold() for entry in entries}
    # Без .bin остальные считаем неполученными, только если в списках есть все полученные:
    # списки обрезаны, и часть полученных могла в них просто не попасть
    complete = sum(1 for api in library.achieved if api in schema_apis) >= library.achieved_count
    for entry in entries:
        item = entry.achievement
        api = item.api.casefold()
        item.rarity = library.rarity.get(api, item.rarity)
        if not library.has_progress:
            continue
        if api in library.achieved:
            item.unlocked = True
            if item.unlocked_at is None:
                item.unlocked_at = library.achieved[api]
        elif not stats_known and (complete or api in library.unachieved):
            item.unlocked = False
        progress = library.progress.get(api)
        if progress and (item.progress is None or progress[0] > item.progress[0]):
            # Максимум оставляем из схемы: librarycache мог остаться от старой версии игры
            item.progress = (min(progress[0], item.progress[1]), item.progress[1]) if item.progress else progress


# ---------------------------------------------------------------- всё вместе

def load_steam_local(appid: int, steam_path: Path | None = None, account: int | None = None,
                     language: str | None = None) -> AchievementSet | None:
    """Достижения игры из файлов Steam; None — у игры нет локальной схемы или в ней нет достижений.

    language — код языка Steam ("russian", "english"…); по умолчанию — язык интерфейса."""
    appid = _appid(appid)
    steam = _steam_dir(steam_path)
    if appid is None or steam is None:
        return None
    if language is None:
        language = i18n.steam_language()
    language = language.strip() if isinstance(language, str) and language.strip() else "english"
    schema = _read_schema(_schema_path(steam, appid), appid, language)
    if not schema or not schema[0]:
        return None
    entries, stats = schema
    account = _account_id(account) or find_active_account(steam)

    updated: list[float | None] = []  # mtime источников прогресса, которые реально что-то сказали
    stats_known = library_known = False
    if account is not None:
        stats_path = _stats_path(steam, account, appid)
        root = _load_binary(stats_path)
        cache = root.get("cache") if root else None
        # Нет файла (или он недописан) — прогресс неизвестен, это не «0 получено»;
        # пустой файл (только crc) — действительно ничего не получено
        if isinstance(cache, dict):
            stats_known = True
            _apply_stats_file(entries, stats, cache)
            updated.append(_mtime(stats_path))

        library_path = _librarycache_path(steam, account, appid)
        library = _read_library(library_path, appid)
        if library is not None:
            _apply_library(entries, library, stats_known)
            if library.has_progress:
                library_known = True
                updated.append(_mtime(library_path))

    times = [mtime for mtime in updated if mtime is not None]
    return AchievementSet(
        appid=appid,
        items=[entry.achievement for entry in entries],
        source=SOURCE,
        updated_at=max(times) if times else None,
        progress_known=stats_known or library_known,
    )
