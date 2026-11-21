from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import load_config  # noqa: E402


def main() -> int:
    load_config()
    from jarvis.tools import google_ws
    from jarvis.tools.registry import ToolError

    try:
        print("Se abrira el navegador para autorizar a JARVIS en su cuenta de Google...")
        google_ws.get_credentials(interactive=True)
    except ToolError as exc:
        print(f"[ERROR] {exc}")
        return 1
    events = google_ws.events(1)
    _, unread = google_ws.list_mail(google_ws.QUERIES["no_leidos"], 1)
    tasks = google_ws.pending_tasks()
    print(f"[OK] Google vinculado. Hoy: {len(events)} eventos, ~{unread} correos sin leer, {len(tasks)} tareas pendientes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
