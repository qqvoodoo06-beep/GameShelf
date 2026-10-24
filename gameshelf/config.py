from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths

APP_NAME = "GameShelf"
APP_VERSION = "0.3.0"


def data_dir() -> Path:
    """Папка с базой, настройками и кэшем обложек (по умолчанию %APPDATA%/GameShelf)."""
    override = os.environ.get("GAMESHELF_DATA_DIR")
    if override:
        path = Path(override)
    else:
        path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Settings:
    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def catalog(self) -> str:
        """Каталог для поиска игр: "steam" (без регистрации) или "rawg" (нужен ключ)."""
        return self.get("catalog", "steam")

    @catalog.setter
    def catalog(self, value: str) -> None:
        self.set("catalog", value)

    # ---- учёт времени в играх
    @property
    def tracking_enabled(self) -> bool:
        return self.get("tracking_enabled", True)

    @property
    def track_foreground_only(self) -> bool:
        """Считать время, только когда окно игры активно (не свёрнуто и не за другим окном)."""
        return self.get("track_foreground_only", False)

    @property
    def close_to_tray(self) -> bool:
        """Крестик сворачивает в значок у часов, чтобы учёт времени продолжался."""
        return self.get("close_to_tray", True)

    @property
    def notify_sessions(self) -> bool:
        return self.get("notify_sessions", True)

    @property
    def rawg_key(self) -> str:
        return self.get("rawg_api_key", "")

    @rawg_key.setter
    def rawg_key(self, value: str) -> None:
        self.set("rawg_api_key", value.strip())
