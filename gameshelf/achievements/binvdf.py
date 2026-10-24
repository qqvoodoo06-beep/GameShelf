"""Бинарный KeyValues от Valve: так Steam хранит схемы и прогресс достижений (appcache/stats/*.bin).

Формат: [тип: 1 байт][ключ: строка с NUL на конце][значение]; объект заканчивается байтом 0x08.
"""
from __future__ import annotations

import struct

_OBJECT = 0x00
_STRING = 0x01
_END = (0x08, 0x0B)  # 0x0B встречается как альтернативный конец объекта в других бинарных VDF

# Числовые типы: 0x02 int32, 0x03 float, 0x04 цвет (uint32), 0x06 указатель (uint32),
# 0x07 uint64, 0x0A int64
_NUMBERS = {
    0x02: struct.Struct("<i"),
    0x03: struct.Struct("<f"),
    0x04: struct.Struct("<I"),
    0x06: struct.Struct("<I"),
    0x07: struct.Struct("<Q"),
    0x0A: struct.Struct("<q"),
}


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Изредка разработчики пишут строки в latin-1 (итальянское имя в app 8850):
        # строгий UTF-8 на них падает, а cp1252 даёт правильные буквы
        return raw.decode("cp1252", "replace")


def parse_binary_vdf(data: bytes) -> dict:
    """bytes → вложенные dict. На обрезанных или битых данных — ValueError.

    Без рекурсии: битый файл с огромной вложенностью не должен уронить программу RecursionError.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("binary VDF: expected bytes")
    data = bytes(data)
    size = len(data)
    root: dict = {}
    stack: list[dict] = [root]
    # Ключи сильно повторяются ("display", "name", "english"…): декодируем каждый один раз
    keys: dict[bytes, str] = {}
    pos = 0
    try:
        while True:
            if pos >= size:
                # Без завершающего 0x08 допустим только корень: у некоторых файлов Valve его нет
                if len(stack) == 1:
                    return root
                raise ValueError("binary VDF: unexpected end of data")
            kind_at = pos
            kind = data[pos]
            pos += 1
            if kind in _END:
                stack.pop()
                if not stack:
                    return root
                continue

            end = data.find(b"\0", pos)
            if end < 0:
                raise ValueError("binary VDF: unterminated key")
            raw_key = data[pos:end]
            key = keys.get(raw_key)
            if key is None:
                key = keys[raw_key] = _decode(raw_key)
            pos = end + 1

            node = stack[-1]
            if kind == _OBJECT:
                child: dict = {}
                node[key] = child
                stack.append(child)
            elif kind == _STRING:
                end = data.find(b"\0", pos)
                if end < 0:
                    raise ValueError("binary VDF: unterminated string")
                node[key] = _decode(data[pos:end])
                pos = end + 1
            else:
                number = _NUMBERS.get(kind)
                if number is None:
                    raise ValueError(f"binary VDF: unknown type {kind:#04x} at offset {kind_at}")
                node[key] = number.unpack_from(data, pos)[0]
                pos += number.size
    except struct.error as exc:  # число обрезано концом файла
        raise ValueError(f"binary VDF: truncated value at offset {pos}") from exc
