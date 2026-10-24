from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QVBoxLayout

from .. import icons
from ..i18n import tr
from ..models import Status
from ..theme import C
from ..timefmt import format_duration
from .widgets import ElidedLabel, NavButton, hbox

SOURCE_LINKS = [("steam", "Steam", "https://store.steampowered.com"), ("rawg", "RAWG", "https://rawg.io")]


class NowPlaying(QFrame):
    """Зелёная плашка «Сейчас в игре» — видна, пока идёт сеанс."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlaying")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 11)
        layout.setSpacing(2)
        caption = QLabel(tr("▶  СЕЙЧАС В ИГРЕ"))
        caption.setObjectName("NowPlayingCaption")
        self.name = ElidedLabel()
        self.name.setObjectName("NowPlayingName")
        self.time = QLabel()
        self.time.setObjectName("NowPlayingTime")
        layout.addWidget(caption)
        layout.addWidget(self.name)
        layout.addWidget(self.time)
        self.hide()

    def set_playing(self, playing: list[tuple[str, int]]) -> None:
        if not playing:
            self.hide()
            return
        name, seconds = playing[0]
        if len(playing) > 1:
            name = tr("{name} и ещё {count}", name=name, count=len(playing) - 1)
        self.name.setText(name)
        self.time.setText(tr("в этом сеансе: {duration}", duration=format_duration(seconds)))
        self.show()


class Sidebar(QFrame):
    navigate = Signal(str)
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(248)
        self._buttons: dict[str, NavButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 22, 14, 16)
        layout.setSpacing(2)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(38))
        name = QLabel("GameShelf")
        name.setObjectName("Logo")
        sub = QLabel(tr("трекер ваших игр"))
        sub.setObjectName("LogoSub")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(name)
        texts.addWidget(sub)
        layout.addLayout(hbox(logo, texts, None, spacing=12, margins=(8, 0, 0, 0)))
        layout.addSpacing(14)

        self._section(layout, tr("БИБЛИОТЕКА"))
        self._add(layout, "all", NavButton(tr("Все игры"), "grid"))
        for status in Status:
            self._add(layout, status.value, NavButton(status.label, status.icon, status.color))

        self._section(layout, tr("ОБЗОР"))
        self._add(layout, "search", NavButton(tr("Поиск игр"), "search"))
        self._add(layout, "stats", NavButton(tr("Статистика"), "stats"))
        self._add(layout, "achievements", NavButton(tr("Достижения"), "trophy"))
        layout.addStretch()

        self.now_playing = NowPlaying()
        layout.addWidget(self.now_playing)
        layout.addSpacing(8)

        settings = NavButton(tr("Настройки"), "settings", checkable=False)
        settings.clicked.connect(self.settings_requested)
        layout.addWidget(settings)

        self.attribution = QLabel()
        self.attribution.setObjectName("Attribution")
        self.attribution.setOpenExternalLinks(True)
        layout.addWidget(self.attribution)
        self.set_sources({"steam"})

    def set_sources(self, sources: set[str]) -> None:
        """Подпись «Данные об играх — …» со ссылками на используемые каталоги (RAWG этого требует)."""
        links = [f'<a href="{url}" style="color:{C["primary_light"]}; text-decoration:none;">{title}</a>'
                 for name, title, url in SOURCE_LINKS if name in sources]
        self.attribution.setText(tr("Данные об играх — {sources}", sources=", ".join(links)))

    def _section(self, layout: QVBoxLayout, title: str) -> None:
        label = QLabel(title)
        label.setObjectName("SidebarSection")
        layout.addWidget(label)

    def _add(self, layout: QVBoxLayout, key: str, button: NavButton) -> None:
        self._buttons[key] = button
        self._group.addButton(button)
        button.clicked.connect(lambda: self.navigate.emit(key))
        layout.addWidget(button)

    def set_current(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def set_now_playing(self, playing: list[tuple[str, int]]) -> None:
        self.now_playing.set_playing(playing)

    def set_counts(self, counts: dict) -> None:
        self._buttons["all"].set_count(counts.get("all", 0))
        for status in Status:
            self._buttons[status.value].set_count(counts.get(status, 0))
