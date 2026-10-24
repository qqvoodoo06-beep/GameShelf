"""Список запущенных процессов и активное окно через WinAPI (ctypes, без psutil).

Вызывается из GUI-потока раз в несколько секунд, поэтому всё сделано
максимально дёшево: один проход EnumProcesses и по одному OpenProcess на процесс.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    path: str        # полный путь к exe, уже через normalize_path()
    parent: int = 0  # PID процесса, который его запустил (0 — неизвестно)


def normalize_path(path: str) -> str:
    """Приводит путь к сравнимому виду: на Windows регистр и слэши не важны."""
    # Кавычки и пробелы по краям встречаются в путях, вставленных вручную
    path = (path or "").strip().strip('"').strip()
    if not path:
        return ""  # normpath("") дал бы ".", а это совпадало бы с чем попало
    return os.path.normcase(os.path.normpath(path))


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PATH_BUFFER_CHARS = 32768  # максимум для длинных путей Windows
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _ProcessEntry(ctypes.Structure):
    """PROCESSENTRY32W — нужна ради PID родителя."""
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _load_winapi() -> SimpleNamespace:
    """Свои экземпляры WinDLL, чтобы argtypes не конфликтовали с ctypes.windll в других модулях."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # K32EnumProcesses есть в kernel32 начиная с Windows 7; иначе берём из psapi
    try:
        enum_processes = kernel32.K32EnumProcesses
    except AttributeError:
        enum_processes = ctypes.WinDLL("psapi", use_last_error=True).EnumProcesses
    enum_processes.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    enum_processes.restype = wintypes.BOOL

    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE  # c_void_p: на 64-битной системе дескриптор не обрежется

    query_image_name = kernel32.QueryFullProcessImageNameW
    query_image_name.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    query_image_name.restype = wintypes.BOOL

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    get_foreground_window = user32.GetForegroundWindow
    get_foreground_window.argtypes = ()
    get_foreground_window.restype = wintypes.HWND

    get_window_thread_process_id = user32.GetWindowThreadProcessId
    get_window_thread_process_id.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    get_window_thread_process_id.restype = wintypes.DWORD

    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE

    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    process_first.restype = wintypes.BOOL

    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    process_next.restype = wintypes.BOOL

    get_long_path = kernel32.GetLongPathNameW
    get_long_path.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    get_long_path.restype = wintypes.DWORD

    return SimpleNamespace(
        enum_processes=enum_processes, open_process=open_process, query_image_name=query_image_name,
        close_handle=close_handle, get_foreground_window=get_foreground_window,
        get_window_thread_process_id=get_window_thread_process_id, create_snapshot=create_snapshot,
        process_first=process_first, process_next=process_next, get_long_path=get_long_path,
    )


_api: SimpleNamespace | None = None
if sys.platform == "win32":
    try:
        _api = _load_winapi()
    except (OSError, AttributeError):
        _api = None


def _all_pids() -> list[int]:
    """PID всех процессов. EnumProcesses не сообщает нужный размер, поэтому буфер растёт,
    пока в него не перестанет помещаться всё впритык."""
    capacity = 1024
    while capacity <= 1 << 20:
        buffer = (wintypes.DWORD * capacity)()
        size_bytes = ctypes.sizeof(buffer)
        returned = wintypes.DWORD(0)
        if not _api.enum_processes(buffer, size_bytes, ctypes.byref(returned)):
            return []
        if returned.value < size_bytes:
            count = returned.value // ctypes.sizeof(wintypes.DWORD)
            return [pid for pid in buffer[:count] if pid]  # 0 — System Idle Process
        capacity *= 2
    return []


def _image_path(pid: int, buffer: ctypes.Array) -> str:
    """Полный путь к exe процесса или "" (нет доступа, процесс уже завершился и т.п.)."""
    handle = _api.open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(len(buffer))
        if not _api.query_image_name(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        _api.close_handle(handle)


def _parents() -> dict[int, int]:
    """PID → PID родителя: по ним видно, какие процессы запустила игра (лаунчер → игра → crash handler)."""
    snapshot = _api.create_snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        return {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        parents: dict[int, int] = {}
        ok = _api.process_first(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = _api.process_next(snapshot, ctypes.byref(entry))
        return parents
    finally:
        _api.close_handle(snapshot)


def _long_path(path: str, buffer: ctypes.Array) -> str:
    """Короткий путь вида C:\\PROGRA~2\\... (старые ярлыки, .bat) → длинный, как его сохраняет GameShelf."""
    if "~" not in path:
        return path
    length = _api.get_long_path(path, buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else path


def running_processes() -> list[ProcessInfo]:
    """Все процессы, у которых удалось прочитать полный путь к exe."""
    if _api is None:
        return []
    try:
        buffer = ctypes.create_unicode_buffer(_PATH_BUFFER_CHARS)
        long_buffer = ctypes.create_unicode_buffer(_PATH_BUFFER_CHARS)
        parents = _parents()
        result: list[ProcessInfo] = []
        for pid in _all_pids():
            path = _image_path(pid, buffer)
            # Псевдопроцессы вроде "Registry" или "MemCompression" отдают имя без пути
            if path and os.path.isabs(path):
                path = _long_path(path, long_buffer)
                result.append(ProcessInfo(pid, normalize_path(path), parents.get(pid, 0)))
        return result
    except Exception:  # вызывается из таймера GUI: любая ошибка = пустой список, а не падение
        return []


def foreground_pid() -> int | None:
    """PID процесса, которому принадлежит активное окно, или None."""
    if _api is None:
        return None
    try:
        hwnd = _api.get_foreground_window()
        if not hwnd:
            return None  # например, рабочий стол заблокирован или окно как раз переключается
        pid = wintypes.DWORD(0)
        if not _api.get_window_thread_process_id(hwnd, ctypes.byref(pid)):
            return None
        return pid.value or None
    except Exception:
        return None
