from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .models import GAME_FIELDS, Game, Session, Status

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    key             TEXT    PRIMARY KEY,
    source          TEXT    NOT NULL,
    id              INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    slug            TEXT    NOT NULL DEFAULT '',
    cover_url       TEXT    NOT NULL DEFAULT '',
    hero_url        TEXT    NOT NULL DEFAULT '',
    released        TEXT    NOT NULL DEFAULT '',
    genres          TEXT    NOT NULL DEFAULT '[]',
    platforms       TEXT    NOT NULL DEFAULT '[]',
    metacritic      INTEGER,
    score           INTEGER,
    score_label     TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL,
    user_rating     INTEGER NOT NULL DEFAULT 0,
    hours           REAL    NOT NULL DEFAULT 0,
    notes           TEXT    NOT NULL DEFAULT '',
    added_at        TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    finished_at     TEXT    NOT NULL DEFAULT '',
    install_source  TEXT    NOT NULL DEFAULT '',
    install_id      TEXT    NOT NULL DEFAULT '',
    install_dir     TEXT    NOT NULL DEFAULT '',
    exe_path        TEXT    NOT NULL DEFAULT '',
    last_played     TEXT    NOT NULL DEFAULT ''
);
""" + """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key    TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT    NOT NULL,
    seconds     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_game ON sessions (game_key);
CREATE INDEX IF NOT EXISTS sessions_by_start ON sessions (started_at);
""" + """
CREATE TABLE IF NOT EXISTS meta_achievements (
    id           TEXT PRIMARY KEY,
    unlocked_at  TEXT NOT NULL
);
"""

# Версия 1 хранила только игры RAWG: ключом был id, а оценка — rawg_rating от 0 до 5
MIGRATE_V1 = """
ALTER TABLE games RENAME TO games_v1;
""" + SCHEMA + """
INSERT INTO games (key, source, id, name, slug, cover_url, hero_url, released, genres, platforms,
                   metacritic, score, score_label, status, user_rating, hours, notes,
                   added_at, updated_at, finished_at)
SELECT 'rawg:' || id, 'rawg', id, name, slug, cover_url, cover_url, released, genres, platforms,
       metacritic, CASE WHEN rawg_rating > 0 THEN CAST(ROUND(rawg_rating * 20) AS INTEGER) END, '',
       status, user_rating, hours, notes, added_at, updated_at, finished_at
FROM games_v1;
DROP TABLE games_v1;
"""

# Версия 2 → 3: привязка к установленной игре и сеансы для учёта времени
MIGRATE_V2 = """
ALTER TABLE games ADD COLUMN install_source TEXT NOT NULL DEFAULT '';
ALTER TABLE games ADD COLUMN install_id     TEXT NOT NULL DEFAULT '';
ALTER TABLE games ADD COLUMN install_dir    TEXT NOT NULL DEFAULT '';
ALTER TABLE games ADD COLUMN exe_path       TEXT NOT NULL DEFAULT '';
ALTER TABLE games ADD COLUMN last_played    TEXT NOT NULL DEFAULT '';
""" + SCHEMA

_LIST_FIELDS = ("genres", "platforms")
_COLUMNS = ["key"] + GAME_FIELDS


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Хранит игры, которые пользователь добавил себе, и историю сеансов (SQLite)."""

    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        # Версия 3 → 4 — только новая таблица собственных достижений: её создаёт SCHEMA (IF NOT EXISTS)
        script = {0: SCHEMA, 1: MIGRATE_V1, 2: MIGRATE_V2, 3: SCHEMA}.get(version)
        if script:
            with self._conn:
                self._conn.executescript("BEGIN;" + script + f"PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;")

    # ------------------------------------------------------------------ игры

    def load_all(self) -> list[Game]:
        rows = self._conn.execute("SELECT * FROM games").fetchall()
        return [self._from_row(row) for row in rows]

    def upsert(self, game: Game) -> None:
        values = {name: getattr(game, name) for name in GAME_FIELDS}
        for name in _LIST_FIELDS:
            values[name] = json.dumps(values[name], ensure_ascii=False)
        values["status"] = str(game.status)
        values["key"] = game.key
        columns = ", ".join(_COLUMNS)
        placeholders = ", ".join(f":{name}" for name in _COLUMNS)
        updates = ", ".join(f"{name}=excluded.{name}" for name in _COLUMNS if name != "key")
        self._conn.execute(
            f"INSERT INTO games ({columns}) VALUES ({placeholders}) ON CONFLICT(key) DO UPDATE SET {updates}",
            values,
        )
        self._conn.commit()

    def delete(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM games WHERE key = ?", (key,))
            self._conn.execute("DELETE FROM sessions WHERE game_key = ?", (key,))

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Game:
        data = dict(row)
        data.pop("key")
        for name in _LIST_FIELDS:
            try:
                data[name] = json.loads(data[name])
            except ValueError:
                data[name] = []
        data["status"] = Status(data["status"])
        return Game(**data)

    # ------------------------------------------------------------------ сеансы

    def insert_session(self, key: str, started_at: str, ended_at: str, seconds: int) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO sessions (game_key, started_at, ended_at, seconds) VALUES (?, ?, ?, ?)",
                (key, started_at, ended_at, seconds))
        return cursor.lastrowid

    def update_session(self, session_id: int, ended_at: str, seconds: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE sessions SET ended_at = ?, seconds = ? WHERE id = ?",
                               (ended_at, seconds, session_id))

    # ------------------------------------------------------------------ собственные достижения

    def meta_unlocked(self) -> dict[str, str]:
        return dict(self._conn.execute("SELECT id, unlocked_at FROM meta_achievements").fetchall())

    def unlock_meta(self, achievement_id: str, when: str) -> None:
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO meta_achievements (id, unlocked_at) VALUES (?, ?)",
                               (achievement_id, when))

    def sessions(self, key: str | None = None, since: str | None = None) -> list[Session]:
        query, params = "SELECT * FROM sessions WHERE 1 = 1", []
        if key is not None:
            query += " AND game_key = ?"
            params.append(key)
        if since is not None:
            query += " AND started_at >= ?"
            params.append(since)
        rows = self._conn.execute(query + " ORDER BY started_at DESC", params).fetchall()
        return [Session(**dict(row)) for row in rows]


