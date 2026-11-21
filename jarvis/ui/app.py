from __future__ import annotations

import logging
import os
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from jarvis import autostart, store
from jarvis.config import CONFIG_FILE, USER_DIR, data_path, env, get_config, resource_path
from jarvis.engine import Engine
from jarvis.services import services, submit
from jarvis.ui.bridge import Bridge
from jarvis.ui.hud import COLORS, UI_STATE, Hud
from jarvis.ui.theme import style_dialog, style_menu

log = logging.getLogger(__name__)

STATE_LABELS = {
    "reposo": "en espera",
    "conectando": "conectando",
    "escuchando": "escuchando",
    "pensando": "pensando",
    "hablando": "hablando",
    "silenciado": "silenciado",
    "error": "error",
}


def orb_icon(color: QColor) -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    glow = QColor(color)
    glow.setAlpha(70)
    p.setPen(QPen(glow, 10))
    p.drawEllipse(12, 12, 40, 40)
    p.setPen(QPen(color, 5))
    p.drawEllipse(12, 12, 40, 40)
    p.end()
    return QIcon(pix)


class _MenuHeader(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.title = QLabel("JARVIS")
        self.title.setStyleSheet("background: transparent; color: #F3F4F6; font-weight: 600; font-size: 9.5pt; letter-spacing: 0.6px;")
        self.state_lbl = QLabel(STATE_LABELS["reposo"])
        self.state_lbl.setStyleSheet("background: transparent; color: #8B93A1; font-size: 8.5pt;")

        top.addWidget(self.dot)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.state_lbl)
        layout.addLayout(top)
        self.set_state("reposo")

    def set_state(self, state: str) -> None:
        color = COLORS.get(state, COLORS["reposo"]).name()
        self.dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
        self.state_lbl.setText(STATE_LABELS.get(state, state))


class _MenuFooter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 6)
        lbl = QLabel("Desarrollado por RIBN Digital")
        lbl.setStyleSheet("background: transparent; color: #646D7C; font-size: 8pt;")
        layout.addWidget(lbl)
        layout.addStretch(1)


