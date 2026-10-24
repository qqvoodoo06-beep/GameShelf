"""Собственные достижения GameShelf — за саму библиотеку и привычки игрока, работают для любых игр."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..i18n import tr
from ..models import Game, Session, Status


@dataclass(frozen=True)
class MetaDefinition:
    id: str
    name: str
    description: str
    icon: str          # имя иконки из gameshelf.icons
    color: str
    metric: str        # какой показатель сравниваем с target
    target: float
    unit: str = ""     # подпись к прогрессу: «ч», «игр»…


@dataclass
class MetaAchievement:
    definition: MetaDefinition
    value: float
    unlocked_at: str = ""        # ISO; пусто — ещё не получено

    @property
    def unlocked(self) -> bool:
        return bool(self.unlocked_at)

    @property
    def progress(self) -> float:
        return min(1.0, self.value / self.definition.target) if self.definition.target else 1.0


GOLD, VIOLET, GREEN, BLUE, PINK = "#fbbf24", "#a78bfa", "#34d399", "#60a5fa", "#f472b6"

DEFINITIONS: list[MetaDefinition] = [
    MetaDefinition("first_game", tr("Первая полка"), tr("Добавьте первую игру в библиотеку"), "bookmark", VIOLET,
                   "games", 1, tr("игр")),
    MetaDefinition("collector_10", tr("Коллекционер"), tr("Соберите 10 игр в библиотеке"), "grid", VIOLET, "games", 10,
                   tr("игр")),
    MetaDefinition("collector_50", tr("Хранитель полки"), tr("Соберите 50 игр в библиотеке"), "grid", GOLD, "games", 50,
                   tr("игр")),
    MetaDefinition("finish_1", tr("Финальные титры"), tr("Пройдите первую игру"), "check", GREEN, "completed", 1,
                   tr("игр")),
    MetaDefinition("finish_10", tr("Финишер"), tr("Пройдите 10 игр"), "check", GREEN, "completed", 10, tr("игр")),
    MetaDefinition("finish_25", tr("Легенда финалов"), tr("Пройдите 25 игр"), "trophy", GOLD, "completed", 25,
                   tr("игр")),
    MetaDefinition("hours_10", tr("Разогрев"), tr("Наиграйте 10 часов"), "clock", BLUE, "hours", 10, tr("ч")),
    MetaDefinition("hours_100", tr("Марафонец"), tr("Наиграйте 100 часов"), "clock", BLUE, "hours", 100, tr("ч")),
    MetaDefinition("hours_500", tr("Жизнь в игре"), tr("Наиграйте 500 часов"), "clock", GOLD, "hours", 500, tr("ч")),
    MetaDefinition("session_4h", tr("Ещё один ход"), tr("Играйте 4 часа без перерыва"), "flame", PINK,
                   "longest_session", 4, tr("ч")),
    MetaDefinition("streak_7", tr("Неделя в игре"), tr("Играйте 7 дней подряд"), "calendar", PINK, "streak", 7,
                   tr("дн.")),
    MetaDefinition("night_owl", tr("Полуночник"), tr("Начните игру между полуночью и пятью утра"), "eye", VIOLET,
                   "night", 1, ""),
    MetaDefinition("comeback", tr("Возвращение"), tr("Вернитесь к игре после перерыва больше 30 дней"), "play", GREEN,
                   "comeback", 1, ""),
    MetaDefinition("critic_10", tr("Критик"), tr("Оцените 10 игр"), "star", GOLD, "rated", 10, tr("игр")),
    MetaDefinition("chronicler_5", tr("Летописец"), tr("Напишите заметки к 5 играм"), "bookmark", BLUE, "notes", 5,
                   tr("игр")),
    MetaDefinition("genres_10", tr("Всеядный"), tr("Соберите игры 10 разных жанров"), "sparkles", PINK, "genres", 10,
                   tr("жанров")),
    MetaDefinition("trophy_100", tr("Охотник за трофеями"), tr("Получите 100 достижений в играх"), "trophy", BLUE,
                   "trophies", 100, ""),
    MetaDefinition("trophy_500", tr("Собиратель трофеев"), tr("Получите 500 достижений в играх"), "trophy", GOLD,
                   "trophies", 500, ""),
    MetaDefinition("perfect_1", tr("Платина"), tr("Получите все достижения в одной игре"), "trophy", GOLD, "perfect", 1,
                   tr("игр")),
    MetaDefinition("rare_1", tr("Редкая птица"), tr("Получите достижение, которое есть меньше чем у 5% игроков"),
                   "sparkles", GOLD, "rare", 1, ""),
]


@dataclass
class TrophyStats:
    """Сводка по достижениям из игр — считает сервис достижений."""
    unlocked: int = 0
    perfect_games: int = 0
    rare_unlocked: int = 0


def _days_streak(days: set[date]) -> int:
    best = current = 0
    previous: date | None = None
    for day in sorted(days):
        current = current + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, current)
        previous = day
    return best


def _played_days(sessions: list[Session]) -> set[date]:
    """Все календарные дни, в которые шла игра: сеанс 23:50–01:30 засчитывает оба дня."""
    days: set[date] = set()
    for session in sessions:
        start, end = _parse(session.started_at), _parse(session.ended_at)
        if start is None:
            continue
        day, last = start.date(), (end or start).date()
        while day <= last and len(days) < 100000:
            days.add(day)
            day += timedelta(days=1)
    return days


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def metrics(games: list[Game], sessions: list[Session], trophies: TrophyStats) -> dict[str, float]:
    starts = [(s.game_key, _parse(s.started_at), s.seconds) for s in sessions]
    starts = [(key, moment, seconds) for key, moment, seconds in starts if moment is not None]

    comeback = 0
    by_game: dict[str, list[datetime]] = {}
    for key, moment, _seconds in sorted(starts, key=lambda item: item[1]):
        history = by_game.setdefault(key, [])
        if history and moment - history[-1] > timedelta(days=30):
            comeback = 1
        history.append(moment)

    return {
        "games": len(games),
        "completed": sum(1 for g in games if g.status == Status.COMPLETED),
        "hours": sum(g.hours for g in games),
        "longest_session": max((seconds for _k, _m, seconds in starts), default=0) / 3600,
        "streak": _days_streak(_played_days(sessions)),
        "night": 1 if any(moment.hour < 5 for _k, moment, _s in starts) else 0,
        "comeback": comeback,
        "rated": sum(1 for g in games if g.user_rating),
        "notes": sum(1 for g in games if g.notes.strip()),
        "genres": len({genre for g in games for genre in g.genres}),
        "trophies": trophies.unlocked,
        "perfect": trophies.perfect_games,
        "rare": trophies.rare_unlocked,
    }


def evaluate(games: list[Game], sessions: list[Session], trophies: TrophyStats,
             unlocked: dict[str, str]) -> list[MetaAchievement]:
    """Текущее состояние всех достижений GameShelf. unlocked — уже сохранённые даты получения."""
    values = metrics(games, sessions, trophies)
    return [MetaAchievement(d, values.get(d.metric, 0), unlocked.get(d.id, "")) for d in DEFINITIONS]
