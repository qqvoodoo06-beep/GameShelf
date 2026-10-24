"""Собственные буквы логотипа Disorder Game Studio — узкие «технические», в духе Agency FB.

Рисуются линиями (как перо с плоским концом) по средней линии буквы, поэтому выглядят одинаково на любом
компьютере и не зависят от установленных шрифтов. Пока нужны только буквы слова DISORDER.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QTransform

CAP = 100.0        # высота букв в условных единицах
STROKE = 14.0      # толщина линии
SPACING = 11.0     # расстояние между буквами
_H = STROKE / 2

# «Беспорядок» — часть логотипа: у каждой буквы DISORDER свой сдвиг по высоте и наклон (в условных единицах
# и градусах), а у некоторых кусок «срезан» и сдвинут вбок, как у сломанного сигнала: (верх, низ — доли высоты,
# сдвиг). Значения подобраны вручную, чтобы слово оставалось читаемым.
DISORDER_JITTER = [(0, -2.0), (-5, 3.0), (3, -1.5), (-2, 2.0), (4, -3.0), (-3, 1.5), (1, -2.5), (5, 2.5)]
DISORDER_CUTS = {2: (0.40, 0.56, 8.0), 4: (0.18, 0.30, 6.0), 6: (0.62, 0.76, -7.0)}

# Буква: ширина и линии. Линия — список точек (x, y, радиус скругления угла в этой точке) и замкнута ли она.
# Координаты — по середине линии: от _H до ширины − _H по горизонтали, от _H до CAP − _H по вертикали.
_GLYPHS: dict[str, tuple[float, list[tuple[list[tuple[float, float, float]], bool]]]] = {}


def _glyph(letter: str, width: float, *lines: tuple[list[tuple[float, float, float]], bool]) -> None:
    _GLYPHS[letter] = (width, list(lines))


def _define() -> None:
    top, bottom, middle = _H, CAP - _H, CAP * 0.5
    # I — одна вертикальная линия
    _glyph("I", STROKE, ([(_H, top, 0), (_H, bottom, 0)], False))
    # D — прямой левый край, скруглённый правый
    w = 46
    r = w - _H
    _glyph("D", w, ([(_H, top, 0), (r, top, 18), (r, bottom, 18), (_H, bottom, 0)], True))
    # O — вытянутый прямоугольник со скруглёнными углами
    w = 46
    r = w - _H
    _glyph("O", w, ([(_H, top, 15), (r, top, 15), (r, bottom, 15), (_H, bottom, 15)], True))
    # E — три перекладины, средняя чуть короче
    w = 40
    r = w - _H
    _glyph("E", w, ([(r, top, 0), (_H, top, 0), (_H, bottom, 0), (r, bottom, 0)], False),
           ([(_H, middle, 0), (r - 4, middle, 0)], False))
    # R — квадратная чаша со скруглёнными углами и прямая наклонная ножка
    w = 46
    r = w - _H
    _glyph("R", w, ([(_H, bottom, 0), (_H, top, 0), (r, top, 13), (r, middle, 13), (_H, middle, 0)], False),
           ([(_H + (r - _H) * 0.45, middle, 0), (r, bottom, 0)], False))
    # S — «квадратная» S: верх, левый спуск, середина, правый спуск, низ
    w = 44
    r = w - _H
    _glyph("S", w, ([(r, top, 0), (_H, top, 13), (_H, middle, 13), (r, middle, 13), (r, bottom, 13),
                     (_H, bottom, 0)], False))


_define()


def _centerline(points: list[tuple[float, float, float]], closed: bool) -> QPainterPath:
    """Ломаная со скруглёнными углами (радиус — в самой точке угла)."""
    path = QPainterPath()
    count = len(points)

    def corner(i: int):
        x, y, radius = points[i % count]
        px, py, _ = points[(i - 1) % count]
        nx, ny, _ = points[(i + 1) % count]
        if radius <= 0 or (not closed and i in (0, count - 1)):
            return QPointF(x, y), None, QPointF(x, y)
        def towards(ax, ay, bx, by, distance):
            length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 or 1
            k = min(distance, length / 2) / length
            return QPointF(ax + (bx - ax) * k, ay + (by - ay) * k)
        return towards(x, y, px, py, radius), QPointF(x, y), towards(x, y, nx, ny, radius)

    first_in, first_ctrl, first_out = corner(0)
    path.moveTo(first_out if closed and first_ctrl is not None else QPointF(points[0][0], points[0][1]))
    for i in range(1, count + (1 if closed else 0)):
        start, control, end = corner(i)
        path.lineTo(start)
        if control is not None:
            path.quadTo(control, end)
    if closed:
        path.closeSubpath()
    return path


def glyph(letter: str) -> tuple[QPainterPath, float]:
    """Контур буквы (готовый к заливке) в условных единицах и её ширина. Неизвестная буква — пусто."""
    if letter not in _GLYPHS:
        return QPainterPath(), CAP * 0.3
    width, lines = _GLYPHS[letter]
    stroker = QPainterPathStroker()
    stroker.setWidth(STROKE)
    # Квадратные концы продлевают линию на полтолщины — ровно до края буквы (средняя линия идёт в _H от края),
    # а то, что вылезло за рамку буквы (наклонная ножка R), срезаем рамкой: края букв ровные, как у шрифта
    stroker.setCapStyle(Qt.PenCapStyle.SquareCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    outline = QPainterPath()
    for points, closed in lines:
        outline = outline.united(stroker.createStroke(_centerline(points, closed)))
    frame = QPainterPath()
    frame.addRect(QRectF(0, 0, width, CAP))
    return outline.intersected(frame).simplified(), width


def word(text: str, height: float) -> list[tuple[str, QPainterPath, float, float]]:
    """Буквы слова высотой height px: (буква, контур с левым верхним углом в 0,0, x от начала слова, ширина)."""
    scale = height / CAP
    transform = QTransform.fromScale(scale, scale)
    result, x = [], 0.0
    for letter in text:
        path, width = glyph(letter)
        result.append((letter, transform.map(path), x, width * scale))
        x += (width + SPACING) * scale
    return result


def word_width(text: str, height: float) -> float:
    letters = word(text, height)
    return letters[-1][2] + letters[-1][3] if letters else 0.0
