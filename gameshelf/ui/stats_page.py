from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from .. import i18n
from ..context import ctx
from ..i18n import tr
from ..models import Session, Status
from ..theme import C
from ..timefmt import MONTHS_SHORT, format_duration, parse_iso
from .widgets import EmptyState, IconBadge, WrapLabel, games_count_text, hbox, make_scroll_area, page_header

WEEKS = 12


class WeeklyChart(QWidget):
    """Столбики «сколько часов наиграно» по неделям за последние 12 недель."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hours = [0.0] * WEEKS
        self._first_week = date.today()
        self.setMinimumHeight(210)

    def set_sessions(self, sessions: list[Session], first_week: date) -> None:
        self._first_week = first_week
        self._hours = [0.0] * WEEKS
        for session in sessions:
            started = parse_iso(session.started_at)
            if started is None:
                continue
            week = (started.date() - first_week).days // 7
            if 0 <= week < WEEKS:
                self._hours[week] += session.seconds / 3600
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        top = max(self._hours)
        if top <= 0:
            painter.setPen(QColor(C["text_dim"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             tr("Сеансов пока нет. Запустите игру из библиотеки —\nи здесь появятся часы по неделям."))
            return
        label_h, value_h = 22, 18
        chart_h = h - label_h - value_h
        slot = w / WEEKS
        bar_w = min(34.0, slot * 0.6)
        small = QFont(self.font())
        small.setPointSizeF(8)
        painter.setFont(small)
        for i, hours in enumerate(self._hours):
            x = i * slot + (slot - bar_w) / 2
            bar_h = max(3.0, chart_h * hours / top) if hours > 0 else 3.0
            rect = QRectF(x, value_h + chart_h - bar_h, bar_w, bar_h)
            if hours > 0:
                gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                gradient.setColorAt(0, QColor(C["accent"]))
                gradient.setColorAt(1, QColor(C["primary"]))
                painter.setBrush(gradient)
            else:
                painter.setBrush(QColor(C["border"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 5, 5)
            if hours >= 0.05:  # подпись «0,0» у недели с парой минут только путает
                painter.setPen(QColor(C["text_muted"]))
                text = i18n.decimal(hours, ".1f") if hours < 10 else f"{hours:.0f}"
                painter.drawText(QRectF(i * slot, rect.top() - value_h, slot, value_h - 2),
                                 Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)
            week_start = self._first_week + timedelta(days=7 * i)
            painter.setPen(QColor(C["text_dim"]))
            painter.drawText(QRectF(i * slot, h - label_h, slot, label_h), Qt.AlignmentFlag.AlignCenter,
                             tr("{day} {month}", day=week_start.day, month=MONTHS_SHORT[week_start.month - 1]))


class PlaytimeRecords(QWidget):
    """Итоги учёта времени: за неделю, самый долгий сеанс, любимая игра месяца."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self._rows: list[tuple[QLabel, WrapLabel]] = []
        for caption in (tr("За последние 7 дней"), tr("Самый долгий сеанс"), tr("Больше всего за 30 дней")):
            key = QLabel(caption.upper())
            key.setObjectName("Caption")
            value = WrapLabel()
            value.setStyleSheet("font-size: 11pt; font-weight: 600;")
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(key)
            box.addWidget(value)
            layout.addLayout(box)
            self._rows.append((key, value))
        layout.addStretch()

    def set_data(self, sessions: list[Session], names: dict[str, str]) -> None:
        now = datetime.now()
        week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
        month_ago = (now - timedelta(days=30)).isoformat(timespec="seconds")
        week = sum(s.seconds for s in sessions if s.started_at >= week_ago)
        values = [format_duration(week) if week else "—"]

        if sessions:
            longest = max(sessions, key=lambda s: s.seconds)
            values.append(f"{format_duration(longest.seconds)}\n{names.get(longest.game_key, tr('игра удалена'))}")
        else:
            values.append("—")

        per_game: dict[str, int] = defaultdict(int)
        for s in sessions:
            if s.started_at >= month_ago:
                per_game[s.game_key] += s.seconds
        if per_game:
            key, seconds = max(per_game.items(), key=lambda item: item[1])
            values.append(f"{names.get(key, tr('игра удалена'))}\n{format_duration(seconds)}")
        else:
            values.append("—")

        for (_, label), value in zip(self._rows, values):
            label.setText(value)


class StatTile(QFrame):
    def __init__(self, icon_name: str, color: str, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.value = QLabel("0")
        self.value.setObjectName("StatValue")
        caption_label = QLabel(caption)
        caption_label.setObjectName("Muted")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.value)
        texts.addWidget(caption_label)
        layout = hbox(IconBadge(icon_name, color, 48, 24), texts, None, spacing=16, margins=(20, 18, 20, 18))
        self.setLayout(layout)


