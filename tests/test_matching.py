from __future__ import annotations

from jarvis.textutil import best_match, normalize
from jarvis.tools import apps, spotify

APPS = [
    {"name": "Google Chrome", "id": "Chrome"},
    {"name": "Spotify", "id": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"},
    {"name": "Microsoft Word", "id": "Microsoft.Office.WINWORD.EXE.15"},
    {"name": "Visual Studio Code", "id": "Microsoft.VisualStudioCode"},
    {"name": "Calculadora", "id": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {"name": "Configuración", "id": "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel"},
]


def test_normalize_removes_accents_and_case():
    assert normalize("  Configuración  ") == "configuracion"


def test_app_resolution_exact_contains_alias_and_fuzzy():
    assert apps.resolve_app("spotify", APPS)[0]["name"] == "Spotify"
    assert apps.resolve_app("word", APPS)[0]["name"] == "Microsoft Word"
    assert apps.resolve_app("navegador", APPS)[0]["name"] == "Google Chrome"
    assert apps.resolve_app("visual studio", APPS)[0]["name"] == "Visual Studio Code"
    assert apps.resolve_app("configuracion", APPS)[0]["name"] == "Configuración"
    assert apps.resolve_app("calculadorra", APPS)[0]["name"] == "Calculadora"


def test_unknown_app_returns_none():
    app, _ = apps.resolve_app("photoshop", APPS)
    assert app is None


def test_playlist_matching_prefers_user_playlists():
    playlists = [{"name": "Deep Focus"}, {"name": "Rock Nacional Clásico"}, {"name": "Gym 2026"}]
    assert spotify.match_playlist("rock nacional", playlists)["name"] == "Rock Nacional Clásico"
    assert spotify.match_playlist("deep focus", playlists)["name"] == "Deep Focus"
    assert spotify.match_playlist("jazz", playlists) is None


def test_best_match_threshold():
    best, scored = best_match("zzz", ["alpha", "beta"], key=lambda s: s, threshold=90)
    assert best is None and len(scored) == 2
