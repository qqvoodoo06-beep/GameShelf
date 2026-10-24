"""Человеческие подписи для времени: «2 ч 35 мин», «вчера в 22:15»."""
from __future__ import annotations

from datetime import date, datetime

from . import i18n
from .i18n import tr

MONTHS_SHORT = [tr("янв."), tr("февр."), tr("мар."), tr("апр."), tr("мая"), tr("июня"), tr("июля"), tr("авг."),
                tr("сент."), tr("окт."), tr("нояб."), tr("дек.")]


def format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return tr("меньше минуты")
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return tr("{minutes} мин", minutes=minutes)
    return tr("{hours} ч {minutes} мин", hours=hours, minutes=minutes) if minutes else tr("{hours} ч", hours=hours)


def format_hours(hours: float) -> str:
    """Часы для карточек: «12 ч», «2,5 ч», «40 мин»."""
    minutes = round(hours * 60)
    if 0 < hours and minutes < 60:
        return tr("{minutes} мин", minutes=max(1, minutes))  # 59,7 минуты — это уже «1 ч», а не «60 мин»
    value = round(hours, 1)
    return tr("{hours} ч", hours=i18n.decimal(value, "g"))


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def relative_day(value: str, with_time: bool = True) -> str:
    """«сегодня в 19:40», «вчера в 22:15», «12 окт.», «12 окт. 2025»."""
    moment = parse_iso(value)
    if moment is None:
        return ""
    days = (date.today() - moment.date()).days
    time = f"{moment:%H:%M}"
    if days == 0:
        return tr("сегодня в {time}", time=time) if with_time else tr("сегодня")
    if days == 1:
        return tr("вчера в {time}", time=time) if with_time else tr("вчера")
    month = MONTHS_SHORT[moment.month - 1]
    if moment.year != date.today().year:
        return tr("{day} {month} {year}", day=moment.day, month=month, year=moment.year)
    return tr("{day} {month}", day=moment.day, month=month)
