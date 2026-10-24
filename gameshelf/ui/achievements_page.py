"""Страница «Достижения»: достижения GameShelf, недавно полученные в играх, прогресс по играм."""
from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget

from .. import i18n, icons
from ..achievements.meta import MetaAchievement
from ..context import ctx
from ..i18n import tr
from ..theme import C
from ..timefmt import relative_day
from .achievements_section import AchievementIcon, rarity_color, unlock_date
from .widgets import CardGrid, EmptyState, IconBadge, WrapLabel, hbox, make_scroll_area, page_header

RECENT = 8


def _number(value: float) -> str:
    """Округляем вниз, с запятой: 3,9 из 4, а не «3.99972», и не «100 / 100», пока цель не достигнута."""
    if value >= 10:
        return i18n.integer(math.floor(value))
    return i18n.decimal(math.floor(value * 10) / 10)


class MetaTile(QFrame):
    """Плитка достижения GameShelf: иконка, название, условие и прогресс."""

    HEIGHT = 156

    def __init__(self, achievement: MetaAchievement, parent=None):
        super().__init__(parent)
        definition = achievement.definition
        self.setObjectName("MetaTile")
        self.setProperty("unlocked", "true" if achievement.unlocked else "false")
        color = definition.color if achievement.unlocked else C["text_dim"]
        badge = IconBadge(definition.icon, color, 44, 22)
        name = QLabel(definition.name)
        name.setStyleSheet(f"font-weight: 700; font-size: 11pt; color: {C['text'] if achievement.unlocked else C['text_muted']};"
                           " background: transparent;")
        description = WrapLabel(definition.description)
        description.setStyleSheet(f"color: {C['text_muted']}; background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        layout.addLayout(hbox(badge, name, None, spacing=12))
        layout.addWidget(description)
        layout.addStretch()
        if achievement.unlocked:
            when = QLabel(tr("Получено {when}", when=relative_day(achievement.unlocked_at, with_time=False)))
            when.setStyleSheet(f"color: {definition.color}; font-weight: 600; background: transparent;")
            layout.addWidget(when)
        else:
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(round(achievement.progress * 1000))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            # По-английски «0 / 1 games» неграмотно, а единица там во множественном числе — при цели 1 её не пишем
            english_one = definition.target == 1 and i18n.lang() == "en"
            unit = f" {definition.unit}" if definition.unit and not english_one else ""
            caption = QLabel(f"{_number(min(achievement.value, definition.target))} / {_number(definition.target)}{unit}")
            caption.setObjectName("SessionRow")
            layout.addLayout(hbox(bar, caption, spacing=10))

    def height_for(self, _width: int) -> int:
        return self.HEIGHT


class ClickableRow(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AchRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        inside = event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if inside:
            self.clicked.emit()


class AchievementsPage(QWidget):
    open_game = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dirty = True
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 24, 0)
        root.setSpacing(18)
        titles, _, self.subtitle = page_header(tr("Достижения"))
        root.addLayout(titles)

        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 8, 32)
        self.body.setSpacing(14)
        root.addWidget(make_scroll_area(content), 1)

        self._refresh_timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        ctx.achievements.summary_changed.connect(self._on_changed)
        ctx.achievements.changed.connect(self._on_changed)

    def _on_changed(self, *_args) -> None:
        if self.isVisible():
            self._refresh_timer.start()  # пачку изменений при запуске — одной перестройкой
        else:
            self._dirty = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    # ------------------------------------------------------------------ построение

    def _clear(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def _section(self, title: str, hint: str = "") -> None:
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        note = QLabel(hint)
        note.setObjectName("SectionHint")
        self.body.addSpacing(6)
        self.body.addLayout(hbox(label, note, None, spacing=10))

    def refresh(self) -> None:
        self._dirty = False
        self._clear()
        service = ctx.achievements
        meta = service.meta()
        sets = service.sets()
        games = {g.key: g for g in ctx.library.games()}
        tracked = {key: s for key, s in sets.items() if s.progress_known and key in games}
        unlocked_total = service.trophy_stats().unlocked  # одна игра из двух каталогов не считается дважды
        meta_done = sum(1 for m in meta if m.unlocked)
        self.subtitle.setText(tr("В играх получено {unlocked} · достижений GameShelf: {done} из {total}",
                                 unlocked=unlocked_total, done=meta_done, total=len(meta)))

        # --- свои достижения: сначала полученные (свежие выше), потом ближайшие к получению
        self._section(tr("Достижения GameShelf"), tr("за вашу библиотеку и привычки"))
        grid = CardGrid(250)
        done = sorted((m for m in meta if m.unlocked), key=lambda m: m.unlocked_at, reverse=True)
        rest = sorted((m for m in meta if not m.unlocked), key=lambda m: -m.progress)
        for achievement in done + rest:
            grid.add(MetaTile(achievement))
        grid.finish()
        self.body.addWidget(grid)

        # --- недавно полученные в играх
        recent = service.recent_unlocks(RECENT)
        rarest = service.rarest_unlocked()
        rarest_hint = ""
        if rarest:
            percent = i18n.decimal(rarest[1].rarity, ".1f")  # только в числе: в названии точки — часть текста
            rarest_hint = tr("самое редкое: «{name}» — {percent}%", name=rarest[1].name, percent=percent)
        self._section(tr("Недавно получены в играх"), rarest_hint)
        if recent:
            for key, achievement in recent:
                self.body.addWidget(self._recent_row(games.get(key), achievement))
        else:
            self.body.addWidget(self._note(tr("Здесь появятся достижения из игр, как только GameShelf их увидит.")))

        # --- прогресс по играм
        self._section(tr("По играм"))
        if not tracked:
            self.body.addWidget(EmptyState(
                "trophy", tr("Пока нет достижений из игр"),
                tr("GameShelf читает достижения игр из Steam и из файлов кряков. "
                   "Добавьте игру в библиотеку и запустите её — прогресс появится сам.")))
        for key, achievement_set in sorted(tracked.items(), key=lambda item: (
                -item[1].unlocked_count / max(1, item[1].total), games[item[0]].name.lower())):
            self.body.addWidget(self._game_row(games[key], achievement_set))
        self.body.addStretch()

    def _note(self, text: str) -> QLabel:
        label = WrapLabel(text, object_name="Muted")
        return label

    def _recent_row(self, game, achievement) -> QFrame:
        row = ClickableRow()
        name = QLabel(achievement.name or achievement.api)
        name.setStyleSheet("font-weight: 600; background: transparent;")
        where = QLabel(game.name if game else "")
        where.setObjectName("SessionRow")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(name)
        texts.addWidget(where)
        when = QLabel(unlock_date(achievement))
        when.setStyleSheet("color: #34d399; font-weight: 600; background: transparent;")
        side = [when]
        if achievement.rarity is not None:
            rarity = QLabel(f"{i18n.decimal(achievement.rarity, '.1f')}%")
            rarity.setStyleSheet(f"color: {rarity_color(achievement.rarity)}; font-weight: 600; background: transparent;")
            side.append(rarity)
        layout = hbox(AchievementIcon(achievement), texts, None, *side, spacing=14, margins=(12, 8, 14, 8))
        layout.setStretch(1, 1)
        row.setLayout(layout)
        if game:
            row.clicked.connect(lambda g=game: self.open_game.emit(g))
        return row

    def _game_row(self, game, achievement_set) -> QFrame:
        row = ClickableRow()
        total, unlocked = achievement_set.total, achievement_set.unlocked_count
        perfect = achievement_set.complete and unlocked == total
        name = QLabel(game.name)
        name.setStyleSheet("font-weight: 600; background: transparent;")
        source = QLabel(achievement_set.source)
        source.setObjectName("SessionRow")
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(round(unlocked * 1000 / max(1, total)))
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        count = QLabel(f"{unlocked} / {total}" if achievement_set.complete else tr("получено {unlocked}", unlocked=unlocked))
        count.setStyleSheet(f"color: {C['star'] if perfect else C['text_muted']}; font-weight: 600;"
                            " background: transparent;")
        trophy = QLabel()
        trophy.setPixmap(icons.pixmap("trophy", C["star"] if perfect else C["text_dim"], 18))
        trophy.setStyleSheet("background: transparent;")
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addLayout(hbox(name, source, None, spacing=10))
        texts.addWidget(bar)
        layout = hbox(trophy, texts, count, spacing=14, margins=(14, 10, 14, 10))
        layout.setStretch(1, 1)
        row.setLayout(layout)
        row.clicked.connect(lambda g=game: self.open_game.emit(g))
        if achievement_set.updated_at:
            updated = datetime.fromtimestamp(achievement_set.updated_at).isoformat(timespec="seconds")
            row.setToolTip(tr("Прогресс обновлён {when}", when=relative_day(updated)))
        return row
