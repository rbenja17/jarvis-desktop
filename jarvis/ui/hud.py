from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from jarvis import store
from jarvis.config import get_config

UI_STATE = "interfaz.json"
FRAME_MS = 33

PALETTE = {
    "brand": "#38BDF8",
    "user": "#5EEAD4",
    "muted": "#9CA3AF",
    "error": "#F87171",
}

STATES: dict[str, dict] = {
    "conectando": {"color": "brand", "audio": None, "drive": 0.0, "idle": 0.0, "mix": 0.3, "speed": 0.0, "rot": 0.0, "radius": 1.0, "opacity": 0.8},
    "reposo": {"color": "brand", "audio": None, "drive": 0.0, "idle": 0.7, "mix": 0.1, "speed": 0.35, "rot": 0.08, "radius": 0.94, "opacity": 0.7},
    "escuchando": {"color": "user", "audio": "mic", "drive": 0.0, "idle": 1.0, "mix": 0.15, "speed": 0.7, "rot": 0.15, "radius": 0.97, "opacity": 1.0},
    "hablando": {"color": "brand", "audio": "out", "drive": 0.0, "idle": 1.0, "mix": 0.85, "speed": 1.6, "rot": 0.35, "radius": 1.02, "opacity": 1.0},
    "pensando": {"color": "brand", "audio": None, "drive": 0.3, "idle": 1.0, "mix": 0.5, "speed": 1.2, "rot": 1.1, "radius": 1.0, "opacity": 0.85},
    "silenciado": {"color": "muted", "audio": None, "drive": 0.0, "idle": 0.0, "mix": 0.0, "speed": 0.0, "rot": 0.0, "radius": 1.0, "opacity": 0.35},
    "error": {"color": "error", "audio": None, "drive": 0.15, "idle": 1.0, "mix": 0.6, "speed": 1.0, "rot": 0.2, "radius": 1.0, "opacity": 0.9},
}
TIMING = {"escuchando": (80, 300), "hablando": (40, 160)}
DEFAULT_TIMING = (60, 250)
STATE_TAU = 0.18
COLOR_TAU = 0.2

HARMONICS = [(2, 0.5, 0.16, 0.8, 0.0), (3, 0.3, 0.24, -1.1, 1.7), (5, 0.14, 0.3, 1.7, 3.1), (7, 0.06, 0.3, -2.3, 4.4)]
POINTS = 48
BASE_RADIUS = 34.0
EXPANSION = 1.5
IDLE_AMPLITUDE = 0.6
LEVEL_AMPLITUDE = 8.5
STROKE = 1.5
GLOW1 = (4.0, 0.16)
GLOW2 = (7.5, 0.07)
GLINT_STROKE = 2.0

_COS = [math.cos(2 * math.pi * i / POINTS) for i in range(POINTS)]
_SIN = [math.sin(2 * math.pi * i / POINTS) for i in range(POINTS)]

COLORS = {state: QColor(PALETTE[cfg["color"]]) for state, cfg in STATES.items()}


def _approach(current: float, target: float, dt: float, tau: float = STATE_TAU) -> float:
    return current + (target - current) * (1 - math.exp(-dt / tau))


def _smooth(prev: float, target: float, dt: float, attack_ms: float, release_ms: float) -> float:
    tau = (attack_ms if target > prev else release_ms) / 1000
    return prev + (target - prev) * (1 - math.exp(-dt / tau))