class StatusBreakdown(QWidget):
    """Полоса из цветных сегментов по статусам + легенда."""

    ROW = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: dict[Status, int] = {}
        self.setMinimumHeight(14 + 22 + self.ROW * len(Status))

    def set_counts(self, counts: dict[Status, int]) -> None:
        self._counts = counts
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        total = sum(self._counts.get(s, 0) for s in Status) or 1

        bar = QRectF(0, 0, w, 14)
        clip = QPainterPath()
        clip.addRoundedRect(bar, 7, 7)
        painter.setClipPath(clip)
        painter.fillRect(bar, QColor(C["border"]))
        x = 0.0
        for status in Status:
            seg = w * self._counts.get(status, 0) / total
            if seg > 0:
                painter.fillRect(QRectF(x, 0, seg + 0.5, 14), QColor(status.color))
                x += seg
        painter.setClipping(False)

        font = QFont(self.font())
        bold = QFont(font)
        bold.setWeight(QFont.Weight.DemiBold)
        y = 14 + 22
        for status in Status:
            count = self._counts.get(status, 0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(status.color))
            painter.drawEllipse(QRectF(2, y + 9, 10, 10))
            painter.setFont(font)
            painter.setPen(QColor(C["text"]))
            painter.drawText(QRectF(24, y, w - 140, self.ROW - 2), Qt.AlignmentFlag.AlignVCenter, status.label)
            painter.setPen(QColor(C["text_dim"]))
            painter.drawText(QRectF(w - 120, y, 64, self.ROW - 2),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{count * 100 / total:.0f}%")
            painter.setFont(bold)
            painter.setPen(QColor(C["text"]))
            painter.drawText(QRectF(w - 48, y, 48, self.ROW - 2),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(count))
            y += self.ROW


class BarList(QWidget):
    """Горизонтальные столбики: «жанр — количество»."""

    ROW = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, int]] = []

    def set_rows(self, rows: list[tuple[str, int]]) -> None:
        self._rows = rows
        self.setMinimumHeight(max(1, len(rows)) * self.ROW)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows:
            painter.setPen(QColor(C["text_dim"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             tr("Жанры появятся, когда вы добавите игры"))
            return
        w = self.width()
        label_w = 130
        bar_x = label_w + 12
        bar_w = max(10, w - bar_x - 44)
        top = max(value for _, value in self._rows)
        for i, (label, value) in enumerate(self._rows):
            y = i * self.ROW
            painter.setPen(QColor(C["text"]))
            text = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, label_w)
            painter.drawText(QRectF(0, y, label_w, self.ROW), Qt.AlignmentFlag.AlignVCenter, text)
            track = QRectF(bar_x, y + self.ROW / 2 - 5, bar_w, 10)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(C["border"]))
            painter.drawRoundedRect(track, 5, 5)
            fill = QRectF(track.x(), track.y(), max(10, bar_w * value / top), 10)
            gradient = QLinearGradient(fill.topLeft(), fill.topRight())
            gradient.setColorAt(0, QColor(C["primary"]))
            gradient.setColorAt(1, QColor(C["accent"]))
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill, 5, 5)
            painter.setPen(QColor(C["text_muted"]))
            painter.drawText(QRectF(w - 36, y, 36, self.ROW),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(value))


def panel(title: str, widget: QWidget) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 22)
    layout.setSpacing(16)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    layout.addWidget(widget)
    layout.addStretch()
    return frame


class StatsPage(QWidget):
    go_search = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dirty = True
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 24, 0)
        root.setSpacing(22)
        titles, _, self.subtitle = page_header(tr("Статистика"))
        root.addLayout(titles)

        self.stack = QStackedWidget()
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 8, 32)
        grid.setSpacing(18)
        self.tile_total = StatTile("gamepad", C["primary_light"], tr("игр в библиотеке"))
        self.tile_completed = StatTile("trophy", "#34d399", tr("пройдено"))
        self.tile_hours = StatTile("clock", "#60a5fa", tr("часов в играх"))
        self.tile_rating = StatTile("star", C["star"], tr("средняя оценка"))
        for col, tile in enumerate((self.tile_total, self.tile_completed, self.tile_hours, self.tile_rating)):
            grid.addWidget(tile, 0, col)
        self.breakdown = StatusBreakdown()
        self.genres = BarList()
        grid.addWidget(panel(tr("По статусам"), self.breakdown), 1, 0, 1, 2)
        grid.addWidget(panel(tr("Любимые жанры"), self.genres), 1, 2, 1, 2)
        self.weekly = WeeklyChart()
        self.records = PlaytimeRecords()
        grid.addWidget(panel(tr("Время в играх по неделям"), self.weekly), 2, 0, 1, 3)
        grid.addWidget(panel(tr("Рекорды"), self.records), 2, 3)
        grid.setRowStretch(3, 1)
        self.stack.addWidget(make_scroll_area(content))

        self.empty = EmptyState(
            "stats", tr("Здесь появится статистика"),
            tr("Добавьте несколько игр в библиотеку — и вы увидите, сколько прошли, сколько часов наиграли "
               "и какие жанры любите больше всего."),
            tr("Найти игры"),
        )
        self.empty.action.connect(self.go_search)
        self.stack.addWidget(self.empty)
        root.addWidget(self.stack, 1)

        ctx.library.changed.connect(self._on_changed)
        ctx.library.playtime_changed.connect(self._on_changed)

    def _on_changed(self, *_args) -> None:
        if self.isVisible():
            self.refresh()
        else:
            self._dirty = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        games = ctx.library.games()
        counts = ctx.library.counts()
        self.subtitle.setText(tr("Ваша библиотека в цифрах · {games}", games=games_count_text(len(games))))
        if not games:
            self.stack.setCurrentIndex(1)
            return
        rated = [g.user_rating for g in games if g.user_rating]
        hours = sum(g.hours for g in games)
        self.tile_total.value.setText(str(len(games)))
        self.tile_completed.value.setText(str(counts[Status.COMPLETED]))
        self.tile_hours.value.setText(i18n.integer(round(hours)))
        self.tile_rating.value.setText(f"{sum(rated) / len(rated):.1f}" if rated else "—")
        self.breakdown.set_counts({s: counts[s] for s in Status})
        genres = Counter(genre for g in games for genre in g.genres)
        self.genres.set_rows(genres.most_common(7))

        today = date.today()
        first_week = today - timedelta(days=today.weekday() + 7 * (WEEKS - 1))
        sessions = ctx.library.sessions(since=first_week.isoformat())
        self.weekly.set_sessions(sessions, first_week)
        self.records.set_data(ctx.library.sessions(), {g.key: g.name for g in games})
        self.stack.setCurrentIndex(0)
