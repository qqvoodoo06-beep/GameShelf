"""Поиск игр, установленных через Steam, Epic Games и GOG, и импорт времени из Steam.

Всё только читается: файлы лаунчеров и реестр. На странных или отсутствующих данных
функции возвращают пустой результат, а не бросают исключения.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import winreg


@dataclass
class InstalledGame:
    source: str         # "steam" | "epic" | "gog"
    source_id: str      # Steam appid, Epic AppName, GOG gameID
    name: str
    install_dir: str    # существующая папка
    exe_path: str = ""  # главный exe, если лаунчер его записывает (у Steam — нет)


# ---------------------------------------------------------------- VDF (Valve KeyValues)

_VDF_TOKEN = re.compile(r'''
    \s+                         # пробелы
  | //[^\n]*                    # комментарий до конца строки
  | "((?:[^"\\]|\\.)*)"?        # строка в кавычках (незакрытая — до конца текста)
  | ([{}])                      # скобки блока
  | \[[^\]\n]*\]                # условия вроде [$WIN32] — пропускаем
  | ([^\s{}"]+)                 # токен без кавычек
''', re.VERBOSE | re.DOTALL)

_VDF_ESCAPE = re.compile(r'\\([\\"])')


def _vdf_tokens(text: str) -> Iterator[tuple[bool, str]]:
    """(это скобка?, текст): строка "{" в кавычках — ключ, а не начало блока."""
    for match in _VDF_TOKEN.finditer(text):
        quoted, brace, bare = match.groups()
        if brace:
            yield True, brace
        elif bare is not None:
            yield False, bare
        elif quoted is not None:
            # Только \\ и \": неэкранированные пути вида C:\new не должны ломаться
            yield False, _VDF_ESCAPE.sub(r"\1", quoted) if "\\" in quoted else quoted


def _vdf_put(node: dict, key: str, value: str | dict) -> str | dict:
    """Повторяющиеся блоки сливаются, повторяющиеся значения — побеждает последнее."""
    existing = node.get(key)
    if isinstance(value, dict) and isinstance(existing, dict):
        return existing
    node[key] = value
    return value


def parse_vdf(text: str) -> dict:
    """Текстовый KeyValues от Valve (.vdf, .acf) → вложенные dict со строками."""
    root: dict = {}
    stack: list[dict] = [root]
    key: str | None = None
    for is_brace, token in _vdf_tokens(text if isinstance(text, str) else ""):
        if is_brace and token == "{":
            # Блок без имени сливаем с родителем: его закрывающая скобка вернёт нас обратно
            stack.append(stack[-1] if key is None else _vdf_put(stack[-1], key, {}))
            key = None
        elif is_brace:
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = token
        else:
            _vdf_put(stack[-1], key, token)
            key = None
    return root


def vdf_get(data: dict, *keys: str, default=None):
    """Вложенный поиск без учёта регистра: в разных установках Steam пишет "Apps" и "apps"."""
    node = data
    for key in keys:
        if not isinstance(node, dict) or not isinstance(key, str):
            return default
        if key in node:
            node = node[key]
            continue
        folded = key.casefold()
        for name, value in node.items():
            if isinstance(name, str) and name.casefold() == folded:
                node = value
                break
        else:
            return default
    return node


# ---------------------------------------------------------------- общие помощники

def _read_text(path: Path, encoding: str = "utf-8") -> str:
    try:
        return path.read_text(encoding=encoding, errors="replace")
    except (OSError, ValueError):  # ValueError — нулевой символ в пути
        return ""


def _is_number(text: str) -> bool:
    """Только цифры 0-9: str.isdigit() пропускает и «²», на котором int() падает."""
    return text.isascii() and text.isdigit()


def _is_dir(path: str) -> bool:
    try:
        return bool(path) and os.path.isdir(path)
    except (OSError, ValueError):
        return False


def _existing_file(path: str) -> str:
    """Путь к exe, только если файл есть: устаревшая запись хуже, чем никакой."""
    try:
        return os.path.normpath(path) if path and os.path.isfile(path) else ""
    except (OSError, ValueError):
        return ""


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _reg_value(root, key_path: str, name: str) -> str:
    """Строковое значение из реестра или "" (на других ОС — тоже "")."""
    if not _IS_WINDOWS:
        return ""
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _reg_subkeys(root, key_path: str) -> Iterator[tuple[str, dict[str, object]]]:
    """Подключи с их значениями: (имя подключа, {имя значения: данные})."""
    if not _IS_WINDOWS:
        return
    try:
        parent = winreg.OpenKey(root, key_path, 0, winreg.KEY_READ)
    except OSError:
        return
    with parent:
        index = 0
        while True:
            try:
                sub_name = winreg.EnumKey(parent, index)
            except OSError:
                break
            index += 1
            values: dict[str, object] = {}
            try:
                with winreg.OpenKey(parent, sub_name, 0, winreg.KEY_READ) as sub:
                    value_index = 0
                    while True:
                        try:
                            name, data, _kind = winreg.EnumValue(sub, value_index)
                        except OSError:
                            break
                        values[name] = data
                        value_index += 1
            except OSError:
                continue
            yield sub_name, values


def _reg_str(values: dict[str, object], name: str) -> str:
    value = values.get(name)
    if isinstance(value, int):
        return str(value)
    return value.strip() if isinstance(value, str) else ""


# ---------------------------------------------------------------- Steam

# Не игры: Steamworks Common Redistributables
_STEAM_SKIP_APPIDS = frozenset({"228980"})


def find_steam_path() -> Path | None:
    if not _IS_WINDOWS:
        return None
    candidates = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for root, key_path, name in candidates:
        value = _reg_value(root, key_path, name)
        if _is_dir(value):
            return Path(os.path.normpath(value))
    return None


def _steam_libraries(steam_path: Path) -> list[Path]:
    """Все библиотеки из libraryfolders.vdf (новый и старый формат) плюс сама папка Steam."""
    found: list[Path] = []
    for vdf_path in (steam_path / "steamapps" / "libraryfolders.vdf",
                     steam_path / "config" / "libraryfolders.vdf"):
        folders = vdf_get(parse_vdf(_read_text(vdf_path)), "libraryfolders", default={})
        if not isinstance(folders, dict):
            continue
        for key, value in folders.items():
            if isinstance(value, dict):
                path = vdf_get(value, "path", default="")
            else:
                # Старый формат: "1" "D:\\SteamLibrary"
                path = value if key.isdigit() else ""
            if isinstance(path, str) and path:
                found.append(Path(path))
    # Папку Steam добавляем последней: в реестре она бывает в нижнем регистре, а в vdf — как на диске
    found.append(steam_path)

    libraries: list[Path] = []
    seen: set[str] = set()
    for library in found:
        key = _path_key(library)
        if key not in seen and _is_dir(str(library)):
            seen.add(key)
            libraries.append(library)
    return libraries


def _read_appmanifest(manifest: Path, steamapps: Path) -> InstalledGame | None:
    state = vdf_get(parse_vdf(_read_text(manifest)), "AppState")
    if not isinstance(state, dict):
        return None
    appid = vdf_get(state, "appid", default="")
    install = vdf_get(state, "installdir", default="")
    if not isinstance(appid, str) or not _is_number(appid) or appid in _STEAM_SKIP_APPIDS:
        return None
    if not isinstance(install, str) or not install.strip():
        return None
    install_dir = os.path.normpath(str(steamapps / "common" / install))
    if not _is_dir(install_dir):
        return None
    name = vdf_get(state, "name", default="")
    return InstalledGame("steam", appid, name if isinstance(name, str) and name else appid, install_dir)


def scan_steam(steam_path: Path | None = None) -> list[InstalledGame]:
    steam_path = steam_path or find_steam_path()
    if steam_path is None:
        return []
    games: list[InstalledGame] = []
    seen: set[str] = set()
    for library in _steam_libraries(Path(steam_path)):
        steamapps = library / "steamapps"
        try:
            manifests = sorted(steamapps.glob("appmanifest_*.acf"))
        except (OSError, ValueError):
            continue
        for manifest in manifests:
            game = _read_appmanifest(manifest, steamapps)
            if game and game.source_id not in seen:
                seen.add(game.source_id)
                games.append(game)
    return games


def _localconfig_path(steam_path: Path) -> Path | None:
    """localconfig.vdf аккаунта, который заходил последним (по времени изменения файла)."""
    best: tuple[float, Path] | None = None
    try:
        accounts = list((steam_path / "userdata").iterdir())
    except (OSError, ValueError):
        return None
    for account in accounts:
        # "0" и "anonymous" — служебные папки, не аккаунты
        if not _is_number(account.name) or account.name == "0":
            continue
        config = account / "config" / "localconfig.vdf"
        try:
            mtime = config.stat().st_mtime
        except (OSError, ValueError):
            continue
        if best is None or mtime > best[0]:
            best = (mtime, config)
    return best[1] if best else None


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# 01.01.3000: дальше datetime.fromtimestamp() на Windows падает
_MAX_UNIX_TIME = 32_503_680_000


def _unix_time(value) -> int:
    """Время последнего запуска или 0, если оно отрицательное или из далёкого будущего."""
    stamp = _to_int(value)
    return stamp if 0 < stamp < _MAX_UNIX_TIME else 0


def steam_playtimes(steam_path: Path | None = None) -> dict[int, tuple[float, int]]:
    """appid → (часы, время последнего запуска в unix). Игры без наигранного времени пропускаются."""
    steam_path = steam_path or find_steam_path()
    if steam_path is None:
        return {}
    config = _localconfig_path(Path(steam_path))
    if config is None:
        return {}
    data = parse_vdf(_read_text(config))
    apps = vdf_get(data, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", default={})
    if not isinstance(apps, dict):
        return {}

    result: dict[int, tuple[float, int]] = {}
    for appid, info in apps.items():
        if not _is_number(appid) or not isinstance(info, dict):
            continue
        minutes = _to_int(vdf_get(info, "Playtime"))
        if minutes <= 0:
            continue
        result[int(appid)] = (round(minutes / 60, 2), _unix_time(vdf_get(info, "LastPlayed")))
    return result


# ---------------------------------------------------------------- Epic Games

def _epic_manifests_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"


def _is_epic_game(item: dict) -> bool:
    """Отсекает движок, плагины и DLC: у игр в AppCategories есть "games"."""
    categories = item.get("AppCategories")
    if isinstance(categories, list) and categories and "games" not in categories:
        return False
    main_app = item.get("MainGameAppName")
    return not (isinstance(main_app, str) and main_app and main_app != item.get("AppName"))


def _read_epic_item(path: Path) -> InstalledGame | None:
    try:
        item = json.loads(_read_text(path, encoding="utf-8-sig"))
    except (ValueError, RecursionError):  # RecursionError — битый файл с огромной вложенностью
        return None
    if not isinstance(item, dict) or item.get("bIsIncompleteInstall") or not _is_epic_game(item):
        return None
    app_name = item.get("AppName")
    location = item.get("InstallLocation")
    if not isinstance(app_name, str) or not app_name or not isinstance(location, str):
        return None
    if not _is_dir(location):
        return None
    location = os.path.normpath(location)
    executable = item.get("LaunchExecutable")
    exe_path = _existing_file(os.path.join(location, executable)) if isinstance(executable, str) and executable else ""
    name = item.get("DisplayName")
    return InstalledGame("epic", app_name, name if isinstance(name, str) and name else app_name, location, exe_path)


def scan_epic(manifests_dir: Path | None = None) -> list[InstalledGame]:
    manifests_dir = Path(manifests_dir) if manifests_dir else _epic_manifests_dir()
    try:
        items = sorted(manifests_dir.glob("*.item"))
    except (OSError, ValueError):
        return []
    games: list[InstalledGame] = []
    seen: set[str] = set()
    for path in items:
        game = _read_epic_item(path)
        if game and game.source_id not in seen:
            seen.add(game.source_id)
            games.append(game)
    return games


# ---------------------------------------------------------------- GOG

_GOG_KEYS = (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games")


def scan_gog() -> list[InstalledGame]:
    if not _IS_WINDOWS:
        return []
    games: list[InstalledGame] = []
    seen: set[str] = set()
    for key_path in _GOG_KEYS:
        for sub_name, values in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, key_path):
            # DLC записаны отдельными ключами с той же папкой и dependsOn = id основной игры
            if _reg_str(values, "dependsOn"):
                continue
            game_id = _reg_str(values, "gameID") or sub_name
            path = _reg_str(values, "path")
            if game_id in seen or not _is_dir(path):
                continue
            seen.add(game_id)
            name = _reg_str(values, "gameName") or game_id
            games.append(InstalledGame("gog", game_id, name, os.path.normpath(path),
                                       _existing_file(_reg_str(values, "exe"))))
    return games


# ---------------------------------------------------------------- все вместе

def scan_all() -> list[InstalledGame]:
    """Все лаунчеры; сбой одного не мешает остальным."""
    games: list[InstalledGame] = []
    for scanner in (scan_steam, scan_epic, scan_gog):
        try:
            games.extend(scanner())
        except Exception:  # noqa: BLE001 — чужие файлы бывают какими угодно
            continue
    return games


_TRADEMARKS = str.maketrans("", "", "™®©")


def normalize_name(name: str) -> str:
    """Имя для сравнения между магазинами: «DARK SOULS™ III» и «Dark Souls III» → "darksoulsiii".

    Разделители выбрасываются целиком, чтобы «Baldur's Gate 3» совпало с «Baldurs Gate 3»;
    диакритика тоже (é → e, ё → е, й → и — одинаково с обеих сторон).
    """
    if not isinstance(name, str):
        return ""
    # ™ убираем до NFKD, иначе он превратится в буквы "TM"
    decomposed = unicodedata.normalize("NFKD", name.translate(_TRADEMARKS))
    return "".join(ch for ch in decomposed.casefold()
                   if ch.isalnum() and not unicodedata.combining(ch))


_SOURCE_PRIORITY = {"steam": 0, "epic": 1, "gog": 2}


def match_installed(game, installed: list[InstalledGame]) -> InstalledGame | None:
    """Установленная копия игры из библиотеки: сначала по appid Steam, потом по точному имени."""
    installed = installed or []
    if getattr(game, "source", "") == "steam":
        appid = str(getattr(game, "id", ""))
        for item in installed:
            if item.source == "steam" and item.source_id == appid:
                return item

    wanted = normalize_name(getattr(game, "name", ""))
    if not wanted:
        return None
    matches = [item for item in installed if normalize_name(item.name) == wanted]
    if not matches:
        return None
    return min(matches, key=lambda item: _SOURCE_PRIORITY.get(item.source, len(_SOURCE_PRIORITY)))