def _mix(a: tuple[float, float, float], b: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    t = max(0.0, min(1.0, t))
    return tuple(x + (y - x) * t for x, y in zip(a, b))  # type: ignore[return-value]


def _rgb(hex_color: str) -> tuple[float, float, float]:
    c = QColor(hex_color)
    return float(c.red()), float(c.green()), float(c.blue())


def ring_path(phases: list[float], e: float, idle: float, mix: float, rotation: float, radius: float) -> QPainterPath:
    amplitude = IDLE_AMPLITUDE * idle + LEVEL_AMPLITUDE * e
    base = BASE_RADIUS * radius + EXPANSION * e
    pts = []
    for i in range(POINTS):
        theta = 2 * math.pi * i / POINTS
        wave = 0.0
        for k, (n, w_calm, w_energetic, _omega, phi) in enumerate(HARMONICS):
            weight = w_calm + (w_energetic - w_calm) * mix
            wave += weight * math.sin(n * (theta + rotation) + phases[k] + phi)
        r = base + amplitude * wave
        pts.append((50 + r * _COS[i], 50 + r * _SIN[i]))
    path = QPainterPath(QPointF(*pts[0]))
    for i in range(POINTS):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % POINTS], pts[(i + 2) % POINTS]
        c1 = QPointF(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = QPointF(p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        path.cubicTo(c1, c2, QPointF(*p2))
    path.closeSubpath()
    return path


class RingSim:

    def __init__(self) -> None:
        self.state = "reposo"
        self.phases = [0.0] * len(HARMONICS)
        self.rotation = 0.0
        self.level = 0.0
        self.t = 0.0
        cfg = STATES["reposo"]
        self.idle, self.mix, self.speed, self.rot = cfg["idle"], cfg["mix"], cfg["speed"], cfg["rot"]
        self.radius, self.opacity, self.drive = cfg["radius"], cfg["opacity"], cfg["drive"]
        self.rgb = _rgb(PALETTE["brand"])
        self.glint = 0.0
        self.shock_started: float | None = None
        self.levels = {"mic": 0.0, "out": 0.0}
        self.level_stamp = {"mic": 0.0, "out": 0.0}

    def set_state(self, state: str) -> None:
        if state not in STATES:
            state = "reposo"
        if state == "hablando" and self.state != "hablando":
            self.shock_started = time.monotonic()
        self.state = state

    def set_level(self, source: str, value: float) -> None:
        self.levels[source] = max(0.0, min(1.0, value))
        self.level_stamp[source] = time.monotonic()

    def step(self, dt: float) -> None:
        self.t += dt
        target = STATES[self.state]
        attack, release = TIMING.get(self.state, DEFAULT_TIMING)
        source = target["audio"]
        level_in = 0.0
        if source and time.monotonic() - self.level_stamp[source] < 0.3:
            level_in = self.levels[source]
        self.level = _smooth(self.level, level_in, dt, attack, release)
        self.idle = _approach(self.idle, target["idle"], dt)
        self.mix = _approach(self.mix, target["mix"], dt)
        self.speed = _approach(self.speed, target["speed"], dt)
        self.rot = _approach(self.rot, target["rot"], dt)
        self.radius = _approach(self.radius, target["radius"], dt)
        self.opacity = _approach(self.opacity, target["opacity"], dt)
        self.drive = _approach(self.drive, target["drive"], dt)
        self.phases = [p + h[3] * self.speed * dt for p, h in zip(self.phases, HARMONICS)]
        self.rotation += self.rot * dt
        self.rgb = _mix(self.rgb, _rgb(PALETTE[target["color"]]), 1 - math.exp(-dt / COLOR_TAU))
        self.glint = (self.glint + dt / 1.6 * 360) % 360

    @property
    def energy(self) -> float:
        return self.level + self.drive

    def frame_opacity(self) -> float:
        opacity = self.opacity
        if self.state == "conectando":
            opacity *= 1 - 0.35 * (0.5 - 0.5 * math.cos(2 * math.pi * self.t / 2.4))
        return opacity


class Subtitle(QWidget):

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(440)
        font = QFont("Segoe UI", 11)
        font.setWeight(QFont.Weight.Bold)
        self.label.setFont(font)
        self.label.setStyleSheet(
            "QLabel { background: rgba(8, 14, 24, 225); color: #F1F7FF; border: 1px solid rgba(56, 189, 248, 110);"
            " border-radius: 12px; padding: 9px 13px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)

    def show_text(self, who: str, text: str, anchor: QWidget) -> None:
        if not who or not text:
            self._hide_timer.start(1500)
            return
        color = PALETTE["brand"] if who == "jarvis" else PALETTE["user"]
        prefix = "JARVIS" if who == "jarvis" else "USTED"
        shown = text if len(text) < 400 else "..." + text[-397:]
        self.label.setText(f"<span style='color:{color}; font-weight:900'>{prefix}</span>&nbsp; {shown}")
        self.adjustSize()
        geo = anchor.frameGeometry()
        screen = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        x = min(max(screen.left() + 8, geo.right() - self.width()), screen.right() - self.width() - 8)
        y = geo.top() - self.height() - 6
        if y < screen.top():
            y = geo.bottom() + 6
        self.move(x, y)
        self.show()
        self._hide_timer.start(8000)


class Hud(QWidget):
    def __init__(self, bridge, on_click, on_double_click, on_menu) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("JARVIS")
        self.size_px = int(get_config().get("interfaz.tamano_orbe", 150))
        self.setFixedSize(self.size_px, self.size_px)
        self.on_click = on_click
        self.on_double_click = on_double_click
        self.on_menu = on_menu

        self.sim = RingSim()
        self._last = time.monotonic()
        self._press: QPoint | None = None
        self._dragged = False
        self.subtitle = Subtitle()
        self._click_timer = QTimer(self, singleShot=True, timeout=self.on_click)

        bridge.state.connect(self.sim.set_state)
        bridge.level.connect(self.sim.set_level)
        bridge.subtitle.connect(lambda who, text: self.subtitle.show_text(who, text, self))

        self._timer = QTimer(self, timeout=self._tick)
        self._timer.start(FRAME_MS)
        self._wanted = False
        self._guard = QTimer(self, timeout=self._ensure_visible)
        self._guard.start(2000)
        self._restore_position()

    def _tick(self) -> None:
        now = time.monotonic()
        self.sim.step(min(0.1, now - self._last))
        self._last = now
        self.update()


    def paintEvent(self, event) -> None:
        sim = self.sim
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.width() / 100, self.height() / 100)
        r, g, b = (int(v) for v in sim.rgb)
        opacity = sim.frame_opacity()

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 1))
        p.drawEllipse(QPointF(50, 50), 46, 46)
        p.setBrush(Qt.NoBrush)

        path = ring_path(sim.phases, sim.energy, sim.idle, sim.mix, sim.rotation, sim.radius)
        for width, alpha in (GLOW2, GLOW1, (STROKE, 1.0)):
            pen = QPen(QColor(r, g, b, int(255 * alpha * opacity)), width)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)

        if sim.shock_started is not None:
            k = (time.monotonic() - sim.shock_started) / 0.65
            if k >= 1:
                sim.shock_started = None
            else:
                eased = 1 - (1 - k) ** 3
                alpha = 0.6 * (k / 0.15) if k < 0.15 else 0.6 * (1 - (k - 0.15) / 0.85)
                radius = BASE_RADIUS * (1 + 0.42 * eased)
                p.setPen(QPen(QColor(r, g, b, int(255 * max(0.0, alpha))), 0.9))
                p.drawEllipse(QPointF(50, 50), radius, radius)

        if sim.state == "conectando":
            rect = QRectF(16, 16, 68, 68)
            for span, alpha in ((80, 0.18), (45, 0.35), (16, 0.95)):
                pen = QPen(QColor(r, g, b, int(255 * alpha)), GLINT_STROKE)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawArc(rect, int(-sim.glint * 16), int(span * 16))

        if sim.state == "silenciado":
            p.setPen(QPen(QColor(r, g, b, int(255 * 0.3)), STROKE))
            p.drawEllipse(QPointF(50, 50), BASE_RADIUS, BASE_RADIUS)
        p.end()


    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._dragged = False
        elif event.button() == Qt.RightButton:
            self.on_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        delta = event.globalPosition().toPoint() - self._press
        if delta.manhattanLength() > 4:
            self._dragged = True
        if self._dragged:
            self.move(self._origin + delta)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragged:
            self._save_position()
        else:
            self._click_timer.start(QGuiApplication.styleHints().mouseDoubleClickInterval())

    def mouseDoubleClickEvent(self, event) -> None:
        self._click_timer.stop()
        self.on_double_click()


    def _restore_position(self) -> None:
        saved = store.load(UI_STATE, {}).get("orbe")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if saved and screen.contains(QPoint(saved[0] + 10, saved[1] + 10)):
            self.move(saved[0], saved[1])
        else:
            self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _save_position(self) -> None:
        pos = self.pos()
        store.update(UI_STATE, {}, lambda s: {**s, "orbe": [pos.x(), pos.y()]})

    def set_shown(self, shown: bool, remember: bool = True) -> None:
        self._wanted = shown
        self.setVisible(shown)
        if remember:
            store.update(UI_STATE, {}, lambda s: {**s, "mostrar_orbe": shown})

    def _ensure_visible(self) -> None:
        if not self._wanted:
            return
        if not self.isVisible() or self.isMinimized():
            self.showNormal()
        center = self.frameGeometry().center()
        if not any(screen.availableGeometry().contains(center) for screen in QGuiApplication.screens()):
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
            self._save_position()
        try:
            import ctypes

            ctypes.windll.user32.SetWindowPos(int(self.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass
