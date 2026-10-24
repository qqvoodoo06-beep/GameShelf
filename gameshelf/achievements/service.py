"""Сервис достижений: сводит Steam (диск + сеть) и файлы кряков, следит за изменениями и сообщает о новых."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from ..config import Settings
from ..database import Library, now_iso
from ..models import Game
from . import emulators, steam_local
from .meta import MetaAchievement, TrophyStats, evaluate
from .models import Achievement, AchievementSet
from .steam_web import SteamAchievementsWeb

RARE_PERCENT = 5.0
RELOAD_DELAY_MS = 1500        # файл часто пишут в несколько приёмов — перечитываем, когда запись закончилась
PLAYING_RELOAD_SECONDS = 30   # пока игра запущена, Steam обновляет прогресс в librarycache — заглядываем туда
BATCH = 3                     # игры читаем по несколько штук за проход, чтобы окно не подвисало
WEB_RETRY_SECONDS = 600       # не удалось получить список из Steam — пробуем снова не раньше чем через 10 минут
OLD_UNLOCK_MARGIN = 300       # достижение старше первого чтения игры (с запасом 5 мин) — не «новое»


def _install_signature(game: Game) -> tuple:
    return game.source, game.id, game.install_source, game.install_id, game.install_dir, game.exe_path


def _content_signature(achievement_set: AchievementSet | None) -> tuple:
    if achievement_set is None:
        return ()
    return (achievement_set.source, achievement_set.progress_known, achievement_set.complete,
            tuple((a.api, a.name, a.unlocked, a.unlocked_at, a.rarity, a.progress) for a in achievement_set.items))


def merge(appid: int, local: AchievementSet | None, emulator, web: list[Achievement] | None) -> AchievementSet | None:
    """Склеивает источники. Список и тексты — из локальной схемы Steam, а если её нет — из сети;
    редкость — из сети (она полнее); «получено» — из любого источника, время — самое раннее."""
    if local and local.items:
        items = [replace(a, origin="steam" if a.unlocked else "") for a in local.items]
    elif web:
        items = [replace(a) for a in web]
    else:
        items = []
    complete = bool(items)  # полный список даёт только схема Steam (с диска или из сети)
    index = {a.api.lower(): a for a in items}

    for remote in web or []:
        item = index.get(remote.api.lower())
        if item is None:
            continue
        if remote.rarity is not None:
            item.rarity = remote.rarity
        if not item.name or item.name == item.api:
            item.name, item.description = remote.name, remote.description
        item.icon = item.icon or remote.icon
        item.icon_gray = item.icon_gray or remote.icon_gray

    progress_known = bool(local and local.progress_known)
    sources = ["Steam"] if progress_known else []
    updated = local.updated_at if local else None

    if emulator is not None:
        progress_known = True  # файл кряка есть — даже пустой он значит «пока ничего не получено»
        sources.append(emulator.family)
        if emulator.updated_at and (updated is None or emulator.updated_at > updated):
            updated = emulator.updated_at
        by_crc = {emulators.crc_key(a.api): a for a in items} if any(
            api.startswith("crc32:") for api in emulator.records) else {}
        for api, record in emulator.records.items():
            item = index.get(api) or by_crc.get(api)
            if item is None:
                if api.startswith("crc32:"):
                    continue  # SmartSteamEmu хранит коды имён; не нашлось в схеме — значит, это стат, а не достижение
                item = Achievement(api=api, name=api)  # имени нет в схеме (её поменяли) — покажем, чтобы счёт был честным
                items.append(item)
                index[api] = item
            if record.unlocked:
                if not item.unlocked or (record.unlocked_at and (not item.unlocked_at
                                                                 or record.unlocked_at < item.unlocked_at)):
                    item.unlocked_at, item.time_approx = record.unlocked_at, record.approx
                item.unlocked = True
                item.origin = item.origin or "emulator"  # засчитал и Steam — значит, Steam покажет его сам
            elif item.unlocked is None:
                item.unlocked = False

    if not items:
        return None
    if progress_known and complete:
        for item in items:
            if item.unlocked is None:
                item.unlocked = False
    return AchievementSet(appid, items, " + ".join(sources), updated, progress_known, complete)


@dataclass
class _Baseline:
    """Что уже было получено к моменту, когда GameShelf впервые прочитал игру (в этом запуске)."""
    appid: int
    since: float                                   # unix-время первого чтения
    known: set[str] = field(default_factory=set)   # уже видели полученными — повторно не поздравляем


class AchievementService(QObject):
    changed = Signal(str)              # key — достижения игры изменились
    unlocked = Signal(str, object)     # key, Achievement — получено, пока GameShelf работал
    meta_unlocked = Signal(object)     # MetaAchievement — новое достижение GameShelf
    summary_changed = Signal()

    def __init__(self, library: Library, settings: Settings, web: SteamAchievementsWeb, parent=None):
        super().__init__(parent)
        self._library = library
        self._settings = settings
        self._web = web
        self._sets: dict[str, AchievementSet] = {}
        self._signatures: dict[str, tuple] = {}
        self._appids: dict[str, tuple[tuple, int | None]] = {}
        self._baselines: dict[str, _Baseline] = {}
        self._web_done: set[int] = set()
        self._web_failed: dict[int, float] = {}
        self._web_pending: set[int] = set()
        self._last_reload: dict[str, float] = {}
        self._watch_keys: dict[str, set[str]] = {}   # путь → игры, которым он важен
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_path_changed)
        self._watcher.directoryChanged.connect(self._on_path_changed)
        self._pending: set[str] = set()
        self._reload_timer = QTimer(self, singleShot=True, interval=RELOAD_DELAY_MS, timeout=self._reload_pending)
        self._meta_timer = QTimer(self, singleShot=True, interval=1000, timeout=self._update_meta)
        self._queue: list[str] = []
        self._started = False
        self._initial_done = False
        library.changed.connect(self._on_library_changed)
        library.playtime_changed.connect(lambda _key: self._schedule_meta())

    # ------------------------------------------------------------------ публичное

    def start(self) -> None:
        """Читает достижения всей библиотеки понемногу, не блокируя окно."""
        self._started = True
        self._enqueue([game.key for game in self._library.games()])

    def get(self, key: str) -> AchievementSet | None:
        return self._sets.get(key)

    def sets(self) -> dict[str, AchievementSet]:
        return dict(self._sets)

    def reload(self, key: str) -> AchievementSet | None:
        return self._load(key)

    def on_game_running(self, key: str) -> None:
        """Пока игра запущена — изредка перечитываем: Steam обновляет прогресс во время игры не в .bin-файле."""
        if time.monotonic() - self._last_reload.get(key, 0) >= PLAYING_RELOAD_SECONDS:
            self._load(key, fetch_web=False)

    def _unique_sets(self) -> list[AchievementSet]:
        """По одному набору на игру Steam: одна и та же игра бывает в библиотеке и из Steam, и из RAWG."""
        unique: dict[int, AchievementSet] = {}
        for achievement_set in self._sets.values():
            current = unique.get(achievement_set.appid)
            if current is None or achievement_set.unlocked_count > current.unlocked_count:
                unique[achievement_set.appid] = achievement_set
        return list(unique.values())

    def trophy_stats(self) -> TrophyStats:
        stats = TrophyStats()
        for achievement_set in self._unique_sets():
            if not achievement_set.progress_known:
                continue
            stats.unlocked += achievement_set.unlocked_count
            # «Платина» — только при полном списке: кряки пишут лишь полученные, и без схемы «5 из 5» — неправда
            if achievement_set.complete and achievement_set.total \
                    and achievement_set.unlocked_count == achievement_set.total:
                stats.perfect_games += 1
            stats.rare_unlocked += sum(1 for a in achievement_set.items
                                       if a.unlocked and a.rarity is not None and a.rarity < RARE_PERCENT)
        return stats

    def recent_unlocks(self, limit: int = 8) -> list[tuple[str, Achievement]]:
        found = [(key, a) for key, s in self._sets.items() for a in s.items if a.unlocked and a.unlocked_at]
        found.sort(key=lambda item: item[1].unlocked_at, reverse=True)
        return found[:limit]

    def rarest_unlocked(self) -> tuple[str, Achievement] | None:
        found = [(key, a) for key, s in self._sets.items() for a in s.items if a.unlocked and a.rarity is not None]
        return min(found, key=lambda item: item[1].rarity) if found else None

    def meta(self) -> list[MetaAchievement]:
        return evaluate(self._library.games(), self._library.sessions(), self.trophy_stats(),
                        self._library.meta_unlocked())

    # ------------------------------------------------------------------ загрузка

    def appid_for(self, game: Game) -> int | None:
        signature = _install_signature(game)
        cached = self._appids.get(game.key)
        if cached and cached[0] == signature:
            return cached[1]
        candidates: list[int] = []
        if game.source == "steam":
            candidates.append(game.id)
        if game.install_source == "steam" and game.install_id.isascii() and game.install_id.isdigit():
            candidates.append(int(game.install_id))
        if game.exe_path or game.install_dir:
            candidates += emulators.appid_candidates(game.exe_path, game.install_dir)
        candidates = list(dict.fromkeys(candidates))
        # Предпочитаем номер, по которому на диске реально есть данные (у RAWG-игры его даёт только кряк)
        chosen = next((appid for appid in candidates
                       if emulators.read_progress(appid) is not None or steam_local.load_steam_local(appid)), None)
        appid = chosen if chosen is not None else (candidates[0] if candidates else None)
        self._appids[game.key] = (signature, appid)
        return appid

    def _enqueue(self, keys: list[str]) -> None:
        was_idle = not self._queue
        self._queue += [key for key in keys if key not in self._queue]
        if was_idle and self._queue:
            QTimer.singleShot(0, self._load_next_batch)

    def _load_next_batch(self) -> None:
        for _ in range(BATCH):
            if not self._queue:
                break
            self._load(self._queue.pop(0))
        if self._queue:
            QTimer.singleShot(0, self._load_next_batch)
        elif self._started and not self._initial_done:
            self._initial_done = True
            self._schedule_meta()

    def _load(self, key: str, fetch_web: bool = True) -> AchievementSet | None:
        self._last_reload[key] = time.monotonic()
        game = self._library.get(key)
        appid = self.appid_for(game) if game else None
        if appid is None:
            self._store(key, None)
            return None

        local = steam_local.load_steam_local(appid)
        emulator = emulators.read_progress(appid)
        merged = merge(appid, local, emulator, self._web.cached(appid))
        self._watch(key, appid)
        self._notify_new(key, appid, merged)
        self._store(key, merged)
        if fetch_web:
            self._fetch_web(key, appid)
        return merged

    def _fetch_web(self, key: str, appid: int) -> None:
        """Названия, иконки и редкость из Steam (кэш на 3 дня). Нужны и для кряков: у них в файлах только имена."""
        failed_at = self._web_failed.get(appid)
        if appid in self._web_done or appid in self._web_pending or (
                failed_at is not None and time.monotonic() - failed_at < WEB_RETRY_SECONDS):
            return
        self._web_pending.add(appid)

        def ok(_items, k=key, a=appid):
            self._web_pending.discard(a)
            self._web_done.add(a)
            self._web_failed.pop(a, None)
            self._load(k, fetch_web=False)
            self._schedule_meta()

        def failed(_message, a=appid):
            self._web_pending.discard(a)
            self._web_failed[a] = time.monotonic()  # сети не было (запуск с Windows) — попробуем позже
            self._schedule_meta()

        self._web.fetch(appid, ok, failed)

    def _notify_new(self, key: str, appid: int, merged: AchievementSet | None) -> None:
        """Поздравляем только с тем, что получено после первого чтения игры в этом запуске.
        Старые достижения, которые «нашлись» позже (Steam дописал прогресс, игру перепривязали), — молча."""
        unlocked = [a for a in merged.items if a.unlocked] if merged else []
        baseline = self._baselines.get(key)
        if baseline is None or baseline.appid != appid:
            self._baselines[key] = _Baseline(appid, time.time(), {a.api.lower() for a in unlocked})
            return
        fresh = [a for a in unlocked if a.api.lower() not in baseline.known]
        baseline.known |= {a.api.lower() for a in fresh}  # база только растёт: мелькание не даёт повторов
        for achievement in sorted(fresh, key=lambda a: a.unlocked_at or 0):
            if achievement.unlocked_at and achievement.unlocked_at < baseline.since - OLD_UNLOCK_MARGIN:
                continue
            self.unlocked.emit(key, achievement)

    def _store(self, key: str, merged: AchievementSet | None) -> None:
        signature = _content_signature(merged)
        if merged is None:
            self._sets.pop(key, None)
        else:
            self._sets[key] = merged
        if self._signatures.get(key) != signature:  # ничего не изменилось — не перестраиваем интерфейс
            self._signatures[key] = signature
            self.changed.emit(key)
            self._schedule_meta()

    # ------------------------------------------------------------------ слежение за файлами

    def _watch(self, key: str, appid: int) -> None:
        paths = steam_local.progress_paths(appid) + emulators.watch_paths(appid)
        for path in paths:
            if not os.path.exists(path):
                continue
            normalized = os.path.normcase(os.path.abspath(path))
            self._watch_keys.setdefault(normalized, set()).add(key)
            if path not in self._watcher.files() and path not in self._watcher.directories():
                self._watcher.addPath(path)

    def _on_path_changed(self, path: str) -> None:
        normalized = os.path.normcase(os.path.abspath(path))
        keys = set(self._watch_keys.get(normalized, set()))
        if os.path.isdir(path) and len(keys) > BATCH:
            # Общая папка кряков (…\RUNE): перечитываем только игры, чья папка <appid> в ней есть
            try:
                names = set(os.listdir(path))
            except OSError:
                names = set()
            keys = {key for key in keys if str(self._appids.get(key, ((), None))[1]) in names}
        # Многие программы пишут файл через замену — после этого его снова нужно добавить в наблюдение
        if os.path.isfile(path) and path not in self._watcher.files():
            self._watcher.addPath(path)
        self._pending |= keys
        if keys:
            self._reload_timer.start()

    def _reload_pending(self) -> None:
        pending, self._pending = self._pending, set()
        for key in pending:
            self._last_reload.pop(key, None)
        self._enqueue(sorted(pending))

    # ------------------------------------------------------------------ библиотека и свои достижения

    def _on_library_changed(self) -> None:
        games = {game.key: game for game in self._library.games()}
        for key in list(self._last_reload):
            if key not in games:  # игру удалили — забываем всё, чтобы при повторном добавлении прочитать заново
                for store in (self._last_reload, self._appids, self._baselines, self._signatures):
                    store.pop(key, None)
                self._store(key, None)
        if not self._started:
            return  # start() прочитает всю библиотеку сам
        changed = []
        for key, game in games.items():
            cached = self._appids.get(key)
            if key not in self._last_reload or (cached and cached[0] != _install_signature(game)):
                changed.append(key)  # новая игра или её переустановили — перечитаем
        self._enqueue(changed)
        self._schedule_meta()

    def _schedule_meta(self) -> None:
        if self._initial_done:
            self._meta_timer.start()

    def _update_meta(self) -> None:
        # При первом запуске срез «что уже сделано» снимаем, только когда пришли все ответы из сети,
        # иначе редкость, пришедшая позже, дала бы поздравление за давно полученное достижение
        first_time = not self._settings.get("meta_baseline_done")
        if first_time and (self._web_pending or self._queue):
            return  # следующий ответ сети снова запустит оценку
        now = now_iso()
        for achievement in self.meta():
            if not achievement.unlocked and achievement.value >= achievement.definition.target:
                self._library.unlock_meta(achievement.definition.id, now)
                achievement.unlocked_at = now
                if not first_time:  # при первом запуске не засыпаем поздравлениями за прошлое
                    self.meta_unlocked.emit(achievement)
        if first_time:
            self._settings.set("meta_baseline_done", True)
        self.summary_changed.emit()
