"""Достижения: общий формат для всех источников (Steam, кряки-эмуляторы, сеть)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Achievement:
    api: str                                   # внутреннее имя (API name), по нему сопоставляем источники
    name: str = ""                             # название (по-русски, если есть перевод)
    description: str = ""
    icon: str = ""                             # URL цветной иконки
    icon_gray: str = ""                        # URL серой иконки ("" — обесцветим цветную сами)
    hidden: bool = False                       # скрытое: описание показываем только после получения
    unlocked: bool | None = None               # None — прогресс неизвестен
    unlocked_at: int | None = None             # unix-время получения
    time_approx: bool = False                  # время известно с точностью до ~15 минут (старые кряки)
    rarity: float | None = None                # доля игроков, получивших его, %
    progress: tuple[float, float] | None = None  # (сейчас, нужно) — для достижений «собери 100 …»
    origin: str = ""                           # кто засчитал получение: "steam" или "emulator" (кряк)


@dataclass
class AchievementSet:
    """Достижения одной игры вместе с прогрессом пользователя."""
    appid: int
    items: list[Achievement] = field(default_factory=list)
    source: str = ""                 # откуда прогресс: "Steam", "RUNE", "CODEX", "OnlineFix"…
    updated_at: float | None = None  # когда источник прогресса последний раз обновлялся (mtime файла)
    progress_known: bool = False     # False — знаем только список достижений, без «получено/нет»
    complete: bool = True            # False — полного списка ещё нет (кряки пишут только полученные)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def unlocked_count(self) -> int:
        return sum(1 for item in self.items if item.unlocked)


def steam_icon_url(appid: int, file_name: str) -> str:
    """Иконка достижения Steam. Этот путь работает и для новых игр (старый /steamcommunity/... у них даёт 404)."""
    if not file_name:
        return ""
    return f"https://shared.fastly.steamstatic.com/community_assets/images/apps/{appid}/{file_name}"
