"""Запуск вместе с Windows через ключ Run текущего пользователя (права администратора не нужны)."""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Сюда Диспетчер задач пишет, что программа отключена на вкладке «Автозагрузка» (значение в Run при этом остаётся)
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "GameShelf"

_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import winreg


def launch_command() -> str:
    """Команда для автозапуска: программа стартует свёрнутой в трей."""
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.executable)}" --tray'
    python = os.path.abspath(sys.executable)
    # pythonw.exe — без чёрного окна консоли при входе в систему
    pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.isfile(pythonw):
        python = pythonw
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f'"{python}" "{os.path.join(project, "main.py")}" --tray'


def _stored_command(key_path: str, value_name: str) -> str | None:
    """Команда, записанная в реестре, или None, если её нет."""
    if not _IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, value_name)
    except (OSError, ValueError, TypeError):  # ValueError/TypeError — недопустимое имя ключа или значения
        return None
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
        return None
    return value


def disabled_in_task_manager(approved_key: str = APPROVED_KEY, value_name: str = VALUE_NAME) -> bool:
    """Отключено ли на вкладке «Автозагрузка» Диспетчера задач (первый байт значения нечётный: 03, 01…)."""
    if not _IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_key, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, value_name)
    except (OSError, ValueError, TypeError):
        return False
    return kind == winreg.REG_BINARY and isinstance(value, bytes) and len(value) > 0 and value[0] % 2 == 1


def is_enabled(key_path: str = RUN_KEY, value_name: str = VALUE_NAME, approved_key: str = APPROVED_KEY) -> bool:
    """Запустится ли GameShelf при входе в Windows."""
    registered = bool((_stored_command(key_path, value_name) or "").strip())
    return registered and not disabled_in_task_manager(approved_key, value_name)


def set_enabled(enabled: bool, key_path: str = RUN_KEY, value_name: str = VALUE_NAME,
                approved_key: str = APPROVED_KEY) -> None:
    """Включает или выключает автозапуск. При ошибке записи бросает OSError."""
    if not _IS_WINDOWS:
        return
    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, launch_command())
        # Если раньше отключали в Диспетчере задач — снимаем этот запрет, иначе Windows программу не запустит
        _delete_value(approved_key, value_name)
        return
    _delete_value(key_path, value_name)


def _delete_value(key_path: str, value_name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass  # ключа или значения и так нет


def sync(key_path: str = RUN_KEY, value_name: str = VALUE_NAME, approved_key: str = APPROVED_KEY) -> None:
    """Если программу перенесли в другую папку, обновляет путь в автозапуске. Не бросает исключений."""
    try:
        stored = _stored_command(key_path, value_name)
        if not stored or not stored.strip() or stored == launch_command():
            return
        if disabled_in_task_manager(approved_key, value_name):
            return  # пользователь отключил автозапуск сам — не включаем его обратно
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, launch_command())
    except (OSError, ValueError, TypeError):
        pass  # вызывается при каждом старте: сбой автозапуска не должен мешать запуску программы
