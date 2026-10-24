"""Достижения репаков: что записали эмуляторы Steam (кряки) на этом компьютере.

Эмуляторы пишут файл сразу при разблокировке (проверено по времени изменения у RUNE, CODEX и GSE),
поэтому за файлами можно следить через QFileSystemWatcher. Форматы RUNE, CODEX, OnlineFix, GSE,
CreamAPI и RLD! проверены на реальных файлах. SKIDROW, ALI213, Hoodlum/DARKSiDERS, 3DM, SmartSteamEmu
и EMPRESS взяты из кода Achievement Watcher: на живых данных их проверить было не на чем.

Всё только читается. На отсутствующих и странных данных функции возвращают None или пустой
результат, а не бросают исключения.
"""
from __future__ import annotations

import json
import os
import re
import sys
import zlib
from collections import deque
from dataclasses import dataclass, field

_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import winreg


@dataclass
class EmulatorRecord:
    unlocked: bool
    unlocked_at: int | None = None  # unix-время получения
    approx: bool = False            # кряк хранил unix // 1000: на самом деле получено до ~17 минут позже


@dataclass
class EmulatorProgress:
    appid: int
    family: str                      # "RUNE", "CODEX", "OnlineFix"…; несколько — через " + "
    paths: list[str] = field(default_factory=list)
    updated_at: float | None = None  # самое новое время изменения среди файлов
    records: dict[str, EmulatorRecord] = field(default_factory=dict)  # ключ — API-имя в нижнем регистре


def crc_key(api: str) -> str:
    """Ключ записи SmartSteamEmu: этот эмулятор хранит не имя достижения, а CRC32 от него.

    Сопоставлять так: records.get(api.lower()) or records.get(crc_key(api)), где api — имя из схемы Steam
    в исходном регистре. Не строка — "": такой ключ ни с чем не совпадёт.
    """
    if not isinstance(api, str):
        return ""
    # surrogatepass: имя из чужого JSON может содержать одиночный суррогат, и encode() упал бы
    return f"crc32:{zlib.crc32(api.encode('utf-8', 'surrogatepass')) & 0xFFFFFFFF:08x}"


# ---------------------------------------------------------------- общие помощники

# Файлы достижений — килобайты; больше не читаем, чтобы чужой мусор не съел память
_MAX_FILE_SIZE = 8 * 1024 * 1024
_MAX_CONFIG_SIZE = 64 * 1024

# 01.01.3000: дальше datetime.fromtimestamp() на Windows падает
_MAX_UNIX_TIME = 32_503_680_000


def _is_dir(path: str) -> bool:
    try:
        return bool(path) and os.path.isdir(path)
    except (OSError, ValueError):  # ValueError — нулевой символ в пути
        return False


def _is_file(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path)
    except (OSError, ValueError):
        return False


def _mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except (OSError, ValueError):
        return None


def _listdir(path: str) -> list[str]:
    try:
        return os.listdir(path) if path else []
    except (OSError, ValueError):
        return []


def _join(base: str, *parts: str) -> str:
    """Путь от переменной окружения, которой может не быть: тогда "" и место пропускается."""
    return os.path.join(base, *parts) if base else ""


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _inside(path_key: str, root_key: str) -> bool:
    return path_key == root_key or path_key.startswith(root_key.rstrip("\\/") + os.sep)


def _read_bytes(path: str, limit: int) -> bytes | None:
    try:
        with open(path, "rb") as file:
            return file.read(limit)
    except (OSError, ValueError):
        return None


def _decode(data: bytes) -> str:
    """Кряки пишут и в UTF-16 с BOM, и в UTF-8 с BOM (OnlineFix 2024 года), и в ASCII.

    UTF-16 без BOM узнаём по нулям через байт: в тексте INI/JSON нулевых байтов не бывает, а прочитанный
    как UTF-8 такой файл дал бы пустой набор, то есть ложное «ничего не получено». Нули, которыми
    некоторые программы добивают файл до размера, выбрасываем.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", "replace")
    elif len(data) >= 4 and data[0] and data[2] and not data[1] and not data[3]:
        text = data.decode("utf-16-le", "replace")
    elif len(data) >= 4 and data[1] and data[3] and not data[0] and not data[2]:
        text = data.decode("utf-16-be", "replace")
    else:
        text = data.decode("utf-8-sig", "replace")
    return text.replace("\x00", "")


def _decode_path(data: bytes) -> str:
    """Путь в файле кряка: не UTF-8 — значит ANSI (cp1251 для кириллических папок)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("mbcs" if _IS_WINDOWS else "latin-1", "replace")


