from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from jarvis.audio import voice_training
from jarvis.audio.io import chime
from jarvis.ui.theme import style_dialog

log = logging.getLogger(__name__)

POSITIVES = 8
CLIP_SECONDS = 2.6
NEGATIVE_SECONDS = 20.0
SILENCE_SECONDS = 5.0


class _TrainSignals(QObject):
    done = Signal(object)


class VoiceTrainingDialog(QDialog):
    def __init__(self, engine) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.engine = engine
        self.setWindowTitle("JARVIS — Entrenar mi voz")
        self.setMinimumWidth(460)
        style_dialog(self)
        self.title = QLabel("Entrenar «hey jarvis» con su voz")
        title_font = QFont("Segoe UI", 13)
        title_font.setWeight(QFont.Weight.Bold)
        self.title.setFont(title_font)
        self.info = QLabel(
            "Le voy a pedir que diga «hey jarvis» 8 veces, como lo dice normalmente, cada vez que suene el tono. "
            "Después, que hable 20 segundos de cualquier cosa sin decir «jarvis», y por último 5 segundos de silencio. "
            "Use el mismo micrófono de siempre. Tarda unos dos minutos."
        )
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.prompt = QLabel("")
        prompt_font = QFont("Segoe UI", 18)
        prompt_font.setWeight(QFont.Weight.Bold)
        self.prompt.setFont(prompt_font)
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setWordWrap(True)
        self.prompt.setMinimumHeight(90)
        self.start_btn = QPushButton("Empezar")
        self.start_btn.setObjectName("primary")
        self.close_btn = QPushButton("Cancelar")
        self.start_btn.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.start_btn)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.title)
        layout.addWidget(self.info)
        layout.addWidget(self.prompt)
        layout.addLayout(buttons)

        self.steps: list[tuple[str, float, str]] = []
        self.index = 0
        self.signals = _TrainSignals()
        self.signals.done.connect(self._trained)


    def start(self) -> None:
        if self.engine.mode != "idle":
            self.prompt.setText("Espere a que JARVIS termine la conversación y vuelva a intentar.")
            return
        voice_training.clear_recordings()
        self.steps = [(f"Diga «hey jarvis»  ({i + 1}/{POSITIVES})", CLIP_SECONDS, f"positivos/{i + 1}.wav") for i in range(POSITIVES)]
        self.steps.append(("Ahora hable normal 20 segundos, de lo que quiera, sin decir «jarvis»", NEGATIVE_SECONDS, "negativos/voz.wav"))
        self.steps.append(("Silencio 5 segundos...", SILENCE_SECONDS, "negativos/silencio.wav"))
        self.index = 0
        self.start_btn.setEnabled(False)
        self.close_btn.setText("Cancelar")
        self._next()

    def _next(self) -> None:
        if self.index >= len(self.steps):
            self._train()
            return
        text, _, _ = self.steps[self.index]
        self.prompt.setText(f"Prepárese...\n{text}")
        QTimer.singleShot(900, self._record)

    def _record(self) -> None:
        if not self.isVisible():
            return
        text, seconds, _ = self.steps[self.index]
        if self.engine.audio is not None:
            self.engine.audio.play(chime("activar", 0.3))
        QTimer.singleShot(250, lambda: self._begin(text, seconds))

    def _begin(self, text: str, seconds: float) -> None:
        if not self.engine.begin_capture():
            self.prompt.setText("No pude usar el micrófono ahora. Cierre esta ventana y vuelva a intentar.")
            self.start_btn.setEnabled(True)
            return
        self.prompt.setText(f"[GRABANDO]\n{text}")
        QTimer.singleShot(int(seconds * 1000), self._finish_clip)

    def _finish_clip(self) -> None:
        pcm = self.engine.end_capture()
        if not self.isVisible():
            return
        _, _, relative = self.steps[self.index]
        folder, name = relative.split("/")
        target = (voice_training.positives_dir() if folder == "positivos" else voice_training.negatives_dir()) / name
        voice_training.save_wav(target, pcm)
        self.index += 1
        self._next()


    def _train(self) -> None:
        self.prompt.setText("Entrenando con su voz...")

        def work() -> None:
            try:
                from jarvis.audio.wakeword import model_path

                result = voice_training.train(model_path())
            except Exception as exc:  # noqa: BLE001
                log.exception("Fallo el entrenamiento de voz")
                result = exc
            self.signals.done.emit(result)

        threading.Thread(target=work, name="jarvis-voz", daemon=True).start()

    def _trained(self, result) -> None:
        self.close_btn.setText("Cerrar")
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Repetir")
        if isinstance(result, Exception):
            self.prompt.setText(f"[ERROR] {result}")
            return
        loop = self.engine.loop
        if loop is not None:
            loop.call_soon_threadsafe(lambda: self.engine.spawn(self.engine.reload_wakeword()))
        self.prompt.setText(
            "[OK] Listo. Ya puede probar «hey jarvis».\n"
            f"Reconocimiento con su voz: {round(result['verificador_en_su_voz'] * 100)} %"
        )

    def reject(self) -> None:
        if self.engine.mode == "captura":
            self.engine.end_capture()
        super().reject()
