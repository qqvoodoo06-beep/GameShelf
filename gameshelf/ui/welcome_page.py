from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QRadialGradient
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import icons
from ..i18n import tr
from ..theme import C
from .widgets import IconBadge, WrapLabel, hbox, rgba

FEATURES = [
    ("search", tr("Любая игра из Steam"), tr("Ищите по названию — можно по-русски. Регистрация не нужна.")),
    ("bookmark", tr("Статусы и заметки"), tr("Играю, в планах, пройдено, отложено, брошено — плюс оценка и часы.")),
    ("stats", tr("Статистика"), tr("Сколько игр пройдено, сколько часов наиграно, какие жанры любите.")),
]


class WelcomePage(QWidget):
    """Первый запуск: коротко рассказываем, что умеет приложение."""

    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.addStretch()

        card = QFrame()
        card.setObjectName("Panel")
        card.setFixedWidth(560)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(44, 40, 44, 36)
        layout.setSpacing(0)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(72))
        logo.setFixedSize(72, 72)
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(20)

        title = QLabel(tr("Добро пожаловать в GameShelf"))
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)
        subtitle = WrapLabel(tr("Ваша личная коллекция игр: отмечайте, во что играете, что уже прошли "
                                "и что хотите попробовать."), object_name="Muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(28)

        for icon_name, heading, text in FEATURES:
            heading_label = QLabel(heading)
            heading_label.setStyleSheet("font-weight: 600;")
            text_label = WrapLabel(text, object_name="Muted")
            texts = QVBoxLayout()
            texts.setSpacing(1)
            texts.addWidget(heading_label)
            texts.addWidget(text_label)
            layout.addLayout(hbox(IconBadge(icon_name, C["primary_light"], 40, 20), texts, spacing=14))
            layout.addSpacing(14)
        layout.addSpacing(12)

        start = QPushButton(tr("Начать"))
        start.setObjectName("Primary")
        start.setMinimumHeight(46)
        start.setCursor(Qt.CursorShape.PointingHandCursor)
        start.setDefault(True)
        start.clicked.connect(self.finished)
        layout.addWidget(start)

        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch()

    def paintEvent(self, event):
        # Мягкое фиолетовое свечение на фоне
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for center, radius, color, alpha in (
            (QPointF(self.width() * 0.25, self.height() * 0.15), self.width() * 0.45, C["primary"], 60),
            (QPointF(self.width() * 0.8, self.height() * 0.9), self.width() * 0.4, C["accent"], 40),
        ):
            gradient = QRadialGradient(center, radius)
            gradient.setColorAt(0, rgba(color, alpha))
            gradient.setColorAt(1, rgba(color, 0))
            painter.fillRect(self.rect(), gradient)
