from __future__ import annotations

import ctypes
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QMenu, QWidget

from jarvis.config import data_path


def _check_icon_url() -> str:
    path: Path = data_path("cache", "ui_check.png")
    if not path.exists():
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#38BDF8"))
        p.drawRoundedRect(1, 1, 30, 30, 7, 7)
        check = QPainterPath(QPointF(8.5, 16.5))
        check.lineTo(QPointF(13.5, 21.5))
        check.lineTo(QPointF(23.5, 10.5))
        pen = QPen(QColor("#0B0F14"), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(check)
        p.end()
        pix.save(str(path), "PNG")
    return path.resolve().as_posix()


def _menu_qss() -> str:
    chk = _check_icon_url()
    return f"""
QMenu {{
    background-color: #181B21;
    color: #E6E9EF;
    border: 1px solid #2A2F3A;
    border-radius: 10px;
    padding: 6px;
    font-family: "Segoe UI";
    font-size: 9.5pt;
}}
QMenu::item {{
    padding: 7px 14px 7px 10px;
    margin: 1px 0px;
    border-radius: 6px;
    background-color: transparent;
}}
QMenu::item:selected {{
    background-color: #252B36;
    color: #FFFFFF;
}}
QMenu::item:disabled {{
    color: #6B7280;
    background-color: transparent;
}}
QMenu::separator {{
    height: 1px;
    background: #262B35;
    margin: 5px 8px;
}}
QMenu::indicator {{
    width: 15px;
    height: 15px;
    margin-left: 6px;
    margin-right: 4px;
    border-radius: 4px;
    border: 1px solid #3C4352;
    background-color: #121419;
}}
QMenu::indicator:checked {{
    border: none;
    background-color: transparent;
    image: url("{chk}");
}}
QMenu::right-arrow {{
    width: 10px;
    height: 10px;
    margin-right: 6px;
}}
"""


def _dialog_qss() -> str:
    chk = _check_icon_url()
    return f"""
QDialog, QInputDialog {{
    background-color: #14161B;
    color: #E6E9EF;
    font-family: "Segoe UI";
    font-size: 9.5pt;
}}
QWidget {{
    color: #E6E9EF;
    font-family: "Segoe UI";
}}
QTabWidget::pane {{
    border: none;
    border-top: 1px solid #252A34;
    background-color: transparent;
    padding-top: 10px;
}}
QTabBar::tab {{
    background: transparent;
    color: #8B93A1;
    padding: 8px 16px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: #D1D5DB;
}}
QTabBar::tab:selected {{
    color: #F9FAFB;
    border-bottom: 2px solid #38BDF8;
}}
QGroupBox {{
    background-color: #1A1D24;
    border: 1px solid #262B35;
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
    color: #CBD5E1;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: #9CA3AF;
    font-size: 8.5pt;
}}
QLineEdit, QPlainTextEdit, QComboBox {{
    background-color: #101216;
    color: #F3F4F6;
    border: 1px solid #2A2F3A;
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: #0284C7;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid #38BDF8;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: #181B21;
    color: #E6E9EF;
    border: 1px solid #2A2F3A;
    selection-background-color: #252B36;
}}
QCheckBox {{
    spacing: 8px;
    color: #D1D5DB;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #3C4352;
    background-color: #101216;
}}
QCheckBox::indicator:checked {{
    border: none;
    background-color: transparent;
    image: url("{chk}");
}}
QPushButton {{
    background-color: #222630;
    color: #E6E9EF;
    border: 1px solid #2F3542;
    border-radius: 6px;
    padding: 6px 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: #2B303D;
    border-color: #3C4455;
}}
QPushButton:pressed {{
    background-color: #1E222B;
}}
QPushButton:disabled {{
    color: #6B7280;
    background-color: #191C22;
    border-color: #232730;
}}
QPushButton#primary {{
    background-color: #0EA5E9;
    color: #090C10;
    border: 1px solid #0EA5E9;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: #38BDF8;
    border-color: #38BDF8;
}}
QPushButton#primary:pressed {{
    background-color: #0284C7;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2F3542;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QLabel#muted {{
    color: #8B93A1;
    font-size: 8.5pt;
}}
QLabel#credit {{
    color: #6B7280;
    font-size: 8.5pt;
}}
QLabel#welcome {{
    background-color: #1A1D24;
    border: 1px solid #262B35;
    border-left: 3px solid #38BDF8;
    border-radius: 6px;
    padding: 10px 12px;
    color: #E6E9EF;
}}
"""


def apply_dark_titlebar(widget: QWidget) -> None:
    try:
        hwnd = int(widget.winId())
        val = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def style_menu(menu: QMenu) -> QMenu:
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setStyleSheet(_menu_qss())
    menu.setMinimumWidth(240)
    return menu


def style_dialog(dialog: QDialog) -> None:
    dialog.setFont(QFont("Segoe UI", 9))
    dialog.setStyleSheet(_dialog_qss())
    apply_dark_titlebar(dialog)
