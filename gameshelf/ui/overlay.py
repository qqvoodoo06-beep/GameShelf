"""Оверлей достижений: плашка поверх окна игры (как в Steam или Xbox), без внедрения в саму игру.

Это обычное окно «поверх всех»: оно не забирает фокус (игра не сворачивается и продолжает получать нажатия)
и пропускает клики насквозь. Над играми в окне и в «полноэкранном без рамки» режиме оно видно; поверх
старого эксклюзивного полного экрана Windows ничего не рисует — тогда ждём, пока игра его отпустит.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import i18n, icons
from ..config import Settings
from ..i18n import ntr, tr
from ..images import ImageLoader

CORNERS = {"bottom_right": tr("Справа снизу"), "bottom_left": tr("Слева снизу"),
           "top_right": tr("Справа сверху"), "top_left": tr("Слева сверху")}
WIDTH, HEIGHT = 392, 98
MARGIN = 28
SLIDE = 36                 # на сколько пикселей плашка выезжает
MAX_IN_ROW = 3             # больше достижений сразу — последние объединяем в одну плашку «и ещё N»
DEFERRED_CHECK_MS = 2000   # как часто проверяем, не вышла ли игра из эксклюзивного полного экрана
RARE_PERCENT = 5.0
GOLD, VIOLET = "#fbbf24", "#a78bfa"


@dataclass
class OverlayItem:
    title: str                 # «Достижение получено», «Редкое достижение!», «Достижение GameShelf»
    name: str
    subtitle: str = ""         # «Valor Mortis · есть у 3,2% игроков»
    icon_url: str = ""
    icon_name: str = "trophy"  # своя иконка, если картинки нет
    accent: str = VIOLET
    game: str = ""             # название игры — для подписи объединённой плашки
    folded: list[OverlayItem] = field(default_factory=list)  # что спрятано в плашке «и ещё N»
    options: dict = field(default_factory=dict)  # свои угол/время/звук (проверка из настроек до сохранения)


# --------------------------------------------------------------------------- Windows: экран игры и полный экран

def _foreground_monitor() -> tuple[int, tuple[int, int], bool] | None:
    """Монитор с активным окном: HMONITOR, левый верхний угол и занимает ли окно его целиком."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))
        user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        m = info.rcMonitor
        covers = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
        return int(monitor), (m.left, m.top), covers
    except (OSError, AttributeError, ValueError):
        return None


def _screen_handle(screen) -> int | None:
    """HMONITOR экрана Qt (QScreen.name() — это название модели монитора, а не системное DISPLAYn)."""
    try:
        native = screen.nativeInterface()
        return int(native.handle()) if native is not None else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _game_screen():
    """Экран, где идёт игра (активное окно), и занимает ли она его целиком."""
    found = _foreground_monitor()
    if not found:
        return None, False
    handle, (left, top), covers = found
    screens = QGuiApplication.screens()
    for candidate in screens:
        if _screen_handle(candidate) == handle:
            return candidate, covers
    for candidate in screens:  # запасной путь: в Qt 6 начало экрана совпадает с системным даже при разном масштабе
        if candidate.geometry().topLeft() == QPoint(left, top):
            return candidate, covers
    return None, False


def _fold(items: list[OverlayItem], limit: int) -> list[OverlayItem]:
    """Не больше limit плашек подряд: всё, что дальше, — в одну «Новые достижения · и ещё N»."""
    flat = [part for item in items for part in (item.folded or [item])]
    limit = max(1, limit)
    if len(flat) <= limit:
        return flat
    keep = limit - 1
    rest = flat[keep:]
    games = list(dict.fromkeys(item.game for item in rest if item.game))
    if len(games) == 1:
        subtitle = games[0]
    elif games:
        subtitle = ntr(len(games), ("{n} игра: {games}", "{n} игры: {games}", "{n} игр: {games}"),
                       games=", ".join(games))
    else:
        subtitle = ""
    if len(rest) > 1:
        name = tr("И ещё {count}: «{name}» и другие", count=len(rest), name=rest[0].name)
    else:
        name = tr("И ещё {count}: «{name}»", count=len(rest), name=rest[0].name)
    summary = OverlayItem(tr("Новые достижения"), name, subtitle, icon_name="trophy",
                          accent=GOLD if any(item.accent == GOLD for item in rest) else VIOLET, folded=rest)
    return flat[:keep] + [summary]


