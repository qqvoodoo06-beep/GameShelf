"""Заставка при запуске: «Disorder Game Studio представляет» → GameShelf.

Буквы названия студии собираются из хаоса в слово (беспорядок → порядок), с лёгким цветным «сбоем», как у
старого экрана. Потом появляется логотип GameShelf, и открывается программа. Щелчок или любая клавиша — пропустить.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

from .. import icons
from ..i18n import tr
from . import logotype

SHOW_INTRO_KEY = "show_intro"  # настройка: показывать заставку при запуске
STUDIO = "DISORDER"
STUDIO_SUB = "GAME STUDIO"
WIDTH, HEIGHT = 760, 430
DURATION = 3.6            # секунды до открытия программы
SCATTER = 150             # насколько далеко разлетаются буквы в начале, px
LETTER_HEIGHT = 56        # высота букв DISORDER, px
# Вспышки сбоя после того, как слово собралось: (начало, конец) в секундах — надпись на миг «рвёт»
GLITCH_BURSTS = [(1.12, 1.2), (1.5, 1.55), (1.78, 1.86)]
RADIUS = 22
MODIFIER_KEYS = {Qt.Key.Key_Alt, Qt.Key.Key_AltGr, Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Meta}


def _clamp(t: float) -> float:
    return max(0.0, min(1.0, t))


def _phase(t: float, start: float, end: float) -> float:
    """0 до начала отрезка, 1 после конца, плавно между."""
    return _clamp((t - start) / (end - start))


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_in_out(t: float) -> float:
    return 3 * t * t - 2 * t * t * t


def _font(family: str, size: float, weight: QFont.Weight, spacing: float = 0.0) -> QFont:
    font = QFont(family)
    font.setPointSizeF(size)
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def _burst(t: float) -> tuple[float, int]:
    """Сила вспышки сбоя (0 — нет) и её номер: от номера зависит, какие полосы и куда прыгают."""
    for number, (start, end) in enumerate(GLITCH_BURSTS):
        if start <= t <= end:
            return 1.0 - abs((t - start) / (end - start) * 2 - 1) * 0.5, number
    return 0.0, -1


class IntroScreen(QWidget):
    """Окно-заставка без рамки по центру экрана. finished — заставка закончилась или её пропустили."""

    finished = Signal()

    def __init__(self, parent=None):
        flags = Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # скруглённые углы без чёрных уголков
        self.setFixedSize(WIDTH, HEIGHT)
        self.setWindowTitle("Disorder Game Studio")
        rng = random.Random(1337)  # хаос каждый раз одинаковый: заставка выглядит так, как задумано
        # Для каждой буквы: куда она отлетела (x, y — доли SCATTER), поворот, задержка появления
        self._chaos = [(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-35, 35), rng.uniform(0, 0.22))
                       for _ in STUDIO]
        # Свои буквы логотипа (не зависят от шрифтов на компьютере) и «рывки» полос во вспышках сбоя
        self._scanlines: QPixmap | None = None  # полосы развёртки, нарисованные один раз
        self._letters = logotype.word(STUDIO, LETTER_HEIGHT)
        self._word_width = logotype.word_width(STUDIO, LETTER_HEIGHT)
        self._burst_slices = [[(rng.uniform(0.0, 0.85), rng.uniform(0.06, 0.18), rng.uniform(-14, 14))
                               for _ in range(3)] for _ in GLITCH_BURSTS]
        self._sub_font = _font("Segoe UI", 12.5, QFont.Weight.DemiBold)
        self._small_font = _font("Segoe UI", 10.5, QFont.Weight.Normal)
        self._app_font = _font("Segoe UI", 30, QFont.Weight.Bold)
        self._hint_font = _font("Segoe UI", 8.5, QFont.Weight.Normal)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self._done = False
        self.moment: float | None = None  # для проверки: нарисовать конкретную секунду, а не текущую

    # ------------------------------------------------------------------ управление

    def start(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.center().x() - WIDTH // 2, area.center().y() - HEIGHT // 2)
        self._clock.start()
        self._timer.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def skip(self) -> None:
        """Закончить сейчас (щелчок, клавиша или время вышло)."""
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.close()
        self.finished.emit()

    def elapsed(self) -> float:
        if self.moment is not None:
            return self.moment
        return self._clock.elapsed() / 1000 if self._clock.isValid() else 0.0

    def _tick(self) -> None:
        if self.elapsed() >= DURATION:
            self.skip()
        else:
            self.update()

    def mousePressEvent(self, event):
        self.skip()

    def keyPressEvent(self, event):
        # Сам по себе Alt/Ctrl/Shift не пропускает: иначе по Alt+F4 Alt открыл бы программу, а F4 тут же её закрыл
        if event.key() not in MODIFIER_KEYS:
            self.skip()

    def closeEvent(self, event):
        self.skip()  # Alt+F4 или Windows закрыли заставку — сразу открываем программу
        event.accept()

    # ------------------------------------------------------------------ рисование

    def paintEvent(self, event):
        t = self.elapsed()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Всё окно плавно появляется в начале и гаснет в конце
        painter.setOpacity(_ease_out(_phase(t, 0.0, 0.35)) * (1 - _phase(t, DURATION - 0.4, DURATION)))
        self._paint_background(painter, t)
        # Смена кадра без наложения: название студии уходит вверх и гаснет, потом снизу поднимается GameShelf
        leave = _ease_in_out(_phase(t, 1.95, 2.3))
        if leave < 1:
            painter.save()
            painter.translate(0, -16 * leave)
            self._paint_studio(painter, t, 1 - leave)
            painter.restore()
        arrive = _ease_out(_phase(t, 2.3, 2.85))
        if arrive > 0:
            painter.save()
            painter.translate(0, 18 * (1 - arrive))
            self._paint_app(painter, arrive)
            painter.restore()
        self._paint_scanlines(painter, t)
        self._paint_hint(painter, t)

    def _paint_background(self, painter: QPainter, t: float) -> None:
        box = QRectF(1, 1, WIDTH - 2, HEIGHT - 2)
        background = QLinearGradient(box.topLeft(), box.bottomRight())
        background.setColorAt(0, QColor("#150e27"))
        background.setColorAt(1, QColor("#0a0713"))
        painter.setPen(QPen(QColor(139, 92, 246, 90), 1.2))
        painter.setBrush(background)
        painter.drawRoundedRect(box, RADIUS, RADIUS)
        # Мягкое фиолетовое свечение за надписью, слегка «дышит»
        glow = QRadialGradient(QPointF(WIDTH / 2, HEIGHT / 2 - 10), WIDTH * 0.42)
        strength = 70 + 30 * _ease_in_out(_phase(t, 0.3, 1.4))
        glow.setColorAt(0, QColor(139, 92, 246, int(strength)))
        glow.setColorAt(0.55, QColor(217, 70, 239, int(strength * 0.25)))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        clip = QPainterPath()
        clip.addRoundedRect(box, RADIUS, RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(box, glow)
        painter.restore()

    def _paint_studio(self, painter: QPainter, t: float, alpha: float) -> None:
        painter.save()
        painter.setOpacity(painter.opacity() * alpha)
        scale = LETTER_HEIGHT / logotype.CAP
        top = HEIGHT / 2 - LETTER_HEIGHT / 2 - 34
        left = (WIDTH - self._word_width) / 2
        burst, number = _burst(t)
        slices = self._burst_slices[number] if number >= 0 else []
        for index, (letter, path, x, width) in enumerate(self._letters):
            dx, dy, angle, delay = self._chaos[index]
            p = _ease_out(_phase(t, 0.15 + delay + index * 0.035, 0.95 + delay + index * 0.035))
            chaos = 1 - p
            if p <= 0:
                continue
            jitter_y, jitter_angle = logotype.DISORDER_JITTER[index]
            center = QPointF(left + x + width / 2 + dx * SCATTER * chaos,
                             top + LETTER_HEIGHT / 2 + jitter_y * scale + dy * SCATTER * 0.6 * chaos)
            painter.save()
            painter.translate(center)
            painter.rotate(jitter_angle + angle * chaos)
            painter.translate(-width / 2, -LETTER_HEIGHT / 2)
            # Цветная «двоящаяся» тень: пока буквы летят — широкая, потом остаётся тонкой, во вспышке рвётся
            split = 7 * chaos + 1.6 + 5 * burst
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(217, 70, 239, int(150 * p)))
            painter.drawPath(path.translated(-split, 0))
            painter.setBrush(QColor(34, 211, 238, int(130 * p)))
            painter.drawPath(path.translated(split, 0.6))
            gradient = QLinearGradient(0, 0, 0, LETTER_HEIGHT)
            gradient.setColorAt(0, QColor(255, 255, 255, int(255 * p)))
            gradient.setColorAt(1, QColor(196, 181, 253, int(255 * p)))
            painter.setBrush(gradient)
            cut = logotype.DISORDER_CUTS.get(index)
            bands = []
            if cut is not None:  # «срез»: полоса буквы сдвинута вбок — часть самого логотипа
                bands.append((cut[0], cut[1], cut[2] * scale * (1 + 1.5 * burst)))
            for slice_top, slice_height, shift in slices:  # во вспышке сбоя прыгают случайные полосы
                bands.append((slice_top, slice_top + slice_height, shift * burst))
            self._draw_cut_letter(painter, path, width, bands)
            painter.restore()
        self._paint_studio_texts(painter, t, top + LETTER_HEIGHT, burst)
        painter.restore()

    @staticmethod
    def _draw_cut_letter(painter: QPainter, path: QPainterPath, width: float,
                         bands: list[tuple[float, float, float]]) -> None:
        """Буква, у которой горизонтальные полосы сдвинуты вбок (как у сломанного сигнала)."""
        if not bands:
            painter.drawPath(path)
            return
        whole = QPainterPath()
        whole.addRect(QRectF(-40, -40, width + 80, LETTER_HEIGHT + 80))
        rest = QPainterPath(whole)
        for band_top, band_bottom, shift in bands:
            band = QPainterPath()
            band.addRect(QRectF(-40, band_top * LETTER_HEIGHT, width + 80, (band_bottom - band_top) * LETTER_HEIGHT))
            rest = rest.subtracted(band)
            painter.save()
            painter.setClipPath(band)
            painter.translate(shift, 0)
            painter.drawPath(path)
            painter.restore()
        painter.save()
        painter.setClipPath(rest)
        painter.drawPath(path)
        painter.restore()

    def _paint_studio_texts(self, painter: QPainter, t: float, baseline: float, burst: float) -> None:
        # Тонкая линия под названием вырастает из центра (во вспышке — дёргается)
        line = _ease_out(_phase(t, 1.0, 1.5))
        if line > 0:
            half = 150 * line
            y = baseline + 20
            shake = 6 * burst
            pen_gradient = QLinearGradient(WIDTH / 2 - half, y, WIDTH / 2 + half, y)
            pen_gradient.setColorAt(0, QColor(139, 92, 246, 0))
            pen_gradient.setColorAt(0.5, QColor(217, 70, 239, 230))
            pen_gradient.setColorAt(1, QColor(139, 92, 246, 0))
            painter.setPen(QPen(pen_gradient, 2))
            painter.drawLine(QPointF(WIDTH / 2 - half + shake, y), QPointF(WIDTH / 2 + half + shake, y))
        # GAME STUDIO: буквы съезжаются из широкой разрядки
        sub = _ease_out(_phase(t, 0.95, 1.55))
        if sub > 0:
            font = QFont(self._sub_font)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 9 + 14 * (1 - sub))
            painter.setFont(font)
            painter.setPen(QColor(196, 181, 253, int(235 * sub * (1 - 0.5 * burst))))
            painter.drawText(QRectF(-4 * burst, baseline + 32, WIDTH, 30), Qt.AlignmentFlag.AlignCenter, STUDIO_SUB)
        presents = _ease_out(_phase(t, 1.35, 1.8))
        if presents > 0:
            painter.setFont(self._small_font)
            painter.setPen(QColor(165, 155, 196, int(220 * presents)))
            painter.drawText(QRectF(0, baseline + 70, WIDTH, 26), Qt.AlignmentFlag.AlignCenter, tr("представляет"))

    def _paint_scanlines(self, painter: QPainter, t: float) -> None:
        """Полосы развёртки, как на старом экране; во вспышке сбоя — ярче. Рисуем их один раз в картинку:
        150 линий заново на каждом кадре съедали бо́льшую часть времени кадра."""
        dpr = self.devicePixelRatioF()
        if self._scanlines is None or self._scanlines.devicePixelRatio() != dpr:
            pixmap = QPixmap(round(WIDTH * dpr), round(HEIGHT * dpr))
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(Qt.GlobalColor.transparent)
            lines = QPainter(pixmap)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(1, 1, WIDTH - 2, HEIGHT - 2), RADIUS, RADIUS)
            lines.setClipPath(clip)
            for y in range(2, HEIGHT, 3):
                lines.fillRect(QRectF(0, y, WIDTH, 1), QColor(0, 0, 0))
            lines.end()
            self._scanlines = pixmap
        burst, _number = _burst(t)
        painter.save()
        painter.setOpacity(painter.opacity() * (0.16 + 0.3 * burst))
        painter.drawPixmap(0, 0, self._scanlines)
        painter.restore()

    def _paint_app(self, painter: QPainter, alpha: float) -> None:
        painter.save()
        painter.setOpacity(painter.opacity() * alpha)
        size = 84
        scale = 0.86 + 0.14 * alpha
        logo = icons.logo_pixmap(size)
        center_y = HEIGHT / 2 - 34
        painter.save()
        painter.translate(WIDTH / 2, center_y)
        painter.scale(scale, scale)
        painter.drawPixmap(QRectF(-size / 2, -size / 2, size, size), logo, QRectF(logo.rect()))
        painter.restore()
        painter.setFont(self._app_font)
        painter.setPen(QColor("#f1edff"))
        painter.drawText(QRectF(0, center_y + size / 2 + 10, WIDTH, 50), Qt.AlignmentFlag.AlignCenter, "GameShelf")
        painter.setFont(self._small_font)
        painter.setPen(QColor(165, 155, 196))
        painter.drawText(QRectF(0, center_y + size / 2 + 58, WIDTH, 26), Qt.AlignmentFlag.AlignCenter,
                         tr("трекер ваших игр"))
        painter.restore()

    def _paint_hint(self, painter: QPainter, t: float) -> None:
        hint = _phase(t, 0.7, 1.1) * (1 - _phase(t, 2.0, 2.3))
        if hint <= 0:
            return
        painter.save()
        painter.setFont(self._hint_font)
        painter.setPen(QColor(110, 100, 144, int(200 * hint)))
        painter.drawText(QRectF(0, HEIGHT - 34, WIDTH - 24, 20),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tr("Щёлкните, чтобы пропустить"))
        painter.restore()


def play_intro(window) -> IntroScreen:
    """Показывает заставку, а по её окончании — главное окно (на переднем плане)."""
    intro = IntroScreen()

    def open_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()
        window._intro = None
        intro.deleteLater()

    intro.finished.connect(open_window)
    window._intro = intro  # держим ссылку, пока заставка идёт
    intro.start()
    return intro
