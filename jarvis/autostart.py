from __future__ import annotations

import sys
from pathlib import Path

from jarvis.config import APP_DIR, IS_FROZEN

SHORTCUT_NAME = "JARVIS.lnk"


def startup_dir() -> Path:
    from win32com.shell import shell, shellcon

    return Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Startup))


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def pythonw() -> Path:
    venv = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv.exists():
        return venv
    return Path(sys.executable).with_name("pythonw.exe")


def launch_target() -> tuple[Path, str, Path]:
    if IS_FROZEN:
        exe = Path(sys.executable).resolve()
        return exe, "", exe.parent
    return pythonw(), "-m jarvis", APP_DIR


def is_enabled() -> bool:
    return shortcut_path().exists()


def enable() -> Path:
    import win32com.client

    path = shortcut_path()
    target, arguments, workdir = launch_target()
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(str(path))
    link.TargetPath = str(target)
    link.Arguments = arguments
    link.WorkingDirectory = str(workdir)
    link.Description = "JARVIS, asistente personal de voz"
    link.IconLocation = str(target)
    link.Save()
    return path


def disable() -> None:
    path = shortcut_path()
    if path.exists():
        path.unlink()


def set_enabled(on: bool) -> bool:
    if on:
        enable()
    else:
        disable()
    return is_enabled()
