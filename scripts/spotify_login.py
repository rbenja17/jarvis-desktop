from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import load_config  # noqa: E402


def main() -> int:
    load_config()
    import spotipy

    from jarvis.tools import spotify
    from jarvis.tools.registry import ToolError

    try:
        manager = spotify.auth_manager(open_browser=True)
    except ToolError as exc:
        print(f"[ERROR] {exc}")
        return 1
    print("Se abrira el navegador para autorizar a JARVIS en su cuenta de Spotify...")
    manager.get_access_token()
    me = spotipy.Spotify(auth_manager=manager).current_user()
    print(f"[OK] Spotify vinculado. Cuenta: {me.get('display_name')}")
    if me.get("product") not in (None, "premium"):
        print("[AVISO] Controlar la reproduccion requiere Spotify Premium.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