class Controller(QObject):
    def __init__(self, app: QApplication, bridge: Bridge, engine: Engine) -> None:
        super().__init__()
        self.app = app
        self.bridge = bridge
        self.engine = engine
        self.hud = Hud(bridge, self.talk, self.ask_text, self.show_menu)
        self.icons = {state: orb_icon(color) for state, color in COLORS.items()}
        self.tray = QSystemTrayIcon(self.icons["reposo"])
        self.menu = self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.setToolTip("JARVIS")
        bridge.state.connect(self._on_state)
        bridge.notify.connect(self._on_notify)
        bridge.open_training.connect(self.train_voice)
        bridge.open_settings.connect(self.open_settings)
        self._voice_dialog = None
        self._settings_dialog = None

        shown = store.load(UI_STATE, {}).get("mostrar_orbe", bool(get_config().get("interfaz.mostrar_orbe", True)))
        self.act_orb.setChecked(shown)
        self.hud.set_shown(shown, remember=False)
        self.tray.show()

        if not env("GEMINI_API_KEY"):
            QTimer.singleShot(450, lambda: self.open_settings(first_run=True))

    def _build_menu(self) -> QMenu:
        menu = style_menu(QMenu())

        self._menu_header = _MenuHeader()
        header_act = QWidgetAction(menu)
        header_act.setDefaultWidget(self._menu_header)
        menu.addAction(header_act)
        menu.addSeparator()

        hotkey = str(get_config().get("activacion.atajo", "ctrl+alt+j")).title()
        self.act_talk = QAction(f"Hablar\t{hotkey}", menu, triggered=self.talk)
        menu.addAction(self.act_talk)
        menu.addAction(QAction("Escribir comando...", menu, triggered=self.ask_text))
        menu.addAction(QAction("Resumen del día", menu, triggered=self.briefing))
        menu.addSeparator()

        self.act_mute = QAction("Silenciar micrófono", menu, checkable=True, triggered=self.toggle_mute)
        self.act_orb = QAction("Mostrar orbe", menu, checkable=True, triggered=self.hud.set_shown)
        self.act_auto = QAction("Iniciar con Windows", menu, checkable=True, triggered=self.toggle_autostart)
        self.act_auto.setChecked(autostart.is_enabled())
        for act in (self.act_mute, self.act_orb, self.act_auto):
            menu.addAction(act)
        menu.addSeparator()

        menu.addAction(QAction("Configuración...", menu, triggered=lambda: self.open_settings(False)))
        menu.addAction(QAction("Entrenar mi voz...", menu, triggered=self.train_voice))

        files_menu = style_menu(QMenu("Archivos", menu))
        files_menu.setMinimumWidth(190)
        files_menu.addAction(QAction("Editar config.toml", files_menu, triggered=self._open_config_file))
        files_menu.addAction(QAction("Carpeta de datos", files_menu, triggered=lambda: os.startfile(USER_DIR)))
        files_menu.addAction(
            QAction("Registros", files_menu, triggered=lambda: os.startfile(data_path("logs", "jarvis.log").parent))
        )
        menu.addMenu(files_menu)
        menu.addSeparator()

        menu.addAction(QAction("Salir", menu, triggered=self.app.quit))
        menu.addSeparator()

        footer_act = QWidgetAction(menu)
        footer_act.setDefaultWidget(_MenuFooter())
        menu.addAction(footer_act)
        return menu

    def _in_loop(self, func, *args) -> None:
        if self.engine.loop is not None:
            self.engine.loop.call_soon_threadsafe(func, *args)

    def _open_config_file(self) -> None:
        if not CONFIG_FILE.exists():
            CONFIG_FILE.write_text("", encoding="utf-8")
        os.startfile(CONFIG_FILE)

    def _refresh_menu_labels(self) -> None:
        hotkey = str(get_config().get("activacion.atajo", "ctrl+alt+j")).title()
        self.act_talk.setText(f"Hablar\t{hotkey}")

    def open_settings(self, first_run: bool = False) -> None:
        from jarvis.ui.settings_dialog import SettingsDialog

        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self._settings_dialog = SettingsDialog(self.engine, first_run=first_run, on_saved=self._refresh_menu_labels)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def talk(self) -> None:
        self._in_loop(self.engine.activate, "interfaz")

    def toggle_mute(self) -> None:
        def flip() -> None:
            muted = self.engine.toggle_mute()
            self.bridge.notify.emit("JARVIS", "Micrófono silenciado." if muted else "Micrófono activo.")

        self._in_loop(flip)

    def toggle_autostart(self, checked: bool) -> None:
        try:
            self.act_auto.setChecked(autostart.set_enabled(checked))
        except Exception as exc:
            log.exception("No se pudo cambiar el inicio automatico")
            self.tray.showMessage("JARVIS", f"No pude cambiar el inicio automático: {exc}")

    def ask_text(self) -> None:
        treat = str(get_config().get("usuario.tratamiento", "señor") or "señor")
        dlg = QInputDialog()
        dlg.setWindowTitle("JARVIS")
        dlg.setLabelText(f"¿Qué necesita, {treat}?")
        dlg.setOkButtonText("Enviar")
        dlg.setCancelButtonText("Cancelar")
        dlg.setMinimumWidth(400)
        style_dialog(dlg)
        if dlg.exec() != QInputDialog.DialogCode.Accepted:
            return
        text = dlg.textValue().strip()
        if not text:
            return
        future = submit(self.engine.handle_text(text, "texto"))
        if future is None:
            return
        self.bridge.subtitle.emit("usuario", text)

        def done(fut) -> None:
            try:
                answer = fut.result()
            except Exception as exc:
                answer = f"Error: {exc}"
            self.bridge.subtitle.emit("jarvis", answer)
            self.bridge.notify.emit("JARVIS", answer)

        future.add_done_callback(done)

    def briefing(self) -> None:
        submit(self.engine.briefing_now())

    def train_voice(self) -> None:
        from jarvis.ui.voice_dialog import VoiceTrainingDialog

        if self._voice_dialog is None or not self._voice_dialog.isVisible():
            self._voice_dialog = VoiceTrainingDialog(self.engine)
        self._voice_dialog.show()
        self._voice_dialog.raise_()
        self._voice_dialog.activateWindow()

    def show_menu(self, pos) -> None:
        self.menu.popup(pos)

    def _on_tray(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.talk()

    def _on_state(self, state: str) -> None:
        self.tray.setIcon(self.icons.get(state, self.icons["reposo"]))
        self.tray.setToolTip(f"JARVIS — {STATE_LABELS.get(state, state)}")
        self._menu_header.set_state(state)
        self.act_mute.setChecked(state == "silenciado")

    def _on_notify(self, title: str, text: str) -> None:
        self.tray.showMessage(title, text[:250], QSystemTrayIcon.Information, 7000)


def _log_uncaught(exc_type, exc, tb) -> None:
    log.error("[ERROR] Excepcion no capturada en la interfaz", exc_info=(exc_type, exc, tb))


def run_app() -> int:
    sys.excepthook = _log_uncaught
    threading.excepthook = lambda args: log.error(
        "[ERROR] Excepcion en el hilo %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("JARVIS")
    ico = resource_path("packaging", "jarvis.ico")
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    else:
        app.setWindowIcon(orb_icon(COLORS["reposo"]))

    bridge = Bridge()
    services.bridge = bridge
    engine = Engine()
    thread = threading.Thread(target=engine.run_forever, name="jarvis-engine", daemon=True)
    thread.start()

    controller = Controller(app, bridge, engine)
    app.aboutToQuit.connect(engine.stop)
    code = app.exec()
    engine.stop()
    thread.join(timeout=6)
    del controller
    return code
