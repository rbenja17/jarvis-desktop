from __future__ import annotations

import multiprocessing
import sys


def _main() -> int:
    multiprocessing.freeze_support()
    from jarvis.__main__ import main

    return main()


if __name__ == "__main__":
    sys.exit(_main())
