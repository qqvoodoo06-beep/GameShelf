from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget

from .. import icons
from ..context import ctx
from ..i18n import tr
from ..models import Game, Status
from ..timefmt import format_hours, relative_day
from .widgets import CoverImage, ElidedLabel, ScoreLabel, StarRating, StatusPill

CARD_MIN_WIDTH = 176
COVER_RATIO = 1.5   # вертикальная обложка 2:3, как у коробки с игрой


class TrophyBadge(QWidget):
    """«🏆 12/39» на карточке. Золотой, когда собраны все достижения."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._icon = QLabel()
        self._text = QLabel()
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.hide()

    def set_set(self, achievement_set) -> None:
        if achievement_set is None or not achievement_set.total:
            self.hide()
            return
        total, unlocked = achievement_set.total, achievement_set.unlocked_count
        known, complete = achievement_set.progress_known, achievement_set.complete
        perfect = known and complete and unlocked == total
        color = "#fbbf24" if perfect else "#a59bc4"
        self._icon.setPixmap(icons.pixmap("trophy", color, 13))
        if known and complete:
            text, tip = f"{unlocked}/{total}", tr("Достижения: получено {unlocked} из {total}",
                                                  unlocked=unlocked, total=total)
        elif known:  # у кряков в файле только полученные — общего числа без списка из Steam не знаем
            text, tip = str(unlocked), tr("Получено достижений: {unlocked}. Полный список ещё загружается из Steam",
                                          unlocked=unlocked)
        else:
            text, tip = str(total), tr("Достижений в игре: {total}. Прогресс не найден", total=total)
        self._text.setText(text)
        self._text.setStyleSheet(f"color: {color}; font-size: 8.5pt; font-weight: 600;")
        self.setToolTip(tip)
        self.show()


class HoursPill(QFrame):
    """Часы в углу обложки — видны всегда, а не обрезаются в строке под названием.
    Зелёные часы — время считается само; серые — нет (файл игры не найден или учёт выключен);
    жёлтый значок — игра в «Играю», а время не считается: стоит указать файл игры."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HoursPill")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 3, 9, 3)
        layout.setSpacing(5)
        self._icon = QLabel()
        self._text = QLabel()
        for label in (self._icon, self._text):
            label.setStyleSheet("background: transparent; border: none;")
            layout.addWidget(label)
        self.hide()

    def set_game(self, game: Game) -> None:
        hours = format_hours(game.hours) if game.hours else ""
        tracking = ctx.settings.tracking_enabled
        # Файл, указанный вручную, потом перенесли или удалили — такую привязку поиск игр сам не обновляет
        missing = tracking and game.install_source == "manual" and not os.path.isfile(game.exe_path)
        # Игра в «Играю», а GameShelf её не нашёл (и пользователь сам не отключал учёт) — подсказываем
        warn = tracking and (missing or (game.status == Status.PLAYING and game.install_source == ""))
        if not tracking and (hours or game.is_linked):
            icon, color, text = "clock", "#a59bc4", hours
            tip = tr("Учёт времени выключен в настройках")
        elif warn:
            icon, color = "alert", "#fbbf24"
            text = hours or tr("не считается")
            tip = (tr("Время не считается: файл игры не найден — его перенесли или удалили.\n"
                      "Откройте игру и укажите файл заново.") if missing else
                   tr("Время не считается: GameShelf не нашёл игру на компьютере.\n"
                      "Откройте игру и нажмите «Указать файл игры» — или перетащите в её окно .exe или ярлык.\n"
                      "Играете не на этом компьютере — нажмите там же «Не считать время»."))
        elif game.is_linked:
            icon, color, text = "clock", "#34d399", hours
            tip = tr("Время считается само") if hours else tr("Время считается само — запустите игру")
        elif hours:
            icon, color, text = "clock", "#a59bc4", hours
            tip = (tr("Учёт времени для этой игры выключен") if game.install_source == "none"
                   else tr("Время не считается: файл игры не указан"))
        else:
            self.hide()
            return
        if hours:
            tip = tr("Наиграно {hours}. {tip}", hours=hours, tip=tip)
        if game.last_played:
            tip += "\n" + tr("Последний раз: {when}", when=relative_day(game.last_played))
        self._icon.setPixmap(icons.pixmap(icon, color, 13))
        self._text.setText(text)
        self._text.setVisible(bool(text))
        self._text.setStyleSheet("background: transparent; border: none; color: #f1edff;"
                                 " font-size: 8.5pt; font-weight: 600;")
        border = "rgba(251, 191, 36, 150)" if warn else "rgba(255, 255, 255, 40)"
        self.setStyleSheet(f"#HoursPill {{ background: rgba(14, 10, 24, 215); border: 1px solid {border};"
                           f" border-radius: 10px; }}")
        self.setToolTip(tip)
        self.adjustSize()
        self.show()


def card_meta(game: Game) -> str:
    parts = []
    if game.year:
        parts.append(game.year)
    if game.genres:
        parts.append(", ".join(game.genres[:2]))
    return "  ·  ".join(parts) or tr("Нет данных")


