"""Блок «Достижения» в окне игры: прогресс, список с иконками, датами и редкостью."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from .. import i18n
from ..achievements.models import Achievement, AchievementSet
from ..context import ctx
from ..i18n import tr
from ..models import Game
from ..theme import C
from ..timefmt import relative_day
from .widgets import ElidedLabel, WrapLabel, hbox

ICON_SIZE = 48
FIRST_ROWS = 12  # длинные списки (у некоторых игр сотни достижений) раскрываем порциями по кнопке
MORE_ROWS = 50


def rarity_color(rarity: float) -> str:
    if rarity < 5:
        return C["star"]          # редкое — золото
    if rarity < 20:
        return C["primary_light"]
    return C["text_dim"]


def unlock_date(achievement: Achievement) -> str:
    if not achievement.unlocked_at:
        return tr("получено")
    moment = datetime.fromtimestamp(achievement.unlocked_at)
    # «сегодня в 19:40», «вчера в 22:15», «12 окт.», «12 окт. 2025» — время только у сегодня и вчера
    text = relative_day(moment.isoformat(timespec="seconds"))
    return ("~ " if achievement.time_approx else "") + text


def gray(pixmap: QPixmap) -> QPixmap:
    """Серая версия иконки — когда Steam не дал отдельной для неполученного достижения."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(pixmap.devicePixelRatio())
    return result


