"""Асинхронные HTTP-запросы через Qt: интерфейс не зависает, пока идёт загрузка."""
from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .config import APP_NAME, APP_VERSION

USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# Код ошибки, когда сервер ответил, но не JSON
BAD_RESPONSE = -1


def call_safely(callback, *args) -> None:
    """Вызывает callback, игнорируя виджеты, которые уже успели удалить."""
    try:
        callback(*args)
    except RuntimeError as exc:
        if "already deleted" not in str(exc):
            raise


class Http(QObject):
    def __init__(self, nam: QNetworkAccessManager, parent=None):
        super().__init__(parent)
        self._nam = nam

    def get(self, url: QUrl | str, on_ok: Callable[[bytes], None], on_err: Callable[[int | None], None],
            timeout_ms: int = 20000) -> None:
        """on_err получает HTTP-код ошибки или None, если сервер недоступен."""
        request = QNetworkRequest(QUrl(url) if isinstance(url, str) else url)
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
        request.setTransferTimeout(timeout_ms)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._finished(reply, on_ok, on_err))

    def get_json(self, url: QUrl | str, on_ok: Callable[[dict | list], None],
                 on_err: Callable[[int | None], None], timeout_ms: int = 20000) -> None:
        def parse(body: bytes) -> None:
            try:
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                call_safely(on_err, BAD_RESPONSE)
                return
            call_safely(on_ok, payload)

        self.get(url, parse, on_err, timeout_ms)

    @staticmethod
    def _finished(reply: QNetworkReply, on_ok, on_err) -> None:
        reply.deleteLater()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        body = bytes(reply.readAll())
        if reply.error() != QNetworkReply.NetworkError.NoError:
            call_safely(on_err, status or None)
            return
        call_safely(on_ok, body)