class Library(QObject):
    """Библиотека пользователя: держит игры в памяти и сообщает об изменениях."""

    changed = Signal()
    playtime_changed = Signal(str)  # key — время игры обновилось (без пересборки всего списка)

    def __init__(self, db: Database):
        super().__init__()
        self._db = db
        self._games: dict[str, Game] = {g.key: g for g in db.load_all()}
        self._batch_depth = 0
        self._batch_dirty = False

    @contextmanager
    def batch(self):
        """Много изменений подряд (автопривязка, перенос часов) — один сигнал changed в конце,
        иначе библиотека перестраивалась бы на каждую игру."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._batch_dirty:
                self._batch_dirty = False
                self.changed.emit()

    def _emit_changed(self) -> None:
        if self._batch_depth:
            self._batch_dirty = True
        else:
            self.changed.emit()

    def _emit_playtime(self, key: str) -> None:
        if self._batch_depth:
            self._batch_dirty = True
        else:
            self.playtime_changed.emit(key)

    def games(self) -> list[Game]:
        return list(self._games.values())

    def get(self, key: str) -> Game | None:
        game = self._games.get(key)
        return replace(game) if game else None

    def status_of(self, key: str) -> Status | None:
        game = self._games.get(key)
        return game.status if game else None

    def counts(self) -> dict:
        counts = {status: 0 for status in Status}
        for game in self._games.values():
            counts[game.status] += 1
        counts["all"] = len(self._games)
        return counts

    def save(self, game: Game) -> None:
        now = now_iso()
        existing = self._games.get(game.key)
        game.added_at = existing.added_at if existing else (game.added_at or now)
        if game.status == Status.COMPLETED and not game.finished_at:
            game.finished_at = now
        game.updated_at = now
        self._store(game)
        self._emit_changed()

    def update_metadata(self, key: str, fresh: Game) -> None:
        """Обновляет данные из каталога (дата, жанры, обложка), не считая это правкой пользователя."""
        game = self._games.get(key)
        if game is None:
            return
        updated = replace(game)
        updated.merge_metadata(fresh)
        if updated != game:
            self._store(updated)
            self._emit_changed()

    def set_install(self, key: str, source: str, install_id: str = "", install_dir: str = "",
                    exe_path: str = "") -> None:
        """Привязывает игру к папке/файлу на диске. source="none" — пользователь отвязал игру сам."""
        game = self.get(key)
        if game is None:
            return
        game.install_source, game.install_id = source, install_id
        game.install_dir, game.exe_path = install_dir, exe_path
        self._store(game)
        self._emit_changed()

    def add(self, game: Game, status: Status) -> None:
        self.save(replace(game, status=status))

    def set_status(self, key: str, status: Status) -> None:
        game = self.get(key)
        if game:
            game.status = status
            self.save(game)

    def remove(self, key: str) -> None:
        self._db.delete(key)
        self._games.pop(key, None)
        self._emit_changed()

    # ------------------------------------------------------------------ время в играх

    def start_session(self, key: str, started_at: str, ended_at: str, seconds: int) -> int | None:
        if key not in self._games:
            return None
        return self._db.insert_session(key, started_at, ended_at, seconds)

    def update_session(self, session_id: int, ended_at: str, seconds: int) -> None:
        self._db.update_session(session_id, ended_at, seconds)

    def add_playtime(self, key: str, seconds: int, when: str) -> None:
        """Прибавляет наигранное время к часам игры и отмечает, когда играли."""
        game = self.get(key)
        if game is None or seconds <= 0:
            return
        # Без округления: иначе каждая минута превращалась бы в 60,12 с и часы постепенно завышались
        game.hours = game.hours + seconds / 3600
        game.last_played = when
        self._store(game)
        self._emit_playtime(key)

    def set_playtime(self, key: str, hours: float, last_played: str) -> None:
        """Задаёт часы и дату последней игры напрямую (перенос из Steam)."""
        game = self.get(key)
        if game is None:
            return
        game.hours, game.last_played = hours, last_played
        self._store(game)
        self._emit_playtime(key)

    def sessions(self, key: str | None = None, since: str | None = None) -> list[Session]:
        return self._db.sessions(key, since)

    def meta_unlocked(self) -> dict[str, str]:
        """Собственные достижения GameShelf: id → когда получено."""
        return self._db.meta_unlocked()

    def unlock_meta(self, achievement_id: str, when: str) -> None:
        self._db.unlock_meta(achievement_id, when)

    def _store(self, game: Game) -> None:
        self._db.upsert(game)
        self._games[game.key] = replace(game)
