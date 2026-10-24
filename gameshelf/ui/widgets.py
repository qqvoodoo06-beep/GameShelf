"""Переиспользуемые элементы интерфейса."""
from __future__ import annotations

import math

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QWheelEvent)
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QApplication, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import i18n, icons
from ..context import ctx
from ..i18n import ntr, tr
from ..models import Game, Status
from ..theme import C


def rgba(color: str, alpha: int) -> QColor:
    result = QColor(color)
    result.setAlpha(alpha)
    return result


class WheelGuard(QObject):
    """Колёсико мыши над выпадающим списком листает страницу, а не меняет выбор, — иначе его легко
    переключить случайно, прокручивая настройки. Выбор — щелчком. Ставится на сами списки через no_wheel()."""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        area = obj.parentWidget()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parentWidget()
        if area is not None:  # сами отдаём колёсико прокрутке, внутри которой стоит список
            viewport = area.viewport()
            forwarded = QWheelEvent(QPointF(obj.mapTo(viewport, event.position().toPoint())), event.globalPosition(),
                                    event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                                    event.phase(), event.inverted())
            QApplication.sendEvent(viewport, forwarded)
        event.accept()
        return True


_wheel_guard: WheelGuard | None = None


def no_wheel(widget: QWidget) -> QWidget:
    """Список (или поле со стрелками), который не переключается колёсиком мыши и не забирает от него фокус:
    иначе после прокрутки страницы стрелки и набранные буквы меняли бы выбор в нём."""
    global _wheel_guard
    if _wheel_guard is None:
        _wheel_guard = WheelGuard(QApplication.instance())
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # по умолчанию у списков WheelFocus: фокус от колёсика
    widget.installEventFilter(_wheel_guard)
    return widget


def games_count_text(n: int) -> str:
    return ntr(n, ("{n} игра", "{n} игры", "{n} игр"), n=i18n.integer(n))


