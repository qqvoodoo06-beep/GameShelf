"""Фиолетовая тёмная тема: цвета и таблица стилей Qt (QSS)."""
from __future__ import annotations

from pathlib import Path
from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from . import icons

C = {
    "bg": "#0e0a18",
    "sidebar": "#130e21",
    "surface": "#1a1429",
    "surface_alt": "#211a35",
    "surface_hover": "#251d3f",
    "input": "#150f24",
    "border": "#2a2142",
    "border_strong": "#3d3160",
    "primary": "#8b5cf6",
    "primary_hover": "#9d76f8",
    "primary_light": "#c4b5fd",
    "accent": "#d946ef",
    "accent_hover": "#e46ff3",
    "text": "#f1edff",
    "text_muted": "#a59bc4",
    "text_dim": "#6e6490",
    "danger": "#f87171",
    "star": "#fbbf24",
}

QSS = Template(r"""
QWidget { color: $text; }
QMainWindow, #Central, QDialog { background: $bg; }
QToolTip { background: $surface_alt; color: $text; border: 1px solid $border_strong; border-radius: 6px; padding: 6px 8px; }

/* ---------- Боковая панель ---------- */
#Sidebar { background: $sidebar; border: none; border-right: 1px solid $border; }
#Logo { font-size: 15pt; font-weight: 700; }
#LogoSub { color: $text_dim; font-size: 8.5pt; }
#SidebarSection { color: $text_dim; font-size: 8pt; font-weight: 700; padding: 16px 14px 6px 14px; }
#Attribution { color: $text_dim; font-size: 8.5pt; padding: 10px 14px 2px 14px; }

/* ---------- Заголовки и текст ---------- */
#PageTitle { font-size: 21pt; font-weight: 700; }
#PageSubtitle, #Muted { color: $text_muted; }
#SectionTitle { font-size: 13pt; font-weight: 600; }
#SectionHint { color: $text_dim; }
#Caption { color: $text_dim; font-size: 8.5pt; font-weight: 700; }
#EmptyTitle { font-size: 15pt; font-weight: 600; }
#EmptyText { color: $text_muted; font-size: 10.5pt; }
#ErrorText { color: $danger; }
#SuccessText { color: #34d399; }

/* ---------- Карточки игр ---------- */
#GameCard { background: $surface; border: 1px solid $border; border-radius: 14px; }
#GameCard:hover { background: $surface_hover; border: 1px solid $primary; }
#CardTitle { font-size: 11pt; font-weight: 600; }
#CardMeta { color: $text_muted; font-size: 9pt; }
#CardScore { color: $text_muted; font-size: 9pt; font-weight: 600; }
QToolButton#CardAdd { background: $primary; border: none; border-radius: 17px; }
QToolButton#CardAdd:hover { background: $primary_hover; }
QToolButton#CardAdd::menu-indicator { image: none; width: 0; }

/* ---------- Панели и статистика ---------- */
#Panel { background: $surface; border: 1px solid $border; border-radius: 16px; }
#StatValue { font-size: 22pt; font-weight: 700; }
#StepNum { background: rgba(139, 92, 246, 46); color: $primary_light; border-radius: 14px; font-weight: 700; }

/* ---------- Прокрутка ---------- */
QScrollArea, #ScrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 3px 4px 3px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 3px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: $primary; }
QScrollBar[scrollable="false"]::handle:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 3px 4px 3px 4px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 3px; min-width: 40px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ---------- Поля ввода ---------- */
QLineEdit, QPlainTextEdit {
    background: $input; border: 1px solid $border; border-radius: 10px; padding: 8px 12px;
    color: $text; selection-background-color: $primary; selection-color: #ffffff;
}
QLineEdit:hover, QPlainTextEdit:hover { border-color: $border_strong; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: $primary; }
QLineEdit#BigSearch { font-size: 12pt; padding: 10px 14px; border-radius: 12px; }

QComboBox { background: $input; border: 1px solid $border; border-radius: 10px; padding: 8px 12px; color: $text; }
QComboBox:hover { border-color: $border_strong; }
QComboBox:on { border-color: $primary; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { image: url("$chevron"); width: 14px; height: 14px; }
QComboBox QAbstractItemView {
    background: $surface; border: 1px solid $border_strong; padding: 4px; outline: none;
    selection-background-color: $surface_hover; selection-color: $text;
}
QComboBox QAbstractItemView::item { padding: 7px 10px; border-radius: 6px; min-height: 22px; }

/* ---------- Кнопки ---------- */
QPushButton {
    background: $surface_alt; border: 1px solid $border; border-radius: 10px;
    padding: 8px 16px; color: $text; font-weight: 600;
}
QPushButton:hover { background: $surface_hover; border-color: $border_strong; }
QPushButton:pressed { background: $surface; }
QPushButton:disabled { color: $text_dim; }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 $primary, stop:1 $accent);
    border: none; color: #ffffff; padding: 9px 20px;
}
QPushButton#Primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 $primary_hover, stop:1 $accent_hover); }
QPushButton#Primary:pressed { background: $primary; }
QPushButton#Primary:disabled { background: $surface_alt; color: $text_dim; }
/* Красная кнопка с рамкой: сразу видно, что это действие, а не подпись */
QPushButton#Danger { background: rgba(248, 113, 113, 14); border: 1px solid rgba(248, 113, 113, 80); color: $danger; }
QPushButton#Danger:hover { background: rgba(248, 113, 113, 34); border-color: rgba(248, 113, 113, 150); }
QPushButton#Danger:pressed { background: rgba(248, 113, 113, 22); }
QPushButton#Ghost { background: transparent; border: none; color: $text_muted; font-weight: 400; }
QPushButton#Ghost:hover { color: $text; }
QPushButton#Link { background: transparent; border: none; color: $primary_light; padding: 4px 0; text-align: left; }
QPushButton#Link:hover { color: #ffffff; }
QPushButton#StatusOption {
    text-align: left; padding: 9px 12px; background: transparent;
    border: 1px solid $border; border-radius: 10px; font-weight: 400;
}
QPushButton#StatusOption:hover { background: $surface_hover; }
QPushButton#StatusOption:checked { background: rgba(139, 92, 246, 46); border: 1px solid $primary; font-weight: 600; }

/* ---------- Меню и диалоги ---------- */
QMenu { background: $surface; border: 1px solid $border_strong; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 22px 8px 12px; border-radius: 6px; color: $text; }
QMenu::item:selected { background: $surface_hover; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QMenu::icon { padding-left: 8px; }
QMessageBox QLabel { color: $text; }

/* ---------- Карточка игры (окно подробностей) ---------- */
#HeroTitle { color: #ffffff; font-size: 24pt; font-weight: 700; background: transparent; }
#HeroMeta { color: #d9d1f5; font-size: 10.5pt; background: transparent; }
#InfoKey { color: $text_dim; }
#Description { color: $text_muted; font-size: 10.5pt; }
#Hint { color: $text_muted; background: transparent; }
#Warning { color: #fbbf24; background: transparent; }
#DropZone {
    background: rgba(24, 16, 44, 232); border: 2px dashed $primary_light; border-radius: 18px;
    color: #ffffff; font-size: 15pt; font-weight: 700;
}
#DropZone[warn="true"] { border-color: #fbbf24; color: #fde68a; }

/* ---------- Флажки ---------- */
QCheckBox { spacing: 10px; color: $text; background: transparent; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 5px; border: 1px solid $border_strong; background: $input;
}
QCheckBox::indicator:hover { border-color: $primary; }
QCheckBox::indicator:checked { background: $primary; border-color: $primary; image: url("$check"); }
QCheckBox:disabled { color: $text_dim; }

/* ---------- Учёт времени ---------- */
#NowPlaying { background: rgba(52, 211, 153, 22); border: 1px solid rgba(52, 211, 153, 70); border-radius: 12px; }
#NowPlayingCaption { color: #34d399; font-size: 8pt; font-weight: 700; background: transparent; }
#NowPlayingName { font-weight: 600; background: transparent; }
#NowPlayingTime { color: $text_muted; font-size: 9pt; background: transparent; }
#Banner { background: rgba(139, 92, 246, 30); border: 1px solid rgba(139, 92, 246, 90); border-radius: 12px; }
#SessionRow { color: $text_muted; }

/* ---------- Достижения ---------- */
QProgressBar { background: $border; border: none; border-radius: 4px; }
QProgressBar::chunk {
    border-radius: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 $primary, stop:1 $accent);
}
#AchRow { background: $surface; border: 1px solid $border; border-radius: 12px; }
#MetaTile { background: $surface; border: 1px solid $border; border-radius: 14px; }
#MetaTile[unlocked="true"] { border: 1px solid rgba(251, 191, 36, 110); }

#Toast {
    background: $surface_alt; border: 1px solid $primary; border-radius: 12px;
    padding: 11px 20px; color: $text; font-weight: 600;
}
""")


