from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum

from .i18n import tr


class Status(StrEnum):
    PLAYING = "playing"
    PLANNED = "planned"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"

    @property
    def label(self) -> str:
        return STATUS_INFO[self][0]

    @property
    def color(self) -> str:
        return STATUS_INFO[self][1]

    @property
    def icon(self) -> str:
        return STATUS_INFO[self][2]


# Название, цвет и иконка для каждого статуса
STATUS_INFO = {
    Status.PLAYING: (tr("Играю"), "#a78bfa", "play"),
    Status.PLANNED: (tr("В планах"), "#60a5fa", "bookmark"),
    Status.COMPLETED: (tr("Пройдено"), "#34d399", "check"),
    Status.ON_HOLD: (tr("Отложено"), "#fbbf24", "pause"),
    Status.DROPPED: (tr("Брошено"), "#f87171", "x"),
}

# Каталоги, из которых берутся игры
SOURCE_TITLES = {"steam": "Steam", "rawg": "RAWG"}

# Поля, которые приходят из каталога (остальные заполняет пользователь)
METADATA_FIELDS = ("name", "slug", "cover_url", "hero_url", "released", "genres", "platforms",
                   "metacritic", "score", "score_label")


@dataclass
class Game:
    id: int
    name: str
    source: str = "steam"
    slug: str = ""
    cover_url: str = ""     # обложка для карточки (в Steam — вертикальная)
    hero_url: str = ""      # широкая картинка для шапки окна игры
    released: str = ""      # ГГГГ-ММ-ДД
    genres: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    metacritic: int | None = None
    score: int | None = None    # 0–100: Steam — доля положительных отзывов, RAWG — средняя оценка из 5 × 20
    score_label: str = ""       # например «Крайне положительные»
    # Данные пользователя
    status: Status | None = None
    user_rating: int = 0
    hours: float = 0.0
    notes: str = ""
    added_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    # Где игра установлена на этом компьютере — для учёта времени и кнопки «Играть»
    install_source: str = ""    # steam | epic | gog | manual; "none" — пользователь отвязал; "" — не найдена
    install_id: str = ""        # appid Steam, AppName Epic, gameID GOG
    install_dir: str = ""
    exe_path: str = ""
    last_played: str = ""       # когда последний раз играли (ISO)

    @property
    def is_linked(self) -> bool:
        return self.install_source not in ("", "none") and bool(self.install_dir or self.exe_path)

    @property
    def key(self) -> str:
        """Уникальный ключ: id из разных каталогов могут совпадать."""
        return f"{self.source}:{self.id}"

    @property
    def year(self) -> str:
        return self.released[:4] if self.released else ""

    @property
    def source_title(self) -> str:
        return SOURCE_TITLES.get(self.source, self.source)

    @property
    def store_url(self) -> str:
        if self.source == "steam":
            return f"https://store.steampowered.com/app/{self.id}/"
        return f"https://rawg.io/games/{self.slug or self.id}"

    def merge_metadata(self, other: Game) -> None:
        """Обновляет данные об игре, не трогая статус, оценку и заметки."""
        for name in METADATA_FIELDS:
            value = getattr(other, name)
            if value not in (None, "", []):
                setattr(self, name, value)


GAME_FIELDS = [f.name for f in fields(Game)]

INSTALL_SOURCE_TITLES = {"steam": "Steam", "epic": "Epic Games", "gog": "GOG", "manual": tr("указан вручную")}


@dataclass
class Session:
    """Один сеанс игры: от запуска до закрытия."""
    id: int
    game_key: str
    started_at: str     # ISO
    ended_at: str       # ISO
    seconds: int        # сколько засчитано (может быть меньше разницы времён, если окно было неактивно)