def confirm_remove(parent: QWidget, game: Game) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(tr("Удалить игру"))
    box.setText(tr("Удалить «{name}» из библиотеки?", name=game.name))
    box.setInformativeText(tr("Статус, оценка и заметки будут удалены."))
    yes = QPushButton(tr("Удалить"))
    yes.setObjectName("Danger")  # до добавления: addButton сразу применяет стиль, и имя потом уже не учтётся
    box.addButton(yes, QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(tr("Отмена"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


# --------------------------------------------------------------------------- обложки

class CoverImage(QWidget):
    """Обложка со скруглёнными углами. Пока картинка грузится, рисуется заглушка."""

    def __init__(self, parent=None, radius: int = 12, round_bottom: bool = True, request_width: int = 640):
        super().__init__(parent)
        self._url = ""
        self._source: QPixmap | None = None
        self._composed: QPixmap | None = None
        self._radius = radius
        self._round_bottom = round_bottom
        self._request_width = request_width

    def set_url(self, url: str) -> None:
        if url == self._url:
            return
        self._url = url
        self._source = None
        self._composed = None
        self.update()
        if url:
            ctx.images.load(url, lambda pm, u=url: self._on_loaded(u, pm), width=self._request_width)

    def _on_loaded(self, url: str, pixmap: QPixmap | None) -> None:
        if url != self._url or pixmap is None:
            return
        self._source = pixmap
        self._composed = None
        self.update()

    def resizeEvent(self, event):
        self._composed = None
        super().resizeEvent(event)

    def _shape(self) -> QPainterPath:
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        if not self._round_bottom:
            square = QPainterPath()
            square.addRect(QRectF(rect.x(), rect.center().y(), rect.width(), rect.height() / 2))
            path = path.united(square)
        return path

    def _compose(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height()
        result = QPixmap(max(1, round(w * dpr)), max(1, round(h * dpr)))
        result.fill(Qt.GlobalColor.transparent)
        result.setDevicePixelRatio(dpr)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = self._shape()

        if self._source is not None and not self._source.isNull():
            painter.fillPath(shape, QColor("#ffffff"))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            target = QSize(result.width(), result.height())
            scaled = self._source.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - target.width()) // 2
            y = (scaled.height() - target.height()) // 3
            cropped = scaled.copy(x, y, target.width(), target.height())
            cropped.setDevicePixelRatio(dpr)
            painter.drawPixmap(0, 0, cropped)
        else:
            gradient = QLinearGradient(0, 0, w, h)
            gradient.setColorAt(0, QColor("#2b2048"))
            gradient.setColorAt(1, QColor("#171125"))
            painter.fillPath(shape, gradient)
            size = max(16, min(56, int(min(w, h) * 0.32)))
            painter.drawPixmap((w - size) // 2, (h - size) // 2, icons.pixmap("gamepad", "#4a3d72", size))
        painter.end()
        return result

    def paintEvent(self, event):
        if self._composed is None:
            self._composed = self._compose()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._composed)
        self.paint_overlay(painter)

    def paint_overlay(self, painter: QPainter) -> None:
        pass


class HeroImage(CoverImage):
    """Большая шапка в окне игры: картинка, плавно уходящая в фон."""

    def __init__(self, parent=None):
        super().__init__(parent, radius=0, round_bottom=True, request_width=1280)

    def paint_overlay(self, painter: QPainter) -> None:
        h = self.height()
        painter.fillRect(self.rect(), rgba(C["bg"], 70))
        fade = QLinearGradient(0, 0, 0, h)
        fade.setColorAt(0.0, rgba(C["bg"], 0))
        fade.setColorAt(0.45, rgba(C["bg"], 90))
        fade.setColorAt(1.0, QColor(C["bg"]))
        painter.fillRect(self.rect(), fade)
        side = QLinearGradient(0, 0, self.width(), 0)
        side.setColorAt(0.0, rgba(C["bg"], 150))
        side.setColorAt(0.6, rgba(C["bg"], 0))
        painter.fillRect(self.rect(), side)


# --------------------------------------------------------------------------- мелкие элементы

class ElidedLabel(QLabel):
    """Однострочная подпись, которая обрезается многоточием, если не помещается."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(1)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class WrapLabel(QLabel):
    """Многострочный текст, который не обрезается: высота подстраивается под ширину."""

    def __init__(self, text: str = "", parent=None, object_name: str = ""):
        super().__init__(text, parent)
        self._fitting = False
        self.setWordWrap(True)
        if object_name:
            self.setObjectName(object_name)

    def setText(self, text: str) -> None:
        super().setText(text)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _natural_height(self) -> int:
        """Высота текста при текущей ширине. QLabel.heightForWidth не бывает меньше minimumHeight, поэтому на время
        замера снимаем минимум — иначе после расширения окна текст так и оставался бы высоким, с пустотой под ним."""
        minimum = self.minimumHeight()
        if minimum:
            self.setMinimumHeight(0)
        height = self.heightForWidth(self.width())
        if minimum:
            self.setMinimumHeight(minimum)
        return height

    def _fit(self) -> None:
        if self._fitting or self.width() <= 0:
            return
        self._fitting = True  # смена минимума может сразу изменить размер и снова вызвать resizeEvent
        try:
            height = self._natural_height()
            if height > 0 and self.minimumHeight() != height:
                self.setMinimumHeight(height)
        finally:
            self._fitting = False


class CollapsibleLabel(WrapLabel):
    """Длинный текст (описание игры), свёрнутый до нескольких строк. Последняя видимая строка мягко
    затухает — видно, что текст продолжается. overflow_changed(True) — текст длиннее свёрнутого вида,
    значит, нужна кнопка «Показать полностью»."""

    overflow_changed = Signal(bool)

    def __init__(self, lines: int = 3, parent=None, object_name: str = ""):
        super().__init__("", parent, object_name)
        self._lines = lines
        self._collapsed = True
        self._overflow = False
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)  # видны первые строки, а не середина

    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._fit()
        self.update()

    def overflows(self) -> bool:
        return self._overflow

    def _fit(self) -> None:
        if self._fitting or self.width() <= 0:
            return
        self._fitting = True
        try:
            full = self._natural_height()
            if full <= 0:
                return
            margins = self.contentsMargins()
            limit = self.fontMetrics().lineSpacing() * self._lines + margins.top() + margins.bottom()
            overflow = full > limit + 2
            height = limit if self._collapsed and overflow else full
            if self.minimumHeight() != height or self.maximumHeight() != height:
                self.setFixedHeight(height)
        finally:
            self._fitting = False
        if overflow != self._overflow:
            self._overflow = overflow
            self.overflow_changed.emit(overflow)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._collapsed and self._overflow):
            return
        painter = QPainter(self)
        fade = self.fontMetrics().lineSpacing() * 1.3
        gradient = QLinearGradient(0, self.height() - fade, 0, self.height())
        gradient.setColorAt(0, rgba(C["bg"], 0))
        gradient.setColorAt(1, QColor(C["bg"]))
        painter.fillRect(QRectF(0, self.height() - fade, self.width(), fade), gradient)


class StatusPill(QLabel):
    """Плашка статуса. overlay=True — тёмный фон, чтобы читалась поверх обложки."""

    def __init__(self, status: Status | None = None, parent=None, overlay: bool = False):
        super().__init__(parent)
        self._overlay = overlay
        self.set_status(status)

    def set_status(self, status: Status | None) -> None:
        if status is None:
            self.hide()
            return
        c = QColor(status.color)
        rgb = f"{c.red()}, {c.green()}, {c.blue()}"
        background = "rgba(14, 10, 24, 215)" if self._overlay else f"rgba({rgb}, 36)"
        self.setText(f"●  {status.label}")
        self.setStyleSheet(
            f"QLabel {{ color: {status.color}; background: {background}; border: 1px solid rgba({rgb}, 110);"
            f" border-radius: 10px; padding: 2px 9px; font-size: 8.5pt; font-weight: 600; }}"
        )
        self.adjustSize()
        self.show()

    def set_playing(self) -> None:
        """Зелёная плашка: игра запущена прямо сейчас."""
        self.setText(tr("▶  Играете сейчас"))
        self.setStyleSheet(
            "QLabel { color: #34d399; background: rgba(14, 10, 24, 225); border: 1px solid rgba(52, 211, 153, 160);"
            " border-radius: 10px; padding: 2px 9px; font-size: 8.5pt; font-weight: 600; }"
        )
        self.adjustSize()
        self.show()


class ScoreLabel(QWidget):
    """Оценка игроков: «👍 96%» (доля положительных отзывов Steam) или «★ 4.4» (средняя оценка RAWG)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self._icon = QLabel()
        self._text = QLabel()
        layout.addWidget(self._icon)
        layout.addWidget(self._text)

    def set_game(self, game: Game, visible: bool = True) -> None:
        score = game.score
        if score is None or not visible:
            self.hide()
            return
        color = "#34d399" if score >= 80 else "#a3e635" if score >= 70 else "#fbbf24" if score >= 40 else "#f87171"
        if game.source == "rawg":
            self._icon.setPixmap(icons.pixmap("star", color, 14))
            self._text.setText(f"{score / 20:.1f}")
            self.setToolTip(tr("Средняя оценка игроков RAWG: {score} из 5", score=f"{score / 20:.1f}"))
        else:
            self._icon.setPixmap(icons.pixmap("thumbs-up", color, 14))
            self._text.setText(f"{score}%")
            if game.score_label:  # score_label — оценка от самого Steam («Очень положительные»), не переводим
                self.setToolTip(tr("Положительных отзывов игроков {source}: {score}% — {label}",
                                   source=game.source_title, score=score, label=game.score_label))
            else:
                self.setToolTip(tr("Положительных отзывов игроков {source}: {score}%",
                                   source=game.source_title, score=score))
        self._text.setStyleSheet(f"color: {color}; font-size: 9pt; font-weight: 600;")
        self.show()


class MetacriticBadge(QLabel):
    def __init__(self, score: int | None, parent=None):
        super().__init__(parent)
        if not score:
            self.hide()
            return
        color = "#34d399" if score >= 75 else "#fbbf24" if score >= 50 else "#f87171"
        self.setText(str(score))
        self.setToolTip(tr("Оценка Metacritic"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"QLabel {{ color: {color}; border: 1px solid {color}; border-radius: 6px;"
            f" padding: 1px 6px; font-size: 8.5pt; font-weight: 700; }}"
        )


def _star_path(rect: QRectF) -> QPainterPath:
    cx, cy = rect.center().x(), rect.center().y() + rect.height() * 0.04
    outer = rect.width() / 2
    inner = outer * 0.45
    path = QPainterPath()
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 36)
        point = QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))
        path.moveTo(point) if i == 0 else path.lineTo(point)
    path.closeSubpath()
    return path


class StarRating(QWidget):
    """Оценка от 0 до 5 звёзд. Повторный клик по той же звезде сбрасывает оценку."""

    changed = Signal(int)

    def __init__(self, value: int = 0, size: int = 22, editable: bool = True, parent=None):
        super().__init__(parent)
        self._value = value
        self._size = size
        self._gap = max(2, size // 5)
        self._editable = editable
        self._hover = 0
        self.setFixedSize(5 * size + 4 * self._gap, size)
        if editable:
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = max(0, min(5, value))
        self.update()

    def _star_at(self, x: float) -> int:
        return max(1, min(5, int(x // (self._size + self._gap)) + 1))

    def mouseMoveEvent(self, event):
        if self._editable:
            self._hover = self._star_at(event.position().x())
            self.update()

    def leaveEvent(self, event):
        self._hover = 0
        self.update()

    def mousePressEvent(self, event):
        if not self._editable or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        star = self._star_at(event.position().x())
        self.set_value(0 if star == self._value else star)
        self.changed.emit(self._value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shown = self._hover or self._value
        filled = QColor(C["star"]) if not self._hover else QColor("#fcd34d")
        for i in range(5):
            rect = QRectF(i * (self._size + self._gap), 0, self._size, self._size)
            path = _star_path(rect)
            if i < shown:
                painter.fillPath(path, filled)
            else:
                painter.fillPath(path, QColor(C["border_strong"]) if self._editable else QColor(C["border"]))


class Spinner(QWidget):
    def __init__(self, size: int = 36, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self):
        self._angle = (self._angle + 7) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        pen = QPen(QColor(C["border"]), 3.5)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen.setColor(QColor(C["primary"]))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)


class LoadingView(QWidget):
    def __init__(self, text: str = tr("Загрузка…"), parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch()
        spinner = Spinner(40)
        layout.addWidget(spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(12)
        self.label = QLabel(text)
        self.label.setObjectName("Muted")
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()


class SearchLineEdit(QLineEdit):
    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.addAction(icons.icon("search", C["text_dim"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.setClearButtonEnabled(True)


class IconBadge(QLabel):
    """Иконка в цветном скруглённом квадрате."""

    def __init__(self, icon_name: str, color: str, size: int = 44, icon_size: int = 22, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPixmap(icons.pixmap(icon_name, color, icon_size))
        c = QColor(color)
        self.setStyleSheet(f"background: rgba({c.red()}, {c.green()}, {c.blue()}, 38); border-radius: {int(size / 3.5)}px;")


class EmptyState(QWidget):
    """Заглушка: «ничего нет», ошибка, подсказка — с иконкой и кнопкой."""

    action = Signal()

    def __init__(self, icon_name: str = "gamepad", title: str = "", text: str = "", button: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(2)
        self.badge = QLabel()
        self.badge.setFixedSize(76, 76)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet("background: rgba(139, 92, 246, 30); border-radius: 38px;")
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(18)
        self.title = QLabel()
        self.title.setObjectName("EmptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addSpacing(4)
        self.text = WrapLabel(object_name="EmptyText")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setFixedWidth(460)
        layout.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(18)
        self.button = QPushButton()
        self.button.setObjectName("Primary")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.action)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(3)
        self.set_content(icon_name, title, text, button)

    def set_content(self, icon_name: str, title: str, text: str, button: str = "") -> None:
        self.badge.setPixmap(icons.pixmap(icon_name, C["primary_light"], 34))
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        self.button.setText(button)
        self.button.setVisible(bool(button))


class Toast(QLabel):
    """Всплывающее уведомление внизу окна."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.hide()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._queue: list[str] = []

    MARGIN = 24
    MAX_QUEUE = 5

    def show_message(self, text: str) -> None:
        # Пока показывается другое сообщение — ждём своей очереди, а не затираем его
        if self.isVisible() and self._timer.isActive():
            if text != self.text() and text not in self._queue:
                self._queue = (self._queue + [text])[-self.MAX_QUEUE:]
            return
        self._display(text)

    def _display(self, text: str) -> None:
        parent = self.parentWidget()
        self.setWordWrap(True)
        self.setMaximumWidth(max(200, min(720, parent.width() - 2 * self.MARGIN)))
        self.setText(text)
        self.adjustSize()
        x = (parent.width() - self.width()) // 2 + 124  # по центру области справа от бокового меню
        x = max(self.MARGIN, min(x, parent.width() - self.width() - self.MARGIN))
        self.move(x, parent.height() - self.height() - 32)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setDuration(220)
        self._anim.setStartValue(self._effect.opacity() if self.isVisible() else 0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(2600)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(400)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if self._anim.endValue() == 0.0:
            self.hide()
            if self._queue:
                self._display(self._queue.pop(0))


class NavButton(QAbstractButton):
    """Пункт бокового меню: иконка, подпись и счётчик."""

    def __init__(self, text: str, icon_name: str, icon_color: str | None = None, checkable: bool = True, parent=None):
        super().__init__(parent)
        self.setText(text)
        self._icon = icon_name
        self._icon_color = icon_color
        self._count: int | None = None
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)

    def set_count(self, count: int | None) -> None:
        self._count = count
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(200, 40)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        active = self.isChecked()
        hover = self.underMouse()

        painter.setPen(Qt.PenStyle.NoPen)
        if active:
            painter.setBrush(rgba(C["primary"], 44))
            painter.drawRoundedRect(QRectF(0, 0, w, h), 10, 10)
            painter.setBrush(QColor(C["primary"]))
            painter.drawRoundedRect(QRectF(0, 11, 3, h - 22), 1.5, 1.5)
        elif hover:
            painter.setBrush(QColor(255, 255, 255, 12))
            painter.drawRoundedRect(QRectF(0, 0, w, h), 10, 10)

        if self._icon_color:
            icon_color = self._icon_color
        else:
            icon_color = C["primary_light"] if active else (C["text"] if hover else C["text_muted"])
        painter.drawPixmap(14, (h - 18) // 2, icons.pixmap(self._icon, icon_color, 18))

        font = QFont(self.font())
        font.setPointSizeF(10)
        font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(C["text"] if active or hover else C["text_muted"]))
        painter.drawText(QRect(44, 0, w - 44 - 52, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.text())

        if self._count is not None:
            count_font = QFont(font)
            count_font.setPointSizeF(8.5)
            count_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(count_font)
            text = str(self._count)
            pill_w = max(26, QFontMetrics(count_font).horizontalAdvance(text) + 14)
            pill = QRectF(w - pill_w - 10, (h - 20) / 2, pill_w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(rgba(C["primary"], 80) if active else QColor(255, 255, 255, 14))
            painter.drawRoundedRect(pill, 10, 10)
            painter.setPen(QColor(C["text"] if active else C["text_muted"]))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


# --------------------------------------------------------------------------- сетка карточек

class ResponsiveGrid(QLayout):
    """Сетка, которая сама подбирает число колонок под ширину окна."""

    def __init__(self, parent=None, min_width: int = 230, spacing: int = 18):
        super().__init__(parent)
        self._items = []
        self._min_width = min_width
        self._gap = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        return QSize(self._min_width, 0)

    def _arrange(self, rect: QRect, apply: bool) -> int:
        items = [it for it in self._items if it.widget() is None or not it.widget().isHidden()]
        if not items:
            return 0
        width = rect.width()
        columns = max(1, (width + self._gap) // (self._min_width + self._gap))
        item_w = (width - self._gap * (columns - 1)) / columns
        y = rect.y()
        for row_start in range(0, len(items), columns):
            row = items[row_start:row_start + columns]
            heights = []
            for it in row:
                widget = it.widget()
                heights.append(widget.height_for(int(item_w)) if hasattr(widget, "height_for")
                               else it.sizeHint().height())
            row_h = max(heights)
            if apply:
                for col, it in enumerate(row):
                    x = rect.x() + round(col * (item_w + self._gap))
                    it.setGeometry(QRect(x, y, int(item_w), row_h))
            y += row_h + self._gap
        return y - self._gap - rect.y()


class CardGrid(QWidget):
    def __init__(self, min_width: int = 230, spacing: int = 18, parent=None):
        super().__init__(parent)
        self._grid = ResponsiveGrid(self, min_width, spacing)
        self._grid.setContentsMargins(0, 0, 0, 0)
        # Высоту задаём сами в _update_height, layout не должен её перезаписывать
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

    def clear(self) -> None:
        while (item := self._grid.takeAt(0)) is not None:
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._update_height()

    def add(self, widget: QWidget) -> None:
        self._grid.addWidget(widget)
        widget.show()

    def finish(self) -> None:
        """Вызывать после добавления пачки карточек."""
        self._grid.invalidate()
        self._update_height()

    def widgets(self) -> list[QWidget]:
        return [self._grid.itemAt(i).widget() for i in range(self._grid.count())]

    def is_empty(self) -> bool:
        return self._grid.count() == 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_height()

    def _update_height(self) -> None:
        height = self._grid.heightForWidth(self.width())
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)


def make_scroll_area(content: QWidget):
    from PySide6.QtWidgets import QFrame, QScrollArea

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # Полоса прокрутки всегда занимает место: иначе при её появлении сетка сужается,
    # контент становится ниже, полоса пропадает — и так по кругу.
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    bar = scroll.verticalScrollBar()

    def on_range_changed(_min, maximum):
        scrollable = maximum > 0
        if bar.property("scrollable") != scrollable:
            bar.setProperty("scrollable", scrollable)
            bar.style().unpolish(bar)
            bar.style().polish(bar)

    bar.rangeChanged.connect(on_range_changed)
    on_range_changed(0, 0)
    content.setObjectName("ScrollContent")
    scroll.setWidget(content)
    scroll.viewport().setAutoFillBackground(False)
    return scroll


def page_header(title: str, subtitle: str = "") -> tuple[QVBoxLayout, QLabel, QLabel]:
    box = QVBoxLayout()
    box.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("PageTitle")
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("PageSubtitle")
    box.addWidget(title_label)
    box.addWidget(subtitle_label)
    return box, title_label, subtitle_label


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            layout.addStretch()
        elif isinstance(w, QLayout):
            layout.addLayout(w)
        else:
            layout.addWidget(w)
    return layout
