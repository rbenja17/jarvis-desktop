# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

datas: list[tuple[str, str]] = [
    (str(ROOT / "config.example.toml"), "."),
    (str(ROOT / ".env.example"), "."),
]
ico_path = ROOT / "packaging" / "jarvis.ico"
if ico_path.exists():
    datas.append((str(ico_path), "packaging"))

import openwakeword  # noqa: E402

oww_models = Path(openwakeword.__file__).parent / "resources" / "models"
for model_file in (
    "hey_jarvis_v0.1.onnx",
    "melspectrogram.onnx",
    "embedding_model.onnx",
    "silero_vad.onnx",
):
    src = oww_models / model_file
    if src.exists():
        datas.append((str(src), "openwakeword/resources/models"))

binaries = []
for pkg in ("_sounddevice_data", "onnxruntime"):
    try:
        datas += collect_data_files(pkg)
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

for pkg in ("certifi", "edge_tts", "googleapiclient", "ddgs", "primp"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

hiddenimports = [
    "jarvis.tools.confirm",
    "jarvis.tools.session",
    "jarvis.tools.apps",
    "jarvis.tools.audio_ctl",
    "jarvis.tools.media",
    "jarvis.tools.spotify",
    "jarvis.tools.bluetooth",
    "jarvis.tools.system",
    "jarvis.tools.web",
    "jarvis.tools.files",
    "jarvis.tools.screen",
    "jarvis.tools.productivity",
    "jarvis.tools.instructions",
    "jarvis.tools.history",
    "jarvis.tools.info",
    "jarvis.tools.phone",
    "jarvis.tools.google_ws",
    "jarvis.tools.briefing",
    "jarvis.tools.routines",
    "jarvis.tools.usage_tool",
    "jarvis.ui.settings_dialog",
    "jarvis.ui.voice_dialog",
    "winrt.runtime",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.devices.radios",
    "winrt.windows.devices.bluetooth",
    "winrt.windows.devices.enumeration",
    "winrt.windows.media.control",
    "winrt.windows.media.audio",
    "win32com.shell",
    "win32com.client",
    "win32gui",
    "win32con",
    "win32api",
    "win32process",
    "pythoncom",
    "pywintypes",
    "pycaw",
    "comtypes",
    "comtypes.stream",
    "openwakeword.custom_verifier_model",
    "sklearn.linear_model",
    "sklearn.preprocessing",
    "sklearn.pipeline",
    "sklearn.utils._typedefs",
    "sklearn.neighbors._partition_nodes",
    "spotipy",
    "google.genai",
    "google_auth_oauthlib.flow",
    "googleapiclient.discovery",
    "telegram",
    "telegram.ext",
    "aiohttp",
    "edge_tts",
    "ddgs",
    "screen_brightness_control",
]

for pkg in ("winrt", "comtypes.gen"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

excludes = [
    "tkinter",
    "_tkinter",
    "matplotlib",
    "PyQt5",
    "PyQt6",
    "IPython",
    "jupyter",
    "pytest",
    "unittest.mock",
    "sklearn.tests",
    "sklearn.utils.tests",
    "scipy.tests",
    "numpy.tests",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ico_path) if ico_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Jarvis",
)