def exclusive_fullscreen() -> bool:
    """Игра в эксклюзивном полном экране Direct3D — поверх неё обычные окна не видны."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        state = ctypes.c_int(0)
        ok = ctypes.WinDLL("shell32").SHQueryUserNotificationState(ctypes.byref(state)) == 0
        return ok and state.value == 3  # QUNS_RUNNING_D3D_FULL_SCREEN
    except (OSError, AttributeError):
        return False


# --------------------------------------------------------------------------- звук

def chime_path(assets_dir: Path) -> Path:
    """Короткий мягкий «дзинь» из двух нот — генерируем сами, чтобы не тащить в сборку мультимедиа Qt."""
    path = assets_dir / "achievement.wav"
    if path.exists():
        return path
    rate, frames = 44100, []
    for start, freq, length in ((0.0, 1046.5, 0.45), (0.09, 1568.0, 0.55)):  # до и соль шестой октавы
        frames.append((int(start * rate), freq, int(length * rate)))
    total = max(begin + length for begin, _f, length in frames)
    samples = [0.0] * total
    for begin, freq, length in frames:
        for i in range(length):
            t = i / rate
            envelope = math.exp(-6 * t) * min(1.0, i / 220)  # мягкая атака без щелчка и затухание
            samples[begin + i] += 0.28 * envelope * (math.sin(2 * math.pi * freq * t)
                                                     + 0.25 * math.sin(4 * math.pi * freq * t))
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
    except OSError:
        pass
    return path


def play_chime(path: Path) -> None:
    if sys.platform != "win32" or not path.exists():
        return
    try:
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except (ImportError, RuntimeError, OSError):
        pass


# --------------------------------------------------------------------------- плашка

class OverlayPopup(QWidget):
    def __init__(self, item: OverlayItem, images: ImageLoader | None):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(WIDTH, HEIGHT)
        self._item = item
        self._icon = None
        if item.icon_url and images is not None:
            images.load(item.icon_url, self._on_icon)

    def _on_icon(self, pixmap) -> None:
        if pixmap is not None and not pixmap.isNull():
            self._icon = pixmap
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        accent = QColor(self._item.accent)
        box = QRectF(3, 3, WIDTH - 6, HEIGHT - 6)

        background = QLinearGradient(box.topLeft(), box.bottomRight())
        background.setColorAt(0, QColor(32, 23, 54, 245))
        background.setColorAt(1, QColor(17, 12, 30, 245))
        border = QLinearGradient(box.topLeft(), box.topRight())
        border.setColorAt(0, accent)
        border.setColorAt(1, QColor("#d946ef"))
        painter.setPen(QPen(border, 1.6))
        painter.setBrush(background)
        painter.drawRoundedRect(box, 16, 16)

        icon_box = QRectF(20, (HEIGHT - 60) / 2, 60, 60)
        clip = QPainterPath()
        clip.addRoundedRect(icon_box, 12, 12)
        if self._icon is not None:
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(icon_box.toRect(), self._icon)
            painter.restore()
        else:
            glow = QColor(accent)
            glow.setAlpha(50)
            painter.fillPath(clip, glow)
            pixmap = icons.pixmap(self._item.icon_name, self._item.accent, 30)
            painter.drawPixmap(int(icon_box.center().x() - 15), int(icon_box.center().y() - 15), pixmap)

        left, right = 96, WIDTH - 20
        base = QFont("Segoe UI")  # явно: окно оверлея живёт отдельно от главного и не должно зависеть от его шрифта
        title_font = QFont(base)
        title_font.setPointSizeF(8.5)
        title_font.setWeight(QFont.Weight.Bold)
        name_font = QFont(base)
        name_font.setPointSizeF(11.5)
        name_font.setWeight(QFont.Weight.DemiBold)
        sub_font = QFont(base)
        sub_font.setPointSizeF(9)
        lines = ((title_font, accent, self._item.title.upper(), 26),
                 (name_font, QColor("#f1edff"), self._item.name, 51),
                 (sub_font, QColor("#a59bc4"), self._item.subtitle, 73))
        for font, color, text, baseline in lines:
            if not text:
                continue
            painter.setFont(font)
            painter.setPen(color)
            elided = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, right - left)
            painter.drawText(left, baseline, elided)


# --------------------------------------------------------------------------- очередь и показ

class AchievementOverlay(QObject):
    def __init__(self, settings: Settings, images: ImageLoader | None, assets_dir: Path, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._images = images
        self._assets_dir = assets_dir
        self._queue: list[OverlayItem] = []
        self._current: OverlayPopup | None = None
        self._item: OverlayItem | None = None
        self._fade: QPropertyAnimation | None = None
        self._slide: QPropertyAnimation | None = None
        self._deferred = QTimer(self, singleShot=True, interval=DEFERRED_CHECK_MS, timeout=self._next)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._hide_current)
        # Задержку проверки отсчитывает сам оверлей: окно настроек к этому времени обычно уже закрыто
        self._test_timer = QTimer(self, singleShot=True, timeout=self._show_test)
        self._test_options: dict = {}

    def enabled(self) -> bool:
        return bool(self._settings.get("overlay_enabled", True))

    def show_items(self, items: list[OverlayItem]) -> None:
        if not items:
            return
        # Считаем вместе с тем, что уже ждёт (в т. ч. пока игра в эксклюзивном полном экране), и с текущей плашкой
        limit = MAX_IN_ROW - (1 if self._current is not None else 0)
        self._queue = _fold(self._queue + list(items), limit)
        self._next()

    def test(self, delay_ms: int = 0, **options) -> None:
        """Пример для кнопки «Проверить оверлей». options — overlay_corner/overlay_seconds/overlay_sound из формы
        настроек: проверяем то, что выбрано, ничего не сохраняя."""
        self._test_options = dict(options)
        if delay_ms > 0:
            self._test_timer.start(delay_ms)
        else:
            self._test_timer.stop()
            self._show_test()

    def _show_test(self) -> None:
        self.show_items([OverlayItem(tr("Редкое достижение!"), tr("Порванное знамя"),
                                     tr("{game} · есть у {percent}% игроков", game="Valor Mortis",
                                        percent=i18n.decimal(3.2)),
                                     icon_name="trophy", accent=GOLD, game="Valor Mortis",
                                     options=self._test_options)])

    # ------------------------------------------------------------------ внутреннее

    def _next(self) -> None:
        if self._current is not None or not self._queue:
            return
        if exclusive_fullscreen():
            self._deferred.start()  # поверх эксклюзивного полного экрана не видно — покажем, когда игра его отпустит
            return
        item = self._queue.pop(0)
        popup = OverlayPopup(item, self._images)
        self._current, self._item = popup, item
        start, end = self._positions(popup)
        popup.move(start)
        popup.setWindowOpacity(0.0)
        popup.show()
        self._animate(popup, start, end, 0.0, 1.0, 320, QEasingCurve.Type.OutCubic)
        if self._option("overlay_sound", True):
            play_chime(chime_path(self._assets_dir))
        try:
            seconds = max(2, min(20, int(self._option("overlay_seconds", 6))))
        except (TypeError, ValueError):
            seconds = 6
        self._hide_timer.start(seconds * 1000)

    def _option(self, name: str, default):
        """Настройка для текущей плашки: своя (проверка из окна настроек) или сохранённая."""
        options = self._item.options if self._item is not None else {}
        return options[name] if name in options else self._settings.get(name, default)

    def _corner(self) -> str:
        corner = self._option("overlay_corner", "bottom_right")
        return corner if corner in CORNERS else "bottom_right"

    def _positions(self, popup: QWidget) -> tuple[QPoint, QPoint]:
        screen, covers = _game_screen()
        if screen is not None:
            # Игра на весь экран закрывает панель задач — тогда прижимаемся к краю самого экрана
            area = screen.geometry() if covers else screen.availableGeometry()
        else:
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        corner = self._corner()
        x = area.left() + MARGIN if corner.endswith("left") else area.right() - popup.width() - MARGIN + 1
        y = area.top() + MARGIN if corner.startswith("top") else area.bottom() - popup.height() - MARGIN + 1
        offset = -SLIDE if corner.startswith("top") else SLIDE
        return QPoint(x, y + offset), QPoint(x, y)

    def _animate(self, popup, start, end, opacity_from, opacity_to, duration, curve, finished=None) -> None:
        for old in (self._fade, self._slide):
            if old is not None:
                old.stop()
        # Анимации — дети плашки: удаляются вместе с ней, а не копятся до выхода из программы
        self._fade = QPropertyAnimation(popup, b"windowOpacity", popup)
        self._fade.setDuration(duration)
        self._fade.setStartValue(opacity_from)
        self._fade.setEndValue(opacity_to)
        self._slide = QPropertyAnimation(popup, b"pos", popup)
        self._slide.setDuration(duration)
        self._slide.setStartValue(start)
        self._slide.setEndValue(end)
        self._slide.setEasingCurve(curve)
        if finished:
            self._fade.finished.connect(finished)
        self._fade.start()
        self._slide.start()

    def _hide_current(self) -> None:
        popup = self._current
        if popup is None:
            return
        start = popup.pos()
        corner = self._corner()
        end = QPoint(start.x(), start.y() + (-SLIDE if corner.startswith("top") else SLIDE) // 2)
        self._animate(popup, start, end, popup.windowOpacity(), 0.0, 260, QEasingCurve.Type.InCubic, self._finish)

    def _finish(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current.deleteLater()
        self._current = self._item = self._fade = self._slide = None
        QTimer.singleShot(250, self, self._next)
