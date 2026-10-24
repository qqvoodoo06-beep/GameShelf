from __future__ import annotations

from ..config import Settings
from ..net import Http
from .base import PAGE_SIZE, CatalogProvider, GameDetails, SearchResult
from .rawg import RawgProvider
from .steam import SteamProvider

__all__ = ["PAGE_SIZE", "CatalogProvider", "GameDetails", "SearchResult", "RawgProvider", "SteamProvider",
           "create_providers"]


def create_providers(http: Http, settings: Settings) -> dict[str, CatalogProvider]:
    providers = (SteamProvider(http, settings), RawgProvider(http, settings))
    return {provider.name: provider for provider in providers}
