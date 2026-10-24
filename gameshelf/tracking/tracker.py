"""Автоматический учёт времени: следим за запущенными программами и считаем, сколько открыта игра."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from ..config import Settings
from ..database import Library, now_iso
from ..models import Game
from .processes import ProcessInfo, foreground_pid, normalize_path, running_processes

if TYPE_CHECKING:
    from .linking import InstallLinker

POLL_MS = 5000
FLUSH_SECONDS = 60          # раз в минуту сохраняем сеанс — при сбое теряется не больше минуты
MIN_SESSION_SECONDS = 60    # случайные запуски короче минуты не записываем
MAX_TICK_SECONDS = 30       # больший промежуток значит, что компьютер спал, — это время не засчитываем


@dataclass
class ActiveSession:
    key: str
    started_at: str
    seconds: float = 0.0
    flushed: int = 0                # сколько секунд уже прибавлено к часам игры
    db_id: int | None = None
    last_flush: float = field(default_factory=time.monotonic)
    pids: set[int] = field(default_factory=set)


@dataclass
class Targets:
    """За чем следим в этом опросе."""
    exact: dict[str, list[str]] = field(default_factory=dict)      # exe → игры (один exe бывает у нескольких)
    prefixes: list[tuple[str, str]] = field(default_factory=list)  # (папка + разделитель, игра), длинные первыми
    folders: dict[str, list[str]] = field(default_factory=dict)    # игра → её папки (для дочерних процессов)
    last_played: dict[str, str] = field(default_factory=dict)


def _inside(path: str, folders: list[str]) -> bool:
    return any(path.startswith(folder) for folder in folders)


class PlaytimeTracker(QObject):
    session_started = Signal(str)        # key
    session_ended = Signal(str, int)     # key, засчитанные секунды — только для записанных сеансов
    active_changed = Signal()            # игра запустилась или закрылась (в том числе слишком короткий сеанс)
    tick = Signal()                      # каждые 5 секунд, пока идёт хотя бы один сеанс

    def __init__(self, library: Library, settings: Settings, linker: InstallLinker | None = None, parent=None):
        super().__init__(parent)
        self._library = library
        self._settings = settings
        self._linker = linker
        self._sessions: dict[str, ActiveSession] = {}
        self._last_tick = time.monotonic()
        self._real_paths: dict[str, str] = {}
        library.changed.connect(self._real_paths.clear)  # привязки поменялись — пути разрешим заново
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)

    # ------------------------------------------------------------------ публичное

    def start(self) -> None:
        self._last_tick = time.monotonic()
        self._timer.start()
        QTimer.singleShot(0, self.poll)

    def stop(self) -> None:
        """Останавливает учёт и сохраняет все идущие сеансы (при выходе из программы)."""
        self._timer.stop()
        self.stop_sessions()

    def stop_sessions(self) -> None:
        for key in list(self._sessions):
            self._finish(key)

    def is_active(self, key: str) -> bool:
        return key in self._sessions

    def active(self) -> list[tuple[str, int]]:
        """Идущие сеансы: (key, засчитано секунд)."""
        return [(s.key, int(s.seconds)) for s in self._sessions.values()]

    # ------------------------------------------------------------------ опрос

    def poll(self) -> None:
        now = time.monotonic()
        elapsed, self._last_tick = now - self._last_tick, now
        if not self._settings.tracking_enabled:
            self.stop_sessions()
            return

        targets = self._targets()
        running: dict[str, set[int]] = {}
        if targets.exact or targets.prefixes or self._sessions:
            for pid, key in self._assign(running_processes(), targets).items():
                running.setdefault(key, set()).add(pid)

        slept = elapsed > MAX_TICK_SECONDS
        for key in list(self._sessions):
            # После сна компьютера прошлый сеанс считаем законченным: если игра всё ещё открыта, начнётся новый.
            # Иначе «4 часа без перерыва» складывались бы из двух вечеров с ночью сна посередине.
            if key not in running or slept:
                self._finish(key)

        counted = 0 < elapsed <= MAX_TICK_SECONDS
        focused = foreground_pid() if self._settings.track_foreground_only else None
        for key, pids in running.items():
            session = self._sessions.get(key)
            if session is None:
                self._sessions[key] = ActiveSession(key, now_iso(), pids=pids)
                self.session_started.emit(key)
                self.active_changed.emit()
                continue
            session.pids = pids
            if counted and (not self._settings.track_foreground_only or focused in pids):
                session.seconds += elapsed
            if now - session.last_flush >= FLUSH_SECONDS:
                self._flush(session)

        if self._sessions:
            self.tick.emit()

    # ------------------------------------------------------------------ какие процессы — чьи

    def _real(self, path: str) -> str:
        """Настоящий путь: Windows показывает процессы без ярлыков-ссылок (junction) и коротких имён PROGRA~1."""
        real = self._real_paths.get(path)
        if real is None:
            try:
                real = normalize_path(os.path.realpath(path))
            except (OSError, ValueError):
                real = normalize_path(path)
            self._real_paths[path] = real
        return real

    def _targets(self) -> Targets:
        targets = Targets()
        for game in self._library.games():
            if not game.is_linked:
                continue
            key = game.key
            targets.last_played[key] = game.last_played
            folders: list[str] = []
            if game.exe_path:
                exe = self._real(game.exe_path)
                targets.exact.setdefault(exe, []).append(key)
                folders.append(os.path.dirname(exe).rstrip(os.sep) + os.sep)
            if game.install_source != "manual":
                # Лаунчеры ставят каждую игру в свою папку — следим за всем, что из неё запущено,
                # в том числе за второй копией той же игры в другом лаунчере.
                for folder in [game.install_dir] + self._other_copies(game):
                    prefix = self._real(folder).rstrip(os.sep) + os.sep
                    targets.prefixes.append((prefix, key))
                    folders.append(prefix)
            # У указанного вручную файла папку целиком не берём: он может лежать прямо в «D:\Games»
            # рядом с другими играми. Его дочерние процессы из той же папки найдутся отдельно.
            targets.folders[key] = folders
        targets.prefixes.sort(key=lambda item: len(item[0]), reverse=True)
        return targets

    def _other_copies(self, game: Game) -> list[str]:
        if self._linker is None:
            return []
        return [copy.install_dir for copy in self._linker.copies(game) if copy.install_dir != game.install_dir]

    def _choose(self, keys: list[str], targets: Targets) -> str:
        """Один exe или папка у нескольких игр: время получает одна — уже идущая или последняя по времени игры."""
        if len(keys) == 1:
            return keys[0]
        for key in keys:
            if key in self._sessions:
                return key
        return max(keys, key=lambda k: targets.last_played.get(k, ""))

    def _assign(self, processes: list[ProcessInfo], targets: Targets) -> dict[int, str]:
        """PID → игра. Каждый процесс достаётся не больше чем одной игре, поэтому время не задваивается."""
        owner: dict[int, str] = {}
        for process in processes:
            keys = targets.exact.get(process.path)
            if keys:
                owner[process.pid] = self._choose(keys, targets)

        exact_running = set(owner.values())
        for process in processes:
            if process.pid in owner:
                continue
            match = next(((prefix, key) for prefix, key in targets.prefixes if process.path.startswith(prefix)), None)
            if match is None:
                continue
            candidates = [key for prefix, key in targets.prefixes if prefix == match[0]]
            # Если из этой папки уже запущена игра, указанная по exe, вспомогательные процессы — её
            preferred = [key for key in candidates if key in exact_running]
            owner[process.pid] = self._choose(preferred or candidates, targets)

        alive = {process.pid for process in processes}
        previous = {pid: session.key for session in self._sessions.values() for pid in session.pids}
        for pid, key in previous.items():
            if pid in alive:
                owner.setdefault(pid, key)  # лаунчер уже закрылся, а запущенная им игра — нет

        # Процессы, которые запустила сама игра из своей папки (игра из подпапки, crash handler).
        # Родитель мог уже закрыться между опросами — тогда узнаём его по прошлому опросу.
        # Браузер или Discord, открытые из игры, лежат в других папках и сеанс не продлевают.
        changed = True
        while changed:
            changed = False
            for process in processes:
                if process.pid in owner:
                    continue
                key = owner.get(process.parent) or previous.get(process.parent)
                if key and _inside(process.path, targets.folders.get(key, [])):
                    owner[process.pid] = key
                    changed = True
        return owner

    # ------------------------------------------------------------------ сохранение

    def _flush(self, session: ActiveSession) -> None:
        session.last_flush = time.monotonic()
        total = int(session.seconds)
        if session.db_id is None and total < MIN_SESSION_SECONDS:
            return
        ended = now_iso()
        if session.db_id is None:
            session.db_id = self._library.start_session(session.key, session.started_at, ended, total)
            if session.db_id is None:
                return  # игру удалили из библиотеки
        else:
            self._library.update_session(session.db_id, ended, total)
        if total > session.flushed:
            self._library.add_playtime(session.key, total - session.flushed, ended)
            session.flushed = total

    def _finish(self, key: str) -> None:
        session = self._sessions.pop(key)
        self._flush(session)
        if session.db_id is not None:
            self.session_ended.emit(key, int(session.seconds))
        self.active_changed.emit()
