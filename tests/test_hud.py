from __future__ import annotations

from jarvis.ui import hud


def test_ring_is_closed_and_inside_the_box():
    path = hud.ring_path([0.0, 1.0, 2.0, 3.0], e=1.0, idle=1.0, mix=0.85, rotation=0.4, radius=1.02)
    box = path.boundingRect()
    assert box.left() > 0 and box.top() > 0 and box.right() < 100 and box.bottom() < 100
    assert path.elementCount() == 1 + 3 * hud.POINTS
    first, last = path.elementAt(0), path.elementAt(path.elementCount() - 1)
    assert abs(first.x - last.x) < 1e-6 and abs(first.y - last.y) < 1e-6


def test_sim_follows_audio_only_in_matching_state():
    sim = hud.RingSim()
    sim.set_state("escuchando")
    for _ in range(30):
        sim.set_level("mic", 0.6)
        sim.set_level("out", 1.0)
        sim.step(0.033)
    assert 0.4 < sim.level <= 0.6
    sim.set_state("pensando")
    for _ in range(60):
        sim.step(0.033)
    assert sim.level < 0.05 and sim.drive > 0.25


def test_speaking_triggers_shockwave_and_unknown_state_falls_back():
    sim = hud.RingSim()
    sim.set_state("hablando")
    assert sim.shock_started is not None
    sim.set_state("algo_raro")
    assert sim.state == "reposo"
