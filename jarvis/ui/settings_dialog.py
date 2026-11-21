from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from jarvis.config import (
    USER_DIR,
    data_path,
    env,
    get_config,
    read_env_file,
    save_env_values,
    update_config_values,
)
from jarvis.ui.theme import style_dialog

log = logging.getLogger(__name__)

GEMINI_VOICES = ["Charon", "Orus", "Puck", "Kore", "Fenrir", "Aoede", "Zephyr", "Leda"]
COUNTRIES = ["AR", "MX", "ES", "CO", "CL", "PE", "UY", "US"]


class _TaskSignals(QObject):
    finished = Signal(str, bool, str)


def _secret_row(value: str, placeholder: str = "") -> tuple[QWidget, QLineEdit]:
    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    edit = QLineEdit(value)
    edit.setPlaceholderText(placeholder)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    toggle = QPushButton("Mostrar")
    toggle.setCheckable(True)
    toggle.setFixedWidth(74)

    def _flip(checked: bool) -> None:
        edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        toggle.setText("Ocultar" if checked else "Mostrar")

    toggle.toggled.connect(_flip)
    row.addWidget(edit, 1)
    row.addWidget(toggle)
    return container, edit


class SettingsDialog(QDialog):
    def __init__(self, engine, first_run: bool = False, on_saved=None) -> None:
        super().__init__(None)
        self.engine = engine
        self.first_run = first_run
        self.on_saved = on_saved
        self.signals = _TaskSignals()
        self.signals.finished.connect(self._on_task_finished)

        self.setWindowTitle("JARVIS — Configuración inicial" if first_run else "JARVIS — Configuración")
        self.setMinimumSize(600, 580)
        style_dialog(self)

        cfg = get_config()
        stored_env = read_env_file()

        def cur_env(key: str) -> str:
            return stored_env.get(key) or env(key)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        if first_run:
            banner = QLabel(
                "<b>Configuración inicial</b><br>"
                "Pegá tu clave de <b>Google AI Studio (Gemini)</b> para comenzar. "
                "Tus claves y datos se guardan únicamente en esta PC."
            )
            banner.setObjectName("welcome")
            banner.setWordWrap(True)
            root.addWidget(banner)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        general_tab = QWidget()
        gen_layout = QVBoxLayout(general_tab)

        gemini_box = QGroupBox("Gemini (obligatorio)")
        gemini_form = QFormLayout(gemini_box)

        key_widget, self.gemini_key = _secret_row(cur_env("GEMINI_API_KEY"), "AIza...")
        gemini_form.addRow("GEMINI_API_KEY:", key_widget)

        key_actions = QHBoxLayout()
        btn_get_key = QPushButton("Obtener clave gratis (AI Studio)")
        btn_get_key.clicked.connect(lambda: webbrowser.open("https://aistudio.google.com/apikey"))
        self.btn_test_key = QPushButton("Probar clave")
        self.btn_test_key.clicked.connect(self._test_gemini_key)
        self.lbl_key_status = QLabel("")
        key_actions.addWidget(btn_get_key)
        key_actions.addWidget(self.btn_test_key)
        key_actions.addWidget(self.lbl_key_status, 1)
        gemini_form.addRow("", key_actions)

        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.addItems(GEMINI_VOICES)
        self.voice_combo.setCurrentText(str(cfg.get("gemini.voz", "Charon")))
        gemini_form.addRow("Voz en vivo:", self.voice_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("En vivo (Gemini Live)", "live")
        self.mode_combo.addItem("Respaldo (Flash-Lite + Edge TTS)", "respaldo")
        cur_mode = str(cfg.get("gemini.modo_voz", "live"))
        idx = self.mode_combo.findData(cur_mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        gemini_form.addRow("Modo de voz:", self.mode_combo)
        gen_layout.addWidget(gemini_box)

        user_box = QGroupBox("Usuario y activación")
        user_form = QFormLayout(user_box)

        self.user_name = QLineEdit(str(cfg.get("usuario.nombre", "") or ""))
        self.user_name.setPlaceholderText("Opcional (ej. Alex)")
        user_form.addRow("Tu nombre:", self.user_name)

        self.user_treat = QLineEdit(str(cfg.get("usuario.tratamiento", "señor") or "señor"))
        self.user_treat.setPlaceholderText("señor / señora / tu nombre")
        user_form.addRow("Tratamiento:", self.user_treat)

        self.user_city = QLineEdit(str(cfg.get("usuario.ciudad", "") or ""))
        self.user_city.setPlaceholderText("Ej. Buenos Aires, Madrid, Ciudad de México")
        user_form.addRow("Ciudad (clima):", self.user_city)

        self.country_combo = QComboBox()
        self.country_combo.setEditable(True)
        self.country_combo.addItems(COUNTRIES)
        self.country_combo.setCurrentText(str(cfg.get("resumen.noticias_pais", "AR") or "AR"))
        user_form.addRow("País (noticias):", self.country_combo)

        self.chk_wake = QCheckBox("Activar palabra de activación («hey jarvis»)")
        self.chk_wake.setChecked(bool(cfg.get("activacion.wake_word", True)))
        user_form.addRow("", self.chk_wake)

        self.hotkey_edit = QLineEdit(str(cfg.get("activacion.atajo", "ctrl+alt+j") or "ctrl+alt+j"))
        self.hotkey_edit.setPlaceholderText("ctrl+alt+j")
        user_form.addRow("Atajo global:", self.hotkey_edit)
        gen_layout.addWidget(user_box)
        gen_layout.addStretch(1)

        self.tabs.addTab(general_tab, "General")

        context_tab = QWidget()
        ctx_layout = QVBoxLayout(context_tab)

        ctx_info = QLabel(
            "<b>Perfil personal (opcional)</b> — Se guarda solo en tu PC (<code>data/perfil.md</code>) "
            "y se envía a tu propia cuenta de Gemini para personalizar las respuestas."
        )
        ctx_info.setWordWrap(True)
        ctx_layout.addWidget(ctx_info)

        self.profile_edit = QPlainTextEdit()
        self.profile_edit.setPlaceholderText(
            "Ejemplo:\n"
            "- Soy desarrollador de software y estudio ingeniería.\n"
            "- Mi playlist para concentrarme se llama Deep Focus.\n"
            "- Prefiero respuestas bien breves y directas."
        )
        profile_file = data_path("perfil.md")
        if profile_file.exists():
            try:
                self.profile_edit.setPlainText(profile_file.read_text("utf-8"))
            except OSError:
                pass
        ctx_layout.addWidget(self.profile_edit, 1)

        ctx_buttons = QHBoxLayout()
        btn_instr = QPushButton("Abrir instrucciones permanentes (instrucciones.md)")
        btn_instr.clicked.connect(self._open_instructions_file)
        btn_folder = QPushButton("Abrir carpeta de datos")
        btn_folder.clicked.connect(lambda: os.startfile(USER_DIR))
        ctx_buttons.addWidget(btn_instr)
        ctx_buttons.addWidget(btn_folder)
        ctx_buttons.addStretch(1)
        ctx_layout.addLayout(ctx_buttons)

        self.tabs.addTab(context_tab, "Tu contexto")

        integ_tab = QWidget()
        integ_layout = QVBoxLayout(integ_tab)

        sp_box = QGroupBox("Spotify (Premium, opcional)")
        sp_form = QFormLayout(sp_box)
        self.spotify_id = QLineEdit(cur_env("SPOTIFY_CLIENT_ID"))
        self.spotify_id.setPlaceholderText("Client ID de developer.spotify.com/dashboard")
        sp_form.addRow("SPOTIFY_CLIENT_ID:", self.spotify_id)
        sp_hint = QLabel("Redirect URI requerido en Spotify Dashboard: <code>http://127.0.0.1:8888/callback</code>")
        sp_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sp_form.addRow("", sp_hint)
        sp_row = QHBoxLayout()
        self.lbl_spotify = QLabel(self._spotify_status_text())
        self.btn_spotify = QPushButton("Vincular Spotify")
        self.btn_spotify.clicked.connect(self._link_spotify)
        btn_sp_dash = QPushButton("Abrir Dashboard")
        btn_sp_dash.clicked.connect(lambda: webbrowser.open("https://developer.spotify.com/dashboard"))
        sp_row.addWidget(self.lbl_spotify, 1)
        sp_row.addWidget(btn_sp_dash)
        sp_row.addWidget(self.btn_spotify)
        sp_form.addRow("Estado:", sp_row)
        integ_layout.addWidget(sp_box)

        g_box = QGroupBox("Google Calendar, Gmail y Tasks (opcional)")
        g_layout = QVBoxLayout(g_box)
        g_row = QHBoxLayout()
        self.lbl_google = QLabel(self._google_status_text())
        btn_g_import = QPushButton("Importar credentials JSON...")
        btn_g_import.clicked.connect(self._import_google_json)
        self.btn_google = QPushButton("Vincular Google")
        self.btn_google.clicked.connect(self._link_google)
        g_row.addWidget(self.lbl_google, 1)
        g_row.addWidget(btn_g_import)
        g_row.addWidget(self.btn_google)
        g_layout.addLayout(g_row)
        integ_layout.addWidget(g_box)

        tg_box = QGroupBox("Telegram (opcional)")
        tg_form = QFormLayout(tg_box)
        self.chk_telegram = QCheckBox("Activar bot de Telegram")
        self.chk_telegram.setChecked(bool(cfg.get("canales.telegram", True)))
        tg_form.addRow("", self.chk_telegram)
        tg_tok_widget, self.tg_token = _secret_row(cur_env("TELEGRAM_BOT_TOKEN"), "Token de @BotFather")
        tg_form.addRow("TELEGRAM_BOT_TOKEN:", tg_tok_widget)
        self.tg_chats = QLineEdit(cur_env("TELEGRAM_CHAT_IDS"))
        self.tg_chats.setPlaceholderText("IDs autorizados separados por coma (ej. 123456789)")
        tg_form.addRow("TELEGRAM_CHAT_IDS:", self.tg_chats)
        integ_layout.addWidget(tg_box)

        http_box = QGroupBox("Atajo de Siri / API en red local (opcional)")
        http_form = QFormLayout(http_box)
        self.chk_http = QCheckBox("Activar servidor HTTP local")
        self.chk_http.setChecked(bool(cfg.get("canales.http", True)))
        http_form.addRow("", self.chk_http)

        http_tok_row = QHBoxLayout()
        http_tok_widget, self.http_token = _secret_row(cur_env("JARVIS_HTTP_TOKEN"), "Token secreto aleatorio")
        btn_gen_tok = QPushButton("Generar")
        btn_gen_tok.clicked.connect(lambda: self.http_token.setText(secrets.token_urlsafe(32)))
        http_tok_row.addWidget(http_tok_widget, 1)
        http_tok_row.addWidget(btn_gen_tok)
        http_form.addRow("JARVIS_HTTP_TOKEN:", http_tok_row)

        from jarvis.channels.http_api import local_ip

        port = int(cfg.get("canales.http_puerto", 8765))
        endpoint = f"http://{local_ip()}:{port}/comando"
        url_row = QHBoxLayout()
        lbl_url = QLabel(f"<code>{endpoint}</code>")
        lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_copy = QPushButton("Copiar URL")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(endpoint))
        url_row.addWidget(lbl_url, 1)
        url_row.addWidget(btn_copy)
        http_form.addRow("Dirección local:", url_row)
        integ_layout.addWidget(http_box)

        integ_layout.addStretch(1)
        self.tabs.addTab(integ_tab, "Integraciones")

        footer = QHBoxLayout()
        self.lbl_status = QLabel("Desarrollado por RIBN Digital")
        self.lbl_status.setObjectName("credit")
        self.lbl_status.setToolTip(f"Carpeta de datos: {USER_DIR}")

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Guardar y aplicar")
        self.btn_save.setObjectName("primary")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._save)

        footer.addWidget(self.lbl_status, 1)
        footer.addWidget(btn_cancel)
        footer.addWidget(self.btn_save)
        root.addLayout(footer)


    def _spotify_status_text(self) -> str:
        try:
            from jarvis.tools import spotify

            return "Vinculado" if spotify.is_linked() else "No vinculado"
        except Exception:
            return "No vinculado"

    def _google_status_text(self) -> str:
        try:
            from jarvis.tools import google_ws

            if google_ws.is_linked():
                return "Vinculado"
            if google_ws.credentials_file().exists():
                return "JSON cargado (falta vincular)"
            return "No vinculado"
        except Exception:
            return "No vinculado"

    def _open_instructions_file(self) -> None:
        path = data_path("instrucciones.md")
        if not path.exists():
            path.write_text("# Instrucciones para JARVIS\n\n", encoding="utf-8")
        os.startfile(path)


    def _test_gemini_key(self) -> None:
        key = self.gemini_key.text().strip()
        if not key:
            self.lbl_key_status.setText("Pegá una clave primero.")
            return
        self.btn_test_key.setEnabled(False)
        self.lbl_key_status.setText("Probando...")

        def work() -> None:
            try:
                from jarvis.brain.text import make_client

                client = make_client(key)
                models = list(client.models.list())
                self.signals.finished.emit("gemini", True, f"Clave válida ({len(models)} modelos disponibles).")
            except Exception as exc:  # noqa: BLE001
                self.signals.finished.emit("gemini", False, f"Error: {exc}")

        threading.Thread(target=work, name="jarvis-test-key", daemon=True).start()

    def _link_spotify(self) -> None:
        cid = self.spotify_id.text().strip()
        if not cid:
            QMessageBox.warning(self, "Spotify", "Primero pegá tu SPOTIFY_CLIENT_ID.")
            return
        save_env_values({"SPOTIFY_CLIENT_ID": cid})
        self.btn_spotify.setEnabled(False)
        self.lbl_spotify.setText("Autorizando en el navegador...")

        def work() -> None:
            try:
                import spotipy

                from jarvis.tools import spotify

                manager = spotify.auth_manager(open_browser=True)
                manager.get_access_token()
                me = spotipy.Spotify(auth_manager=manager).current_user()
                name = me.get("display_name") or "tu cuenta"
                self.signals.finished.emit("spotify", True, f"Vinculado ({name})")
            except Exception as exc:  # noqa: BLE001
                log.exception("Fallo la vinculacion de Spotify")
                self.signals.finished.emit("spotify", False, str(exc))

        threading.Thread(target=work, name="jarvis-spotify-auth", daemon=True).start()

    def _import_google_json(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar credenciales OAuth de Google",
            str(Path.home() / "Downloads"),
            "Archivos JSON (*.json)",
        )
        if not selected:
            return
        try:
            import json

            from jarvis.tools import google_ws

            raw = json.loads(Path(selected).read_text("utf-8"))
            if "installed" not in raw:
                QMessageBox.warning(
                    self,
                    "Google OAuth",
                    "Ese archivo no parece un cliente OAuth de tipo 'App de escritorio' (falta la clave 'installed').",
                )
                return
            shutil.copyfile(selected, google_ws.credentials_file())
            self.lbl_google.setText(self._google_status_text())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"No se pudo importar el archivo: {exc}")

    def _link_google(self) -> None:
        self.btn_google.setEnabled(False)
        self.lbl_google.setText("Autorizando en el navegador...")

        def work() -> None:
            try:
                from jarvis.tools import google_ws

                google_ws.get_credentials(interactive=True)
                self.signals.finished.emit("google", True, "Vinculado")
            except Exception as exc:  # noqa: BLE001
                log.exception("Fallo la vinculacion de Google")
                self.signals.finished.emit("google", False, str(exc))

        threading.Thread(target=work, name="jarvis-google-auth", daemon=True).start()

    def _on_task_finished(self, tag: str, ok: bool, message: str) -> None:
        if tag == "gemini":
            self.btn_test_key.setEnabled(True)
            self.lbl_key_status.setText(message)
            self.lbl_key_status.setStyleSheet("color: #16A34A;" if ok else "color: #DC2626;")
        elif tag == "spotify":
            self.btn_spotify.setEnabled(True)
            self.lbl_spotify.setText(message if ok else "Error al vincular")
            if not ok:
                QMessageBox.warning(self, "Spotify", message)
        elif tag == "google":
            self.btn_google.setEnabled(True)
            self.lbl_google.setText(message if ok else "Error al vincular")
            if not ok:
                QMessageBox.warning(self, "Google", message)


    def _save(self) -> None:
        try:
            save_env_values(
                {
                    "GEMINI_API_KEY": self.gemini_key.text().strip(),
                    "SPOTIFY_CLIENT_ID": self.spotify_id.text().strip(),
                    "TELEGRAM_BOT_TOKEN": self.tg_token.text().strip(),
                    "TELEGRAM_CHAT_IDS": self.tg_chats.text().strip(),
                    "JARVIS_HTTP_TOKEN": self.http_token.text().strip(),
                }
            )
            update_config_values(
                {
                    "usuario.nombre": self.user_name.text().strip(),
                    "usuario.tratamiento": self.user_treat.text().strip() or "señor",
                    "usuario.ciudad": self.user_city.text().strip(),
                    "resumen.noticias_pais": (self.country_combo.currentText().strip() or "AR").upper(),
                    "gemini.voz": self.voice_combo.currentText().strip() or "Charon",
                    "gemini.modo_voz": str(self.mode_combo.currentData() or "live"),
                    "activacion.wake_word": self.chk_wake.isChecked(),
                    "activacion.atajo": self.hotkey_edit.text().strip() or "ctrl+alt+j",
                    "canales.telegram": self.chk_telegram.isChecked(),
                    "canales.http": self.chk_http.isChecked(),
                }
            )
            profile_text = self.profile_edit.toPlainText().strip()
            profile_file = data_path("perfil.md")
            if profile_text:
                profile_file.write_text(profile_text + "\n", encoding="utf-8")
            elif profile_file.exists():
                profile_file.unlink()
        except Exception as exc:  # noqa: BLE001
            log.exception("No se pudo guardar la configuracion")
            QMessageBox.critical(self, "Error", f"No se pudo guardar la configuración:\n{exc}")
            return

        if self.engine is not None and self.engine.loop is not None:
            self.engine.loop.call_soon_threadsafe(lambda: self.engine.spawn(self.engine.reload_settings()))
        if callable(self.on_saved):
            self.on_saved()
        self.accept()