def apply_theme(app: QApplication, asset_dir: Path) -> None:
    asset_dir.mkdir(parents=True, exist_ok=True)
    chevron = asset_dir / "chevron-down.svg"
    chevron.write_text(icons.svg_markup("chevron-down", C["text_muted"], 2.2), encoding="utf-8")
    check = asset_dir / "check.svg"
    check.write_text(icons.svg_markup("check", "#ffffff", 3), encoding="utf-8")

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: C["bg"],
        QPalette.ColorRole.WindowText: C["text"],
        QPalette.ColorRole.Base: C["input"],
        QPalette.ColorRole.AlternateBase: C["surface"],
        QPalette.ColorRole.Text: C["text"],
        QPalette.ColorRole.Button: C["surface_alt"],
        QPalette.ColorRole.ButtonText: C["text"],
        QPalette.ColorRole.Highlight: C["primary"],
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: C["surface_alt"],
        QPalette.ColorRole.ToolTipText: C["text"],
        QPalette.ColorRole.PlaceholderText: C["text_dim"],
        QPalette.ColorRole.Link: C["primary_light"],
        QPalette.ColorRole.Mid: C["border"],
        QPalette.ColorRole.Dark: C["sidebar"],
        QPalette.ColorRole.Light: C["border_strong"],
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(QSS.substitute(C, chevron=chevron.as_posix(), check=check.as_posix()))
