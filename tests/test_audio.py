from __future__ import annotations

import time

import numpy as np
import pytest

from jarvis.audio import echo
from jarvis.audio.echo import EchoGate
from jarvis.audio.io import OUT_RATE, AudioIO


def _frame(level: int) -> bytes:
    return (np.ones(1280, dtype=np.int16) * level).tobytes()


@pytest.fixture(autouse=True)
def fresh_echo():
    echo.reset_learning()
    yield
    echo.reset_learning()


def test_echo_gate_blocks_own_voice_but_lets_user_interrupt():
    gate = EchoGate()
    own = _frame(90)
    for _ in range(30):
        assert gate.filter(own, playing=True) == bytes(len(own))
    assert gate.threshold >= 180
    user = _frame(500)
    assert gate.filter(user, playing=True) == user
    assert gate.filter(own, playing=True) == own
    assert gate.filter(own, playing=False) == own


def test_echo_gate_learns_echo_louder_than_the_minimum():
    gate = EchoGate()
    loud_echo = _frame(300)
    passed = sum(gate.filter(loud_echo, playing=True) == loud_echo for _ in range(40))
    assert passed <= 1 + echo.HANGOVER_FRAMES
    assert gate.threshold >= 300 * 2.5 - 1
    assert gate.filter(loud_echo, playing=True) == bytes(2560)
    assert gate.filter(_frame(900), playing=True) == _frame(900)


def test_echo_gate_starts_calibrated_from_previous_session():
    first = EchoGate()
    for _ in range(20):
        first.filter(_frame(300), playing=True)
    second = EchoGate()
    assert second.threshold >= 300 * 2.5 - 1
    assert second.filter(_frame(300), playing=True) == bytes(2560)


def test_mic_watchdog_reasons():
    from jarvis.engine import mic_problem

    assert mic_problem(frame_age=0.1, wall_gap=2.0) is None
    assert "suspension" in mic_problem(frame_age=0.1, wall_gap=8 * 3600)
    assert "dejo de enviar" in mic_problem(frame_age=10.0, wall_gap=2.0)


def _pull(audio: AudioIO, samples: int = 480) -> bytes:
    out = bytearray(samples * 2)
    audio._out_callback(memoryview(out), samples, None, None)
    return bytes(out)


def test_prebuffer_waits_then_plays_and_counts_gaps(monkeypatch):
    audio = AudioIO(lambda frame: None)
    monkeypatch.setattr(audio, "_ensure_output", lambda: None)
    voice = (np.ones(int(OUT_RATE * 0.05), dtype=np.int16) * 1000).tobytes()

    audio.play(voice, prebuffer=0.2)
    assert _pull(audio) == bytes(960)
    for _ in range(4):
        audio.play(voice, prebuffer=0.2)
    assert _pull(audio) != bytes(960)

    while audio.pending_seconds() > 0:
        _pull(audio)
    _pull(audio)
    audio.play(voice, prebuffer=0.2)
    assert audio.gaps == 1

    audio.clear()
    audio.play(voice, prebuffer=0.05)
    assert audio.gaps == 1
    time.sleep(0.08)
    assert _pull(audio) != bytes(960)


def test_wakeword_requires_consecutive_frames_and_vad_for_verifier():
    from collections import deque
    from types import SimpleNamespace

    from jarvis.audio.wakeword import WakeWord

    class FakeModel:
        def __init__(self, base_scores, vad_scores):
            self._scores = iter(base_scores)
            self.vad = SimpleNamespace(prediction_buffer=deque(vad_scores, maxlen=20))
            self.model_inputs = {"hey_jarvis_v0.1": 16}
            self.preprocessor = SimpleNamespace(get_features=lambda _n: np.zeros((1, 16, 96)))

        def predict(self, _audio):
            return {"hey_jarvis_v0.1": next(self._scores, 0.0)}

        def reset(self):
            pass

    class FakeVerifier:
        def __init__(self, probs):
            self._probs = iter(probs)

        def predict_proba(self, _feat):
            p = next(self._probs, 0.0)
            return np.array([[1.0 - p, p]])

    ww = WakeWord.__new__(WakeWord)
    ww.label = "hey_jarvis_v0.1"
    ww.threshold = 0.38
    ww.vad_threshold = 0.5
    ww.verifier_threshold = 0.60
    ww.confirm_frames = 2
    ww._levels = deque(maxlen=12)
    ww.last_verifier = 0.0
    ww._hits = 0
    ww._reset_pending = False
    ww._cooldown_until = 0.0
    ww.last_score = 0.0
    ww._near_miss_at = 0.0
    ww._peak = 0.0
    ww._peak_at = 0.0
    ww._peak_detail = (0.0, 0.0)
    ww._frames = 0
    ww._level_sum = 0.0
    ww._report_at = time.monotonic() + 300.0

    ww.model = FakeModel([0.0, 0.0], vad_scores=[0.1, 0.2])
    ww.verifier = FakeVerifier([0.95, 0.95])
    assert ww.process(_frame(500)) is False
    assert ww.process(_frame(500)) is False

    ww.model = FakeModel([0.0, 0.0, 0.0, 0.0], vad_scores=[0.8, 0.9])
    ww.verifier = FakeVerifier([0.75, 0.20, 0.72, 0.68])
    assert ww.process(_frame(500)) is False
    assert ww.process(_frame(500)) is False
    assert ww.process(_frame(500)) is False
    assert ww.process(_frame(500)) is True
