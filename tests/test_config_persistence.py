from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from jarvis import autostart, config
from jarvis.brain.prompt import build_system_prompt
from jarvis.tools import info
from jarvis.tools.registry import ToolError


def test_save_and_read_env_preserves_comments_and_updates_environ(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# Comentario\nGEMINI_API_KEY=old\nOTHER_KEY=keep\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)

    config.save_env_values(
        {"GEMINI_API_KEY": "new-secret", "SPOTIFY_CLIENT_ID": "abc 123"},
        path=env_file,
    )

    content = env_file.read_text("utf-8")
    assert "# Comentario" in content
    assert "OTHER_KEY=keep" in content
    parsed = config.read_env_file(env_file)
    assert parsed["GEMINI_API_KEY"] == "new-secret"
    assert parsed["SPOTIFY_CLIENT_ID"] == "abc 123"
    assert parsed["OTHER_KEY"] == "keep"
    assert os.environ["GEMINI_API_KEY"] == "new-secret"
    assert os.environ["SPOTIFY_CLIENT_ID"] == "abc 123"


def test_update_config_values_preserves_comments_and_routines(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "# Header\n"
        "[usuario]\n"
        "# comentario usuario\n"
        'nombre = ""\n'
        'tratamiento = "señor"\n'
        'ciudad = ""\n\n'
        "[activacion]\n"
        "wake_word = true\n\n"
        "[[rutinas.modo_trabajo]]\n"
        'herramienta = "fijar_volumen"\n'
        "args = { nivel = 30 }\n",
        encoding="utf-8",
    )

    cfg = config.update_config_values(
        {
            "usuario.nombre": "Alex",
            "usuario.ciudad": "Córdoba",
            "activacion.wake_word": False,
            "activacion.atajo": "ctrl+alt+k",
            "nueva_seccion.clave": 42,
        },
        path=cfg_file,
    )

    raw = cfg_file.read_text("utf-8")
    assert "# comentario usuario" in raw
    parsed = tomllib.loads(raw)
    assert parsed["usuario"]["nombre"] == "Alex"
    assert parsed["usuario"]["ciudad"] == "Córdoba"
    assert parsed["usuario"]["tratamiento"] == "señor"
    assert parsed["activacion"]["wake_word"] is False
    assert parsed["activacion"]["atajo"] == "ctrl+alt+k"
    assert parsed["nueva_seccion"]["clave"] == 42
    assert parsed["rutinas"]["modo_trabajo"][0]["herramienta"] == "fijar_volumen"
    assert cfg.get("usuario.ciudad") == "Córdoba"


def test_ensure_user_files_copies_example_config(tmp_path, monkeypatch):
    example = tmp_path / "app" / "config.example.toml"
    example.parent.mkdir(parents=True)
    example.write_text('[usuario]\ntratamiento = "señor"\nciudad = ""\n', encoding="utf-8")

    user_dir = tmp_path / "user"
    monkeypatch.setattr(config, "USER_DIR", user_dir)
    monkeypatch.setattr(config, "DATA_DIR", user_dir / "data")
    monkeypatch.setattr(config, "CONFIG_FILE", user_dir / "config.toml")
    monkeypatch.setattr(config, "EXAMPLE_CONFIG_FILE", example)

    config.ensure_user_files()

    assert (user_dir / "data").is_dir()
    assert (user_dir / "config.toml").read_text("utf-8") == example.read_text("utf-8")


def test_autostart_launch_target_frozen_vs_dev(monkeypatch, tmp_path):
    fake_exe = tmp_path / "Jarvis.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(autostart, "IS_FROZEN", True)
    monkeypatch.setattr(autostart.sys, "executable", str(fake_exe))

    target, args, workdir = autostart.launch_target()
    assert target == fake_exe.resolve()
    assert args == ""
    assert workdir == fake_exe.resolve().parent

    monkeypatch.setattr(autostart, "IS_FROZEN", False)
    monkeypatch.setattr(autostart, "APP_DIR", tmp_path)
    target_dev, args_dev, workdir_dev = autostart.launch_target()
    assert args_dev == "-m jarvis"
    assert workdir_dev == tmp_path
    assert target_dev.name.lower() == "pythonw.exe"


def test_empty_city_handled_gracefully():
    config.set_config(config.Config({"usuario": {"tratamiento": "señor", "ciudad": ""}}))
    prompt = build_system_prompt("texto")
    assert "Ciudad del usuario:" not in prompt
    with pytest.raises(ToolError, match="ciudad"):
        info.weather("")


def test_example_config_is_valid_toml_and_has_no_personal_data():
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    text = example.read_text("utf-8")
    parsed = tomllib.loads(text)
    assert parsed["usuario"]["nombre"] == ""
    assert parsed["usuario"]["ciudad"] == ""
    assert "AIza" not in text
