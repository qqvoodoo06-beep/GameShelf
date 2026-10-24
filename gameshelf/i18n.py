"""Язык интерфейса: русский или английский.

Тексты пишутся в коде по-русски и оборачиваются в tr(): tr("Играю"), tr("Наиграно {hours}", hours=...).
Для русского tr() возвращает текст как есть, для английского ищет перевод в locales/en.py (ключ — русский
текст целиком). Числа с существительными — ntr(): ntr(5, ("{n} игра", "{n} игры", "{n} игр")).

Язык выбирается один раз при запуске — init() в main.py вызывается раньше, чем импортируются модули
с текстами (часть текстов — константы модулей). Смена языка в настройках действует после перезапуска.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LANGUAGE_KEY = "language"           # в settings.json: "auto" | "ru" | "en"
# Названия языков всегда на своём языке — чтобы найти свой, даже если интерфейс на чужом
LANGUAGES = {"ru": "Русский", "en": "English"}
# Системные языки, для которых «как в системе» — русский интерфейс; для остальных — английский
RUSSIAN_SPEAKING = {"ru", "be", "kk"}

_lang = "ru"
_texts: dict[str, str] = {}
_plurals: dict[str, tuple[str, str]] = {}


def _settings_path() -> Path:
    """settings.json без Qt: init() вызывается до создания приложения (тот же путь, что config.data_dir())."""
    override = os.environ.get("GAMESHELF_DATA_DIR")
    folder = Path(override) if override else Path(os.environ.get("APPDATA") or Path.home()) / "GameShelf"
    return folder / "settings.json"


def saved_choice() -> str:
    try:
        choice = json.loads(_settings_path().read_text(encoding="utf-8")).get(LANGUAGE_KEY, "auto")
    except (OSError, ValueError, AttributeError):
        return "auto"
    # Файл могли поправить руками: не строка (список, словарь) — как будто языка не выбирали
    return choice if isinstance(choice, str) and (choice in LANGUAGES or choice == "auto") else "auto"


def system_language() -> str:
    """Язык Windows (интерфейса, а не региональных форматов): "ru" или "en"."""
    try:
        from PySide6.QtCore import QLocale
        languages = QLocale.system().uiLanguages() or [QLocale.system().name()]
        code = languages[0].replace("_", "-").split("-")[0].lower()
    except (ImportError, IndexError, RuntimeError):
        return "ru"
    return "ru" if code in RUSSIAN_SPEAKING else "en"


def resolve(choice: str) -> str:
    return choice if choice in LANGUAGES else system_language()


def _catalog(language: str) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    if language == "en":
        from .locales import en
        return en.TEXTS, en.PLURALS
    return {}, {}


def init(choice: str | None = None) -> str:
    """Выбирает язык: choice — "ru" / "en" / "auto"; None — из настроек. Возвращает выбранный язык."""
    global _lang, _texts, _plurals
    _lang = resolve(saved_choice() if choice is None else choice)
    _texts, _plurals = _catalog(_lang)
    return _lang


def lang() -> str:
    return _lang


def tr(text: str, /, **values) -> str:
    """Перевод текста; values подставляются в {имена} уже после перевода."""
    result = _texts.get(text, text)
    return result.format(**values) if values else result


def tr_for(language: str, text: str, /, **values) -> str:
    """Перевод на указанный язык, а не на текущий — для вопроса «перезапустить?» на только что выбранном языке."""
    result = _catalog(language)[0].get(text, text)
    return result.format(**values) if values else result


def _russian_form(n: int) -> int:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return 2
    n %= 10
    return 0 if n == 1 else 1 if 2 <= n <= 4 else 2


def ntr(count: int, forms: tuple[str, str, str], /, **values) -> str:
    """Число с существительным в нужной форме. forms — русские формы для 1, 2 и 5 («{n} игра», «{n} игры»,
    «{n} игр»); перевод ищется по первой форме. {n} — само число, его можно заменить: n="1 234"."""
    values.setdefault("n", count)
    english = _plurals.get(forms[0]) if _plurals else None
    if english:
        text = english[0 if abs(count) == 1 else 1]
    else:
        text = forms[_russian_form(count)]
    return text.format(**values)


def decimal(value: float, spec: str = "g") -> str:
    """Дробное число по правилам языка: «2,5» по-русски, «2.5» по-английски."""
    text = format(value, spec)
    return text.replace(".", ",") if _lang == "ru" else text


def integer(n: int) -> str:
    """Целое с разделителем тысяч: «1 234» по-русски, «1,234» по-английски."""
    text = f"{n:,}"
    return text.replace(",", " ") if _lang == "ru" else text


def steam_language() -> str:
    """Язык для запросов к Steam: названия, описания и достижения приходят на языке интерфейса."""
    return "russian" if _lang == "ru" else "english"
