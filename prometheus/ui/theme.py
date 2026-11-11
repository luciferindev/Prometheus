"""Dark theme, colour tokens and icons."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

BG = "#14161a"
SURFACE = "#1c1f25"
SURFACE_HI = "#252931"
BORDER = "#2f343d"
TEXT = "#e8eaed"
MUTED = "#9aa1ac"
ACCENT = "#ff7a33"
ACCENT_HI = "#ff9455"
GOOD = "#4cc38a"
WARN = "#f5b83d"
BAD = "#f2555a"
INFO = "#5aa9f0"

LEVEL_COLOR = {"ok": GOOD, "warn": WARN, "missing": BAD, "error": BAD, "info": INFO}

_ICONS = {
    "dashboard": "M3 3h8v10H3zM13 3h8v6h-8zM13 11h8v10h-8zM3 15h8v6H3z",
    "settings": "M12 8a4 4 0 100 8 4 4 0 000-8zm9 4l-2.1-.9-.5-1.3.9-2.1-1.9-1.9-2.1.9-1.3-.5L13.1 4h-2.2l-.9 2.2-1.3.5-2.1-.9-1.9 1.9.9 2.1-.5 1.3L3 12v0l2.1.9.5 1.3-.9 2.1 1.9 1.9 2.1-.9 1.3.5.9 2.2h2.2l.9-2.2 1.3-.5 2.1.9 1.9-1.9-.9-2.1.5-1.3z",
    "eye": "M12 5C6.5 5 2.7 9.2 1.5 12c1.2 2.8 5 7 10.5 7s9.3-4.2 10.5-7C21.300 9.200 17.500 5 12 5zm0 11a4 4 0 110-8 4 4 0 010 8zm0-6a2 2 0 100 4 2 2 0 000-4z",
    "sword": "M19 3l2 0 0 2-9.5 9.5 1.5 1.5-1.4 1.4-1.5-1.500-3.200 3.200-1.400-1.400 3.200-3.200-1.500-1.500 1.400-1.400 1.500 1.500z",
    "logs": "M4 5h16v2H4zM4 11h16v2H4zM4 17h10v2H4z",
    "plug": "M9 2v6H7v4a5 5 0 004 4.9V22h2v-5.1A5 5 0 0017 12V8h-2V2h-2v6h-2V2z",
    "stop": "M6 6h12v12H6z",
    "wand": "M4 20l11-11 2 2L6 22zM17 3l1 2 2 1-2 1-1 2-1-2-2-1 2-1z",
    "ok": "M9.5 16.2L5.3 12l-1.4 1.4 5.6 5.6 11-11-1.4-1.4z",
    "warn": "M12 3L2 20h20zm-1 6h2v6h-2zm0 8h2v2h-2z",
    "missing": "M18.3 5.7L12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7 2.9 18.3 9.2 12 2.9 5.7 4.3 4.3l6.3 6.3 6.3-6.3z",
    "info": "M11 10h2v8h-2zM11 6h2v2h-2zM12 2a10 10 0 100 20 10 10 0 000-20z",
}
_ICONS["error"] = _ICONS["missing"]


def icon(name: str, color: str = TEXT, size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))


def pixmap(name: str, color: str = TEXT, size: int = 20) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path fill="{color}" fill-rule="evenodd" d="{_ICONS[name]}"/></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ratio = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    pm = QPixmap(QSize(int(size * ratio), int(size * ratio)))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, SURFACE),
        (QPalette.ColorRole.AlternateBase, SURFACE_HI), (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Button, SURFACE_HI),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#141414"), (QPalette.ColorRole.ToolTipBase, SURFACE_HI),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, MUTED),
        (QPalette.ColorRole.Link, ACCENT_HI),
    ):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6b717b"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


QSS = f"""
* {{ font-size: 10.5pt; }}
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog, QWizard, QWizardPage {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_HI}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}

#Sidebar {{ background: {SURFACE}; border: none; border-right: 1px solid {BORDER}; outline: none; padding: 8px 6px; }}
#Sidebar::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 0; color: {MUTED}; }}
#Sidebar::item:hover {{ background: {SURFACE_HI}; color: {TEXT}; }}
#Sidebar::item:selected {{ background: {SURFACE_HI}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
#Brand {{ font-size: 13pt; font-weight: 700; letter-spacing: 2px; color: {TEXT}; padding: 16px 16px 8px 16px; background: {SURFACE}; }}
#SidebarWrap {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}

#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
#CardTitle {{ color: {MUTED}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
#PageTitle {{ font-size: 18pt; font-weight: 600; }}
#Muted {{ color: {MUTED}; }}
#Value {{ font-size: 15pt; font-weight: 600; }}
#Mono {{ font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace; }}
#Code {{ font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 22pt; font-weight: 700;
        letter-spacing: 4px; color: {ACCENT_HI}; padding: 10px; background: {SURFACE_HI}; border-radius: 10px; }}
#Banner {{ background: #3a2f16; border: 1px solid #6b5420; border-radius: 10px; padding: 10px 14px; color: #f6dfa6; }}
#BannerBad {{ background: #3b1c1f; border: 1px solid #7a2f34; border-radius: 10px; padding: 10px 14px; color: #f7c2c4; }}

QPushButton {{ background: {SURFACE_HI}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: #4a515d; background: #2b3039; }}
QPushButton:pressed {{ background: #20242b; }}
QPushButton:disabled {{ color: #6b717b; background: {SURFACE}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #1a1208; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HI}; }}
QPushButton#Primary:disabled {{ background: #5a3a25; border-color: #5a3a25; color: #a08a7a; }}
QPushButton#Danger {{ background: #5c1f23; border-color: {BAD}; color: #ffd9da; font-weight: 600; }}
QPushButton#Danger:hover {{ background: #7a262b; }}
QPushButton#Danger:disabled {{ background: {SURFACE}; border-color: {BORDER}; color: #6b717b; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {ACCENT}; selection-color: #141414; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_HI}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    selection-color: #141414; outline: none; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}

QProgressBar {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 6px; height: 10px; text-align: center;
    color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QProgressBar#Health::chunk {{ background: {BAD}; }}
QProgressBar#Food::chunk {{ background: {WARN}; }}

QTreeWidget {{ background: transparent; border: none; outline: none; }}
QTreeWidget::item {{ padding: 3px 4px; }}
QTableWidget, QListWidget#Events {{ background: transparent; border: none; outline: none; gridline-color: transparent; }}
QTableWidget::item, QListWidget#Events::item {{ padding: 5px 6px; border-bottom: 1px solid {BORDER}; }}
QListWidget#Events::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {SURFACE_HI}; }}
QHeaderView, QTableCornerButton::section {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; border: none; border-bottom: 1px solid {BORDER}; color: {MUTED};
    padding: 6px; font-size: 9pt; font-weight: 600; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a404a; border-radius: 4px; min-height: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3a404a; border-radius: 4px; min-width: 28px; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QScrollArea {{ border: none; background: transparent; }}
"""