def _parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Терпимый INI: «k = v», ключи в нижнем регистре, повторные секции сливаются.

    Имена секций остаются как есть: у CODEX и RUNE это API-имена достижений.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ";#":
            continue
        if line[0] == "[" and line[-1] == "]":
            current = sections.setdefault(line[1:-1].strip(), {})
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current[key.strip().lower()] = value.strip()
    return sections


def _to_int(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if not isinstance(value, float):
        text = str(value).strip()
        try:
            return int(text)
        except ValueError:
            pass
        try:
            value = float(text)  # "1700000000.0" — кто-то записал время числом с точкой
        except ValueError:
            return 0
    # NaN и бесконечность не проходят сравнение
    return int(value) if abs(value) < 1e18 else 0


_TRUE_WORDS = frozenset({"1", "true"})


def _is_true(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in _TRUE_WORDS


def _unlock_time(raw: int) -> tuple[int | None, bool]:
    """(unix-время, приблизительное ли). Ноль и мусор — время неизвестно."""
    if 1_000_000_000 <= raw < _MAX_UNIX_TIME:
        return raw, False
    # 7 цифр — unix // 1000 (CreamAPI, OnlineFix 2024 года): сходится со временем создания и изменения файлов
    if 1_000_000 <= raw < 10_000_000:
        return raw * 1000, True
    # Миллисекунды на машине не встречались, но с секундами их не спутать
    if 1_000_000_000_000 <= raw < _MAX_UNIX_TIME * 1000:
        return raw // 1000, False
    return None, False


def _rld_u32(value: str) -> int | None:
    """RLD!: 5 байт в hex — uint32 little-endian и контрольный байт (сумма первых четырёх & 0xFF).

    Сверка суммы отсекает похожие, но чужие значения: на машине сошлось 48 из 48.
    """
    try:
        data = bytes.fromhex(value.strip())
    except ValueError:
        return None
    if len(data) != 5 or sum(data[:4]) & 0xFF != data[4]:
        return None
    return int.from_bytes(data[:4], "little")


def _hex_u32(value: str) -> int:
    """3DM: время — uint32 little-endian в hex, без контрольного байта."""
    try:
        data = bytes.fromhex(value.strip())
    except ValueError:
        return 0
    return int.from_bytes(data[:4], "little") if len(data) >= 4 else 0


# ---------------------------------------------------------------- разбор файлов достижений

# (API-имя, получено, время как записано в файле)
_RawRecord = tuple[str, bool, int]

# Индекс достижений у CODEX/RUNE ([SteamAchievements] 00000=…) и RLD! ([Steam] ACH001=…)
_INDEX_SECTIONS = frozenset({"steamachievements", "steam", "steam64"})
_FLAG_KEYS = ("achieved", "haveachieved", "unlocked", "earned")
_TIME_KEYS = ("unlocktime", "timestamp", "timeunlocked", "haveachievedtime", "havehaveachievedtime")


def _has_flag(values: dict[str, str]) -> bool:
    return "state" in values or any(key in values for key in _FLAG_KEYS)


def _ini_records(text: str) -> list[_RawRecord]:
    """CODEX, RUNE, OnlineFix (оба формата), CreamAPI, RLD!, ALI213, Hoodlum, 3DM, SKIDROW."""
    sections = _parse_ini(text)
    folded = {name.lower(): values for name, values in sections.items()}

    # Hoodlum / DARKSiDERS: [Achievements] api=1 и [AchievementsUnlockTimes] api=unix
    plain = folded.get("achievements")
    if plain is not None and not _has_flag(plain):
        times = folded.get("achievementsunlocktimes", {})
        return [(api, _is_true(flag), _to_int(times.get(api))) for api, flag in plain.items()]
    # 3DM: [State] api=0101 и [Time] api=<hex>
    states = folded.get("state")
    if states is not None and "time" in folded and not _has_flag(states):
        times = folded["time"]
        return [(api, state.lower() == "0101", _hex_u32(times.get(api, ""))) for api, state in states.items()]
    # SKIDROW achieve.dat: [ACHIEVE_DATA] api=1, времени нет
    plain = folded.get("achieve_data")
    if plain is not None and not _has_flag(plain):
        return [(api, _is_true(flag), 0) for api, flag in plain.items()]

    records: list[_RawRecord] = []
    for name, values in sections.items():
        # Секция "[]" встречается у RUNE с Achieved=1, но в индексе ей соответствует пустая строка
        if not name or name.lower() in _INDEX_SECTIONS:
            continue
        if "state" in values:  # RLD!
            state = _rld_u32(values["state"])
            if state is not None:
                records.append((name, state == 1, _rld_u32(values.get("time", "")) or 0))
            continue
        flag = next((values[key] for key in _FLAG_KEYS if key in values), None)
        if flag is None:
            continue  # не достижение: [UserStats], настройки и т. п.
        # Только флаг: у RUNE бывает Achieved=1 при прогрессе 5/18, так что CurProgress/MaxProgress не смотрим
        raw_time = next((_to_int(values[key]) for key in _TIME_KEYS if values.get(key)), 0)
        records.append((name, _is_true(flag), raw_time))
    return records


def _json_records(text: str) -> list[_RawRecord] | None:
    """GSE, Goldberg, EMPRESS: {"API": {"earned": true, "earned_time": unix}}."""
    try:
        data = json.loads(text) if text.strip() else None
    except (ValueError, RecursionError):  # RecursionError — битый файл с огромной вложенностью
        return None
    if data is None:  # пустой файл или null: файл есть, а получено пока ничего
        return []
    if isinstance(data, list):
        # Список объектов на машине не встречался; имя берём изнутри, как Achievement Watcher
        entries = [(_json_name(value), value) for value in data if isinstance(value, dict)]
    elif isinstance(data, dict):
        entries = list(data.items())
    else:
        return None
    return [(api, _is_true(value.get("earned")), _to_int(value.get("earned_time")))
            for api, value in entries if isinstance(api, str) and isinstance(value, dict)]


def _json_name(value: dict) -> str:
    for key in ("id", "apiname", "name"):
        name = value.get(key)
        if isinstance(name, str) and name.strip():
            return name
    return ""


_SSE_ENTRY = 24


def _sse_records(data: bytes) -> list[_RawRecord] | None:
    """SmartSteamEmu stats.bin: uint32 — число записей, дальше записи по 24 байта.

    В записи [0:4] — CRC32 API-имени, [8:12] — время, [20] == 1 — получено. Единица бывает и у статов:
    такие записи просто не совпадут ни с одним crc_key() из схемы.
    """
    if not data:
        return []  # пустой файл: как и у остальных форматов, «пока ничего не получено»
    if len(data) < 4:
        return None
    body = data[4:]
    if len(body) != int.from_bytes(data[:4], "little") * _SSE_ENTRY:
        return None
    records: list[_RawRecord] = []
    for offset in range(0, len(body), _SSE_ENTRY):
        entry = body[offset:offset + _SSE_ENTRY]
        if entry[20] == 1:
            crc = int.from_bytes(entry[0:4], "little")
            records.append((f"crc32:{crc:08x}", True, int.from_bytes(entry[8:12], "little")))
    return records


def _parse_file(path: str) -> list[_RawRecord] | None:
    """None — файла нет или он битый: такой файл считаем отсутствующим, а не «ничего не получено»."""
    data = _read_bytes(path, _MAX_FILE_SIZE)
    if data is None:
        return None
    name = os.path.basename(path).lower()
    if name == "stats.bin":
        return _sse_records(data)
    text = _decode(data)
    if name.endswith(".json"):
        return _json_records(text)
    # Achievements.Bin у ALI213, несмотря на расширение, — текстовый INI
    return _ini_records(text)


def _is_better(new: EmulatorRecord, old: EmulatorRecord | None) -> bool:
    """Полученное важнее неполученного, из двух полученных — с более ранним известным временем."""
    if old is None:
        return True
    if new.unlocked != old.unlocked:
        return new.unlocked
    if not new.unlocked or new.unlocked_at is None:
        return False
    return old.unlocked_at is None or new.unlocked_at < old.unlocked_at


def _put(records: dict[str, EmulatorRecord], key: str, record: EmulatorRecord) -> None:
    if _is_better(record, records.get(key)):
        records[key] = record


def _file_records(raw: list[_RawRecord]) -> dict[str, EmulatorRecord]:
    records: dict[str, EmulatorRecord] = {}
    for api, unlocked, raw_time in raw:
        api = api.strip()
        if not api:
            continue
        when, approx = _unlock_time(raw_time) if unlocked else (None, False)
        # Регистр имён у кряка и в схеме Steam иногда расходится (Kill_25_5 и kill_25_5)
        _put(records, api.lower(), EmulatorRecord(unlocked, when, approx))
    return records


# ---------------------------------------------------------------- где эмуляторы хранят данные

_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer"


def _documents_dir() -> str:
    """«Документы» пользователя: их часто переносят в OneDrive, поэтому путь берём из реестра."""
    if _IS_WINDOWS:
        for sub_key in ("User Shell Folders", "Shell Folders"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SHELL_FOLDERS + "\\" + sub_key) as key:
                    value, _kind = winreg.QueryValueEx(key, "Personal")
            except OSError:
                continue
            if isinstance(value, str) and value.strip():
                path = os.path.expandvars(value.strip())
                if _is_dir(path):
                    return os.path.normpath(path)
    path = os.path.join(os.path.expanduser("~"), "Documents")
    return path if _is_dir(path) else ""


# Папка в «Документах» у Hoodlum-подобных эмуляторов — UserName из ini, по умолчанию имя группы
_SCENE_NAMES = ("HOODLUM", "DARKSiDERS", "SKIDROW")


def _candidates(appid: int) -> list[tuple[str, str, str]]:
    """(семейство, путь к файлу достижений, корень эмулятора) для всех мест, где кряки хранят appid.

    Корень нужен watch_paths: выше него папки не смотрим, иначе будем ловить чужие изменения.
    """
    aid = str(appid)
    public = os.environ.get("PUBLIC", "")
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    program_data = os.environ.get("PROGRAMDATA", "")
    documents = _documents_dir()
    found: list[tuple[str, str, str]] = []

    def add(family: str, root: str, *parts: str) -> None:
        if root:
            found.append((family, os.path.join(root, aid, *parts), root))

    # Одна раскладка на несколько групп: имя папки и есть имя кряка (RUNE, CODEX, RLD!…)
    for base, parts, usual in ((_join(public, "Documents", "Steam"), ("achievements.ini",), ("CODEX", "RUNE")),
                               (_join(appdata, "Steam"), ("achievements.ini",), ("CODEX",)),
                               (_join(program_data, "Steam"), ("stats", "achievements.ini"), ("RLD!",))):
        groups = sorted(_listdir(base))
        for group in groups:
            add(group, os.path.join(base, group), *parts)
        # Папки самой группы ещё нет (первая игра с этим кряком): для watch_paths корнем будет base,
        # чтобы заметить, когда она появится
        present = {group.lower() for group in groups}
        for group in usual:
            if base and group.lower() not in present:
                found.append((group, os.path.join(base, group, aid, *parts), base))

    public_docs = _join(public, "Documents")
    add("OnlineFix", _join(public_docs, "OnlineFix"), "Stats", "Achievements.ini")
    add("OnlineFix", _join(public_docs, "OnlineFix"), "Achievements.ini")  # формат 2024 года
    add("GSE", _join(appdata, "GSE Saves"), "achievements.json")
    add("Goldberg", _join(appdata, "Goldberg SteamEmu Saves"), "achievements.json")
    # Самосборные Goldberg писали ini — Achievement Watcher читает и его
    add("Goldberg", _join(appdata, "Goldberg SteamEmu Saves"), "achievements.ini")
    add("CreamAPI", _join(appdata, "CreamAPI"), "stats", "CreamAPI.Achievements.cfg")
    # Здесь EMPRESS живёт в Public\Documents, а Achievement Watcher ищет в Public и APPDATA
    for root in (_join(public_docs, "EMPRESS"), _join(public, "EMPRESS"), _join(appdata, "EMPRESS")):
        add("EMPRESS", root, "remote", aid, "achievements.json")
    add("SKIDROW", _join(local, "SKIDROW"), "achieve.dat")
    add("SKIDROW", _join(documents, "SKIDROW"), "achieve.dat")
    add("SmartSteamEmu", _join(appdata, "SmartSteamEmu"), "stats.bin")

    # ALI213 с SaveType=1: <Документы>\VALVE\<appid>\<игрок>\Stats\Achievements.Bin
    valve = _join(documents, "VALVE")
    for player in sorted(_listdir(_join(valve, aid))):
        found.append(("ALI213", os.path.join(valve, aid, player, "Stats", "Achievements.Bin"), valve))
    # Hoodlum, DARKSiDERS и SKIDROW после 2019 г. с UserDataFolder=mydocs: <Документы>\<UserName>\<appid>\SteamEmu.
    # Смотрим только имена по умолчанию: проверять каждую папку «Документов» — лишние 5 мс на игру
    for user_dir in (_join(documents, name) for name in _SCENE_NAMES):
        emu_dir = _join(user_dir, aid, "SteamEmu")
        if _is_dir(emu_dir):
            family = os.path.basename(user_dir)
            found.append((family, os.path.join(emu_dir, "UserStats", "achiev.ini"), user_dir))
            found.append((family, os.path.join(emu_dir, "stats.ini"), user_dir))
    return found


def _valid_appid(appid) -> int:
    try:
        value = int(appid)
    except (TypeError, ValueError, OverflowError):
        return 0
    return value if 0 < value < 2 ** 32 else 0


# ---------------------------------------------------------------- прогресс

def read_progress(appid: int) -> EmulatorProgress | None:
    """Всё, что эмуляторы записали для appid, одним набором.

    None — ни одного файла нет. Файл есть, а записей нет — пустой records: «пока ничего не получено».
    """
    try:
        return _read_progress(_valid_appid(appid))
    except Exception:  # noqa: BLE001 — чужие файлы бывают какими угодно, а вызывающему нужен ответ
        return None


def _read_progress(appid: int) -> EmulatorProgress | None:
    if not appid:
        return None
    sources: list[tuple[str, str, float | None, dict[str, EmulatorRecord]]] = []
    seen: set[str] = set()
    for family, path, _root in _candidates(appid):
        key = _path_key(path)
        if key in seen or not _is_file(path):
            continue
        seen.add(key)
        raw = _parse_file(path)
        if raw is not None:
            sources.append((family, path, _mtime(path), _file_records(raw)))
    if not sources:
        return None

    # Главный источник — первый в подписи: с полученными достижениями, потом с записями, потом свежий
    sources.sort(key=lambda source: (not any(r.unlocked for r in source[3].values()),
                                     not source[3], -(source[2] or 0.0)))
    families: list[str] = []
    records: dict[str, EmulatorRecord] = {}
    for family, _path, _time, file_records in sources:
        if family not in families:
            families.append(family)
        for key, record in file_records.items():
            _put(records, key, record)
    times = [source[2] for source in sources if source[2] is not None]
    return EmulatorProgress(appid=appid, family=" + ".join(families),
                            paths=[source[1] for source in sources],
                            updated_at=max(times) if times else None, records=records)


def watch_paths(appid: int) -> list[str]:
    """Что поставить на QFileSystemWatcher: существующие файлы достижений и папки, где они появятся.

    Для каждого места берётся самая глубокая существующая папка на пути к файлу, но не выше корня
    эмулятора. Когда в ней что-то появится, список стоит запросить заново: он станет точнее.
    """
    try:
        appid = _valid_appid(appid)
        if not appid:
            return []
        files: list[str] = []
        folders: list[str] = []
        seen: set[str] = set()
        for _family, path, root in _candidates(appid):
            if _is_file(path) and _path_key(path) not in seen:
                seen.add(_path_key(path))
                files.append(path)
            folder = _deepest_existing(os.path.dirname(path), root)
            if folder and _path_key(folder) not in seen:
                seen.add(_path_key(folder))
                folders.append(folder)
        return files + folders
    except Exception:  # noqa: BLE001
        return []


def _deepest_existing(folder: str, root: str) -> str:
    root_key = _path_key(root)
    while True:
        if _is_dir(folder):
            return folder
        if _path_key(folder) == root_key:
            return ""
        parent = os.path.dirname(folder)
        if parent == folder:
            return ""
        folder = parent


# ---------------------------------------------------------------- appid по папке игры

# Конфиги эмуляторов: имя файла → (надёжность, [(секция, ключ)]); меньше — надёжнее
_APPID_CONFIGS: dict[str, tuple[int, tuple[tuple[str, str], ...]]] = {
    "steam_emu.ini": (0, (("settings", "appid"),)),         # RUNE, CODEX
    "onlinefix.ini": (1, (("main", "realappid"),)),         # FakeAppId=480 — чужой appid для сетевой игры
    "cream_api.ini": (3, (("config", "appid"),)),
    "steam_api.ini": (3, (("gamesettings", "appid"), ("settings", "appid"))),  # SKIDROW
    "hlm.ini": (3, (("gamesettings", "appid"),)),           # HOODLUM
    "ds.ini": (3, (("gamesettings", "appid"),)),            # DARKSiDERS
    "ali213.ini": (3, (("settings", "appid"),)),
    "valve.ini": (3, (("settings", "appid"),)),
    "steamconfig.ini": (3, (("settings", "appid"),)),
    "smartsteamemu.ini": (3, (("smartsteamemu", "appid"),)),
    "coldclientloader.ini": (3, (("steamclient", "appid"),)),
}
_APPID_TXT = "steam_appid.txt"
_RANK_GSE_APPID = 2   # steam_settings\steam_appid.txt — его читает сам GSE
_RANK_APPID_TXT = 4   # steam_appid.txt без steam_settings мог оставить и разработчик
_RANK_LAST_RUN = 5    # RLD!\<appid>\last.txt с путём к exe

# 480 — Spacewar, тестовый appid Valve: его ставят OnlineFix и сборки GSE вместо настоящего
_IGNORED_APPIDS = frozenset({0, 480})
# Копии тех же конфигов, оставленные репакером
_SKIP_DIRS = frozenset({"_crack", "_original files"})
_WALK_DEPTH = 7
_WALK_LIMIT = 20000
_BIN_DIRS = frozenset({"binaries", "bin", "bin32", "bin64", "win32", "win64", "x86", "x64", "x86_64"})
_LEADING_DIGITS = re.compile(r"\s*([0-9]+)")


def appid_candidates(exe_path: str = "", install_dir: str = "") -> list[int]:
    """Возможные appid игры по конфигам эмулятора в её папке; самые надёжные — первыми.

    Без папки игры корнем считается папка exe, а из ...\\Binaries\\Win64 поднимаемся выше:
    у игр на Unreal кряк лежит в Engine\\Binaries\\ThirdParty\\Steamworks.
    """
    try:
        return _appid_candidates(str(exe_path or ""), str(install_dir or ""))
    except Exception:  # noqa: BLE001
        return []


def _appid_candidates(exe_path: str, install_dir: str) -> list[int]:
    exe_path = os.path.normpath(exe_path) if exe_path else ""
    if _is_dir(exe_path):
        # Папка вместо exe: иначе корнем стал бы её родитель — целая библиотека с чужими играми
        install_dir, exe_path = install_dir or exe_path, ""
    roots: list[str] = []
    if _is_dir(install_dir):
        roots.append(os.path.normpath(install_dir))
    elif exe_path:
        roots.append(_game_root(exe_path))
    exe_dir = os.path.dirname(exe_path) if exe_path else ""
    if exe_dir and not any(_inside(_path_key(exe_dir), _path_key(root)) for root in roots):
        roots.append(exe_dir)
    roots = [root for root in roots if _is_dir(root)]

    found: list[tuple[int, int, int]] = []  # (надёжность, порядок находки, appid)
    for name, path in _find_configs(roots):
        text = _decode(_read_bytes(path, _MAX_CONFIG_SIZE) or b"")
        if name == _APPID_TXT:
            in_settings = os.path.basename(os.path.dirname(path)).lower() == "steam_settings"
            rank, values = (_RANK_GSE_APPID if in_settings else _RANK_APPID_TXT), [text]
        else:
            rank, keys = _APPID_CONFIGS[name]
            sections = {section.lower(): values for section, values in _parse_ini(text).items()}
            values = [sections.get(section, {}).get(key, "") for section, key in keys]
        for value in values:
            appid = _parse_appid(value)
            if appid:
                found.append((rank, len(found), appid))
    for appid in _last_run_appids(exe_path, roots):
        found.append((_RANK_LAST_RUN, len(found), appid))

    result: list[int] = []
    for _rank, _order, appid in sorted(found):
        if appid not in result:
            result.append(appid)
    return result


def _parse_appid(value: str) -> int:
    match = _LEADING_DIGITS.match(value)
    if not match:
        return 0
    appid = _valid_appid(match.group(1))
    return 0 if appid in _IGNORED_APPIDS else appid


def _game_root(exe_path: str) -> str:
    folder = os.path.dirname(exe_path)
    while os.path.basename(folder).lower() in _BIN_DIRS:
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    # Unreal 4/5: <корень>\<Проект>\Binaries\Win64, а рядом с <Проект> — Engine
    parent = os.path.dirname(folder)
    if parent != folder and _is_dir(os.path.join(parent, "Engine")):
        folder = parent
    return folder


def _find_configs(roots: list[str]) -> list[tuple[str, str]]:
    """(имя в нижнем регистре, путь) конфигов эмуляторов. Обход в ширину: мелкие уровни раньше,
    чтобы лимит на число файлов не съели глубокие папки с ресурсами."""
    budget = _WALK_LIMIT
    found: list[tuple[str, str]] = []
    for root in roots:
        # Корень диска целиком не обходим: exe, лежащий прямо в D:\, — не повод читать весь диск
        max_depth = 1 if os.path.dirname(root) == root else _WALK_DEPTH
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        while queue and budget > 0:
            folder, depth = queue.popleft()
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        budget -= 1
                        if budget <= 0:
                            break
                        name = entry.name.lower()
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                # Ссылки и точки соединения пропускаем: они могут вести по кругу
                                if depth < max_depth and name not in _SKIP_DIRS and not entry.is_junction():
                                    queue.append((entry.path, depth + 1))
                            elif name == _APPID_TXT or name in _APPID_CONFIGS:
                                found.append((name, entry.path))
                        except OSError:
                            continue
            except (OSError, ValueError):
                continue
    return found


def _last_run_appids(exe_path: str, roots: list[str]) -> list[int]:
    """RLD! записывает в %ProgramData%\\Steam\\RLD!\\<appid>\\last.txt путь к последнему запущенному exe."""
    exe_key = _path_key(exe_path) if exe_path else ""
    root_keys = [_path_key(root) for root in roots if os.path.dirname(root) != root]
    if not exe_key and not root_keys:
        return []
    base = _join(os.environ.get("PROGRAMDATA", ""), "Steam")
    found: list[int] = []
    for group in sorted(_listdir(base)):
        group_dir = os.path.join(base, group)
        for name in sorted(_listdir(group_dir)):
            appid = _valid_appid(name) if name.isascii() and name.isdigit() else 0
            data = _read_bytes(os.path.join(group_dir, name, "last.txt"), 4096) if appid else None
            if not data:
                continue
            lines = _decode_path(data).replace("\x00", "").strip().splitlines()
            if not lines:
                continue
            try:
                last_key = _path_key(lines[0].strip().strip('"'))
            except ValueError:
                continue
            if last_key == exe_key or any(_inside(last_key, root_key) for root_key in root_keys):
                found.append(appid)
    return found
