"""Общие сервисы приложения, доступные из любого окна."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .achievements.service import AchievementService
    from .achievements.steam_web import SteamAchievementsWeb
    from .config import Settings
    from .database import Library
    from .images import ImageLoader
    from .models import Game
    from .providers import CatalogProvider
    from .tracking.linking import InstallLinker
    from .tracking.tracker import PlaytimeTracker
    from .ui.overlay import AchievementOverlay


class AppContext:
    settings: Settings
    library: Library
    images: ImageLoader
    providers: dict[str, CatalogProvider]
    tracker: PlaytimeTracker
    linker: InstallLinker
    achievements: AchievementService
    achievements_web: SteamAchievementsWeb
    overlay: AchievementOverlay
    toast: Callable[[str], None] = staticmethod(lambda text: None)
    restart_requested: bool = False  # после выхода запустить программу заново (сменили язык)

    def catalog(self) -> CatalogProvider:
        """Каталог, выбранный в настройках, — в нём идёт поиск."""
        return self.providers.get(self.settings.catalog) or self.providers["steam"]

    def provider_for(self, game: Game) -> CatalogProvider | None:
        """Каталог, из которого добавлена игра, — у него берём подробности."""
        return self.providers.get(game.source)


ctx = AppContext()
