"""Только одна запущенная копия GameShelf: две копии считали бы игровое время дважды."""
from __future__ import annotations

import getpass
import os
import re
import sys
import time

from PySide6.QtCore import QCoreApplication, QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_SHOW = b"show"
_TIMEOUT_MS = 500
# Сколько ждать, пока владелец блокировки поднимет сервер (запускается) или завершится (закрывается)
_RETRIES = 30
_RETRY_DELAY = 0.1


def _allow_foreground() -> None:
    """Этот запуск сделал пользователь, поэтому ему можно на передний план. Передаём это право
    уже работающей копии — иначе Windows не даст ей показать окно, а только мигнёт кнопкой на панели задач."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.WinDLL("user32").AllowSetForegroundWindow(-1)  # ASFW_ANY
    except (OSError, AttributeError):
        pass


def default_name() -> str:
    """Имя канала с именем пользователя: у каждого пользователя Windows своя копия программы."""
    user = os.environ.get("USERNAME", "")
    if not user:
        try:
            user = getpass.getuser()
        except (OSError, ImportError, KeyError):
            user = ""
    return "GameShelf-" + (re.sub(r"[^\w.-]", "_", user) or "user")


class SingleInstance(QObject):
    """Объект нужно держать живым всё время работы программы: вместе с ним освобождается блокировка."""

    activated = Signal()  # другая копия попросила показать окно

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self._name = name or default_name()
        # В Windows два QLocalServer спокойно слушают одно имя, поэтому при одновременном запуске
        # первую копию определяет файл-блокировка. Если процесс убили, QLockFile сам увидит,
        # что владельца нет, и заберёт блокировку.
        lock_name = re.sub(r'[\\/:*?"<>|]', "_", self._name) + ".lock"
        self._lock = QLockFile(QDir(QDir.tempPath()).filePath(lock_name))
        self._lock.setStaleLockTime(0)  # держим всё время работы: устаревшей считаем, только если процесса нет
        self._server: QLocalServer | None = None
        self._first = False

    @property
    def name(self) -> str:
        return self._name

    def try_lock(self) -> bool:
        """True — мы первая копия. False — копия уже запущена, её попросили показаться; нужно выйти."""
        if self._first:
            return True
        if self._send_show():
            return False
        if not self._lock.tryLock(0):
            if self._lock.error() == QLockFile.LockError.LockFailedError:
                if not self._wait_for_owner():
                    return False
            # Иначе файл-блокировку создать нельзя (нет доступа к TEMP) — полагаемся только на сервер
        QLocalServer.removeServer(self._name)  # сокет, оставшийся после аварийного завершения
        self._listen()
        self._first = True
        return True

    def release(self) -> None:
        """Отпустить блокировку раньше выхода из процесса — перед запуском новой копии (перезапуск)."""
        if self._server is not None:
            self._server.close()
        self._lock.unlock()
        self._first = False

    def _wait_for_owner(self) -> bool:
        """Блокировка у живой копии без сервера: она либо стартует, либо закрывается.
        True — она успела завершиться и блокировка теперь наша."""
        for _ in range(_RETRIES):
            time.sleep(_RETRY_DELAY)
            if self._send_show():
                return False  # стартовала и подняла сервер — она и покажет окно
            if self._lock.tryLock(0):
                return True  # закрылась — первой копией становимся мы
        return False

    def _send_show(self) -> bool:
        """Стучится в запущенную копию. True — она ответила (значит, она есть)."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(_TIMEOUT_MS):
            return False
        _allow_foreground()
        socket.write(_SHOW + b"\n")
        socket.flush()
        socket.waitForBytesWritten(_TIMEOUT_MS)
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(_TIMEOUT_MS)
        return True

    def _listen(self) -> None:
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # другие пользователи не достучатся
        server.newConnection.connect(self._on_new_connection)
        if server.listen(self._name):
            self._server = server
            app = QCoreApplication.instance()
            if app is not None:
                # Канал закрываем в начале выхода: иначе новый запуск, пока эта копия сохраняет данные,
                # «достучится» до неё и тоже выйдет — и программа не будет запущена вовсе
                app.aboutToQuit.connect(server.close)
        else:
            server.deleteLater()  # окно по второму запуску не покажется, но копия всё равно одна

    def _on_new_connection(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            socket = server.nextPendingConnection()
            buffer = bytearray()

            def read(socket: QLocalSocket = socket, buffer: bytearray = buffer) -> None:
                buffer.extend(bytes(socket.readAll()))
                if _SHOW in buffer:
                    buffer.clear()
                    self.activated.emit()

            socket.readyRead.connect(read)
            socket.disconnected.connect(read)  # данные могли прийти вместе с отключением
            socket.disconnected.connect(socket.deleteLater)
            read()