def status_menu(parent: QWidget, current: Status | None = None) -> QMenu:
    """Меню со списком статусов. У каждого действия data() = Status."""
    menu = QMenu(parent)
    for status in Status:
        action = menu.addAction(icons.icon(status.icon, status.color, 16), status.label)
        action.setData(status.value)
        if status == current:
            action.setCheckable(True)
            action.setChecked(True)
    return menu


class GameCard(QFrame):
    """Карточка игры в сетке. mode="library" — своя библиотека, "search" — результаты поиска."""

    clicked = Signal(object)
    add_requested = Signal(object, object)
    context_requested = Signal(object, QPoint)

    BODY_HEIGHT = 82

    def __init__(self, game: Game, mode: str = "library", parent=None):
        super().__init__(parent)
        self.game = game
        self.mode = mode
        self.setObjectName("GameCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Обложка и текст расставляются вручную в resizeEvent: так высота карточки
        # зависит только от ширины, и сетка не пересчитывается по кругу.
        self.cover = CoverImage(self, radius=13, round_bottom=False, request_width=420)
        self.cover.set_url(game.cover_url)
        self.pill = StatusPill(parent=self.cover, overlay=True)
        self.hours_pill = HoursPill(self.cover) if mode == "library" else None

        body = self.body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 12, 11)
        body_layout.setSpacing(2)

        self.title = ElidedLabel(game.name)
        self.title.setObjectName("CardTitle")
        self.meta = ElidedLabel(card_meta(game))
        self.meta.setObjectName("CardMeta")
        body_layout.addWidget(self.title)
        body_layout.addWidget(self.meta)
        body_layout.addStretch()

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.stars = StarRating(game.user_rating, size=13, editable=False)
        self.score = ScoreLabel()
        self.trophies = TrophyBadge()
        footer.addWidget(self.stars)
        footer.addWidget(self.score)
        footer.addStretch()
        footer.addWidget(self.trophies)
        body_layout.addLayout(footer)

        self.add_button = None
        if mode == "search":
            self.add_button = QToolButton(self.cover)
            self.add_button.setObjectName("CardAdd")
            self.add_button.setIcon(icons.icon("plus", "#ffffff", 18))
            self.add_button.setFixedSize(34, 34)
            self.add_button.setToolTip(tr("Добавить в библиотеку"))
            self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.add_button.clicked.connect(self._show_add_menu)
            self.add_button.hide()

        self.refresh_state()

    @staticmethod
    def cover_height(width: int) -> int:
        return round((width - 2) * COVER_RATIO)

    def height_for(self, width: int) -> int:
        return self.cover_height(width) + self.BODY_HEIGHT + 2

    def refresh_state(self) -> None:
        """Обновляет статус (например, после добавления игры из поиска)."""
        if self.mode == "library":
            if ctx.tracker.is_active(self.game.key):
                self.pill.set_playing()
            else:
                self.pill.set_status(self.game.status)
            # Своя оценка важнее оценки игроков, поэтому показываем что-то одно
            self.stars.setVisible(self.game.user_rating > 0)
            self.score.set_game(self.game, visible=not self.game.user_rating)
            self.trophies.set_set(ctx.achievements.get(self.game.key))
            self.hours_pill.set_game(self.game)
            self._place_hours_pill()
            return
        self.trophies.hide()
        status = ctx.library.status_of(self.game.key)
        self.pill.set_status(status)
        self.stars.hide()
        self.score.set_game(self.game)
        if status is not None and self.add_button:
            self.add_button.hide()

    def update_game(self, game: Game) -> None:
        """Свежие данные той же игры (например, прибавились часы) — без пересоздания карточки."""
        self.game = game
        self.meta.setText(card_meta(game))
        self.refresh_state()

    def _show_add_menu(self) -> None:
        menu = status_menu(self)
        action = menu.exec(self.add_button.mapToGlobal(QPoint(0, self.add_button.height() + 4)))
        if action:
            self.add_requested.emit(self.game, Status(action.data()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        inner_w = self.width() - 2
        cover_h = self.cover_height(self.width())
        self.cover.setGeometry(1, 1, inner_w, cover_h)
        self.body.setGeometry(1, 1 + cover_h, inner_w, self.BODY_HEIGHT)
        self.pill.move(10, 10)
        self._place_hours_pill()
        if self.add_button:
            self.add_button.move(inner_w - self.add_button.width() - 10, 10)

    def _place_hours_pill(self) -> None:
        if self.hours_pill is not None:
            self.hours_pill.move(10, self.cover.height() - self.hours_pill.height() - 10)

    def enterEvent(self, event):
        if self.add_button and ctx.library.status_of(self.game.key) is None:
            self.add_button.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.add_button:
            self.add_button.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        clicked = event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if clicked:
            self.clicked.emit(self.game)  # последним: после этого карточку могут удалить

    def contextMenuEvent(self, event):
        self.context_requested.emit(self.game, event.globalPos())