class AchievementIcon(QLabel):
    def __init__(self, achievement: Achievement, parent=None):
        super().__init__(parent)
        self.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.setStyleSheet(f"background: {C['surface_alt']}; border-radius: 8px;")
        self._locked = achievement.unlocked is False
        url = achievement.icon if not self._locked else (achievement.icon_gray or achievement.icon)
        self._needs_gray = self._locked and not achievement.icon_gray
        if url:
            ctx.images.load(url, self._on_loaded)

    def _on_loaded(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            return
        dpr = self.devicePixelRatioF()
        scaled = pixmap.scaled(round(ICON_SIZE * dpr), round(ICON_SIZE * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.setPixmap(gray(scaled) if self._needs_gray else scaled)


class AchievementRow(QFrame):
    def __init__(self, achievement: Achievement, parent=None):
        super().__init__(parent)
        self.setObjectName("AchRow")
        locked = achievement.unlocked is False
        # Скрытое прячем всегда, пока не знаем точно, что оно получено (в том числе в игре из поиска)
        secret = achievement.hidden and achievement.unlocked is not True
        name = QLabel(tr("Скрытое достижение") if secret else achievement.name or achievement.api)
        name.setStyleSheet(f"font-weight: 600; color: {C['text_muted'] if locked else C['text']};"
                           " background: transparent;")
        # Описание в одну строку (полный текст — в подсказке): перенос строк в сотнях строк подвешивал окно
        description = ElidedLabel(tr("Подробности откроются, когда вы его получите.") if secret
                                  else achievement.description or "")
        description.setStyleSheet(f"color: {C['text_dim'] if locked else C['text_muted']}; background: transparent;")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(name)
        if description.text():
            texts.addWidget(description)
        if locked and achievement.progress and achievement.progress[1] > 0:
            current, target = achievement.progress
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(round(min(1, current / target) * 1000))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            caption = QLabel(f"{current:g} / {target:g}")
            caption.setObjectName("SessionRow")
            texts.addLayout(hbox(bar, caption, spacing=8))

        side = QVBoxLayout()
        side.setSpacing(4)
        if achievement.unlocked:
            when = QLabel(unlock_date(achievement))
            when.setStyleSheet("color: #34d399; font-weight: 600; background: transparent;")
            if achievement.time_approx:
                when.setToolTip(tr("Этот кряк записывает время с точностью до ~15 минут"))
            side.addWidget(when, 0, Qt.AlignmentFlag.AlignRight)
        if achievement.rarity is not None:
            percent = i18n.decimal(achievement.rarity, ".1f")
            rarity = QLabel(f"{percent}%")
            color = rarity_color(achievement.rarity)
            rarity.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: 8px;"
                                 " padding: 1px 7px; font-size: 8.5pt; font-weight: 600; background: transparent;")
            rarity.setToolTip(tr("Это достижение есть у {percent}% игроков Steam", percent=percent))
            side.addWidget(rarity, 0, Qt.AlignmentFlag.AlignRight)
        side.addStretch()

        layout = hbox(AchievementIcon(achievement), texts, None, side, spacing=14, margins=(12, 10, 12, 10))
        layout.setStretch(1, 1)
        self.setLayout(layout)


class AchievementsSection(QWidget):
    """Достижения игры. Для игры из библиотеки — с прогрессом, для игры из поиска — только список."""

    def __init__(self, game: Game, in_library: bool, parent=None):
        super().__init__(parent)
        self._game = game
        self._in_library = in_library
        self._filter = "all"
        self._limit = FIRST_ROWS
        self._items: list[Achievement] = []
        self._set: AchievementSet | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title = QLabel(tr("Достижения"))
        title.setObjectName("SectionTitle")
        self.counter = QLabel()
        self.counter.setObjectName("Muted")
        layout.addLayout(hbox(title, self.counter, None, spacing=12))

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)
        self.source = WrapLabel(object_name="SessionRow")
        layout.addWidget(self.source)

        self.filters = QButtonGroup(self)
        buttons = []
        for key, label in (("all", tr("Все")), ("unlocked", tr("Получены")), ("locked", tr("Осталось"))):
            button = QPushButton(label)
            button.setObjectName("StatusOption")
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("filter", key)
            self.filters.addButton(button)
            buttons.append(button)
        self.filters.buttonClicked.connect(self._on_filter)
        self.filter_row = hbox(*buttons, None, spacing=8)
        layout.addLayout(self.filter_row)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        layout.addLayout(self.rows)
        self.more = QPushButton()
        self.more.setObjectName("Link")
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.clicked.connect(self._expand)
        layout.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)

        if in_library:
            ctx.achievements.changed.connect(self._on_changed)
            self._set = ctx.achievements.get(game.key) or ctx.achievements.reload(game.key)
            self._render()
        elif game.source == "steam":
            self._show_message(tr("Загружаем достижения…"))
            ctx.achievements_web.fetch(game.id, self._on_web, lambda message: self._show_message(message))
        else:
            self._show_message("")

    # ------------------------------------------------------------------ данные

    def _on_changed(self, key: str) -> None:
        if key == self._game.key:
            self._set = ctx.achievements.get(key)
            self._render()

    def _on_web(self, items: list[Achievement]) -> None:
        self._set = AchievementSet(self._game.id, items) if items else None
        self._render()

    def _on_filter(self, button) -> None:
        self._filter = button.property("filter")
        self._limit = FIRST_ROWS
        self._render()

    def _expand(self) -> None:
        # Дописываем следующую порцию, не перестраивая уже показанные строки
        start = self._limit
        self._limit += MORE_ROWS
        for achievement in self._items[start:self._limit]:
            self.rows.addWidget(AchievementRow(achievement))
        self._update_more()

    def _update_more(self) -> None:
        rest = len(self._items) - self._limit
        self.more.setVisible(rest > 0)
        self.more.setText(tr("Показать ещё {count} (осталось {rest})", count=min(rest, MORE_ROWS), rest=rest))

    # ------------------------------------------------------------------ отображение

    def _show_message(self, text: str) -> None:
        self.setVisible(bool(text))
        self.counter.setText("")
        self.bar.hide()
        self._set_filters_visible(False)
        self.more.hide()
        self._clear_rows()
        self.source.setText(text)

    def _set_filters_visible(self, visible: bool) -> None:
        for button in self.filters.buttons():
            button.setVisible(visible)

    def _clear_rows(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render(self) -> None:
        achievement_set = self._set
        if achievement_set is None or not achievement_set.items:
            self._show_message("" if not self._in_library else
                               tr("У этой игры нет достижений — или GameShelf не нашёл её номер в Steam."))
            return
        self.show()
        known = achievement_set.progress_known
        total, unlocked = achievement_set.total, achievement_set.unlocked_count
        if known and achievement_set.complete:
            # Округляем вниз: «100%» — только когда получено действительно всё
            self.counter.setText(tr("{unlocked} из {total} · {percent}%", unlocked=unlocked, total=total,
                                    percent=unlocked * 100 // total))
            self.bar.setValue(unlocked * 1000 // total)
            self.bar.show()
        elif known:
            self.counter.setText(tr("получено {unlocked} · полный список загружается из Steam", unlocked=unlocked))
            self.bar.hide()
        else:
            self.counter.setText(tr("всего {total}", total=total))
            self.bar.hide()
        self._set_filters_visible(known)
        self.source.setText(self._source_text(achievement_set))

        self._items = self._sorted(achievement_set.items, known)
        self._clear_rows()
        for achievement in self._items[:self._limit]:
            self.rows.addWidget(AchievementRow(achievement))
        self._update_more()

    def _sorted(self, items: list[Achievement], known: bool) -> list[Achievement]:
        if known and self._filter == "unlocked":
            items = [a for a in items if a.unlocked]
        elif known and self._filter == "locked":
            items = [a for a in items if not a.unlocked]
        # Сначала полученные (свежие выше), потом остальные — от самых простых (частых) к редким
        return sorted(items, key=lambda a: (not a.unlocked, -(a.unlocked_at or 0) if a.unlocked else -(a.rarity or 0)))

    def _source_text(self, achievement_set: AchievementSet) -> str:
        if not self._in_library:
            return tr("Список из Steam. Добавьте игру в библиотеку — и GameShelf покажет ваш прогресс.")
        if not achievement_set.progress_known:
            return tr("Прогресс не найден: игра не запускалась через Steam на этом компьютере, "
                      "а файлов кряка с достижениями нет.")
        if achievement_set.updated_at:
            moment = datetime.fromtimestamp(achievement_set.updated_at).isoformat(timespec="seconds")
            text = tr("Прогресс: {source} · обновлён {when}", source=achievement_set.source, when=relative_day(moment))
        else:
            text = tr("Прогресс: {source}", source=achievement_set.source)
        if achievement_set.source.startswith("Steam"):
            text = tr("{text}. Steam сохраняет прогресс на диск при выходе из Steam — "
                      "самое свежее может появиться позже.", text=text)
        return text
