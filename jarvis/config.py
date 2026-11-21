from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

IS_FROZEN = bool(getattr(sys, "frozen", False))


def _app_dir() -> Path:
    if IS_FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _user_dir(app_dir: Path) -> Path:
    override = os.environ.get("JARVIS_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if IS_FROZEN:
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home()) / "JARVIS"
    return app_dir


APP_DIR = _app_dir()
USER_DIR = _user_dir(APP_DIR)
BASE_DIR = USER_DIR
DATA_DIR = USER_DIR / "data"
CONFIG_FILE = USER_DIR / "config.toml"
ENV_FILE = USER_DIR / ".env"
EXAMPLE_CONFIG_FILE = APP_DIR / "config.example.toml"


def resource_path(*parts: str) -> Path:
    return APP_DIR.joinpath(*parts)


def data_path(*parts: str) -> Path:
    path = DATA_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_user_files() -> None:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists() and EXAMPLE_CONFIG_FILE.exists():
        shutil.copyfile(EXAMPLE_CONFIG_FILE, CONFIG_FILE)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def env_list(name: str) -> list[str]:
    return [item.strip() for item in env(name).split(",") if item.strip()]


def read_env_file(path: Path | None = None) -> dict[str, str]:
    target = path or ENV_FILE
    values: dict[str, str] = {}
    if not target.exists():
        return values
    for raw in target.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _format_env_value(value: str) -> str:
    clean = value.replace("\r", "").replace("\n", "").strip()
    if any(ch in clean for ch in (" ", "#", "'", '"')):
        escaped = clean.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return clean


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_env_values(updates: dict[str, str], path: Path | None = None) -> None:
    target = path or ENV_FILE
    existing_lines = target.read_text("utf-8").splitlines() if target.exists() else [
        "# Claves privadas de JARVIS. Nunca compartas este archivo.",
        "",
    ]
    pending = {k.strip(): str(v).strip() for k, v in updates.items() if k.strip()}
    out_lines: list[str] = []
    seen: set[str] = set()

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                out_lines.append(f"{key}={_format_env_value(pending[key])}")
                seen.add(key)
                continue
        out_lines.append(line)

    for key, val in pending.items():
        if key not in seen:
            out_lines.append(f"{key}={_format_env_value(val)}")

    _atomic_write(target, "\n".join(out_lines).rstrip("\n") + "\n")
    for key, val in pending.items():
        if val:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Tipo no soportado para TOML: {type(value).__name__}")


_SECTION_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
_ARRAY_TABLE_RE = re.compile(r"^\s*\[\[(.+)\]\]\s*(?:#.*)?$")


def update_config_values(updates: dict[str, Any], path: Path | None = None) -> Config:
    target = path or CONFIG_FILE
    if not target.exists() and EXAMPLE_CONFIG_FILE.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE_CONFIG_FILE, target)

    by_section: dict[str, dict[str, Any]] = {}
    for dotted, value in updates.items():
        if "." not in dotted:
            by_section.setdefault("", {})[dotted] = value
        else:
            section, key = dotted.rsplit(".", 1)
            by_section.setdefault(section, {})[key] = value

    lines = target.read_text("utf-8").splitlines() if target.exists() else []
    out: list[str] = []
    current_section = ""
    written: dict[str, set[str]] = {sec: set() for sec in by_section}

    def flush_missing(sec: str) -> None:
        if sec not in by_section:
            return
        for k, v in by_section[sec].items():
            if k not in written[sec]:
                out.append(f"{k} = {_toml_value(v)}")
                written[sec].add(k)

    for line in lines:
        if _ARRAY_TABLE_RE.match(line):
            flush_missing(current_section)
            current_section = "__array__"
            out.append(line)
            continue
        sec_match = _SECTION_RE.match(line)
        if sec_match:
            flush_missing(current_section)
            current_section = sec_match.group(1).strip()
            out.append(line)
            continue
        if current_section in by_section:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key_part = stripped.split("=", 1)[0].strip()
                if key_part in by_section[current_section]:
                    out.append(f"{key_part} = {_toml_value(by_section[current_section][key_part])}")
                    written[current_section].add(key_part)
                    continue
        out.append(line)

    flush_missing(current_section)

    for sec, pairs in by_section.items():
        remaining = {k: v for k, v in pairs.items() if k not in written[sec]}
        if not remaining:
            continue
        if out and out[-1].strip():
            out.append("")
        if sec:
            out.append(f"[{sec}]")
        for k, v in remaining.items():
            out.append(f"{k} = {_toml_value(v)}")

    rendered = "\n".join(out).rstrip("\n") + "\n"
    parsed = tomllib.loads(rendered)
    _atomic_write(target, rendered)
    cfg = Config(parsed)
    set_config(cfg)
    return cfg


class Config:

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def section(self, name: str) -> dict[str, Any]:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}


_config: Config | None = None


def load_config(path: Path | None = None, reload_env: bool = False) -> Config:
    global _config
    ensure_user_files()
    load_dotenv(ENV_FILE, override=reload_env)
    cfg_path = path or CONFIG_FILE
    data: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "rb") as fh:
            data = tomllib.load(fh)
    _config = Config(data)
    return _config


def get_config() -> Config:
    return _config if _config is not None else load_config()


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
