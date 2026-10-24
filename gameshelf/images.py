"""Загрузка обложек с кэшем в памяти и на диске."""
from __future__ import annotations

import hashlib
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .net import USER_AGENT, call_safely

ImageCallback = Callable[[QPixmap | None], None]

_RAWG_MEDIA = "media.rawg.io/media/"


_RAWG_WIDTHS = (420, 640, 1280)  # другие размеры RAWG не отдаёт (404)


def rawg_resized(url: str, width: int) -> str:
    """RAWG умеет отдавать уменьшенные картинки: /media/resize/640/-/games/..."""
    if _RAWG_MEDIA in url and "/media/resize/" not in url and "/media/crop/" not in url:
        supported = next((w for w in _RAWG_WIDTHS if w >= width), _RAWG_WIDTHS[-1])
        return url.replace(_RAWG_MEDIA, f"{_RAWG_MEDIA}resize/{supported}/-/", 1)
    return url


class ImageLoader(QObject):
    MAX_IN_MEMORY = 300

    def __init__(self, nam: QNetworkAccessManager, cache_dir: Path, parent=None):
        super().__init__(parent)
        self._nam = nam
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending: dict[str, list[ImageCallback]] = {}

    def load(self, url: str, callback: ImageCallback, width: int | None = None) -> None:
        if not url:
            return
        requested = rawg_resized(url, width) if width else url
        self._load(requested, url, callback)

    def _load(self, url: str, original: str, callback: ImageCallback) -> None:
        if url in self._memory:
            self._memory.move_to_end(url)
            call_safely(callback, self._memory[url])
            return

        path = self._path_for(url)
        if path.exists():
            pixmap = QPixmap()
            if pixmap.loadFromData(path.read_bytes()):
                self._remember(url, pixmap)
                call_safely(callback, pixmap)
                return

        if url in self._pending:
            self._pending[url].append(callback)
            return
        self._pending[url] = [callback]

        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
        request.setTransferTimeout(30000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._finished(reply, url, original))

    def _finished(self, reply: QNetworkReply, url: str, original: str) -> None:
        reply.deleteLater()
        data = bytes(reply.readAll())
        callbacks = self._pending.pop(url, [])
        pixmap = QPixmap()
        if reply.error() == QNetworkReply.NetworkError.NoError and pixmap.loadFromData(data):
            try:
                self._path_for(url).write_bytes(data)
            except OSError:
                pass
            self._remember(url, pixmap)
            for callback in callbacks:
                call_safely(callback, pixmap)
            return

        # Уменьшенная версия не загрузилась: пробуем оригинал
        if url != original:
            for callback in callbacks:
                self._load(original, original, callback)
            return
        for callback in callbacks:
            call_safely(callback, None)

    def _remember(self, url: str, pixmap: QPixmap) -> None:
        self._memory[url] = pixmap
        self._memory.move_to_end(url)
        while len(self._memory) > self.MAX_IN_MEMORY:
            self._memory.popitem(last=False)

    def _path_for(self, url: str) -> Path:
        return self._cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")

    def disk_usage(self) -> int:
        return sum(p.stat().st_size for p in self._cache_dir.glob("*.img"))

    def clear_disk_cache(self) -> None:
        shutil.rmtree(self._cache_dir, ignore_errors=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
