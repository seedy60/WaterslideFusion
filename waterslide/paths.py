"""Locate the game's asset directory in every deployment mode.

Priority order:

1. an ``assets/`` folder next to the running executable (frozen builds:
   lets players drop in extra levels/sounds);
2. the PyInstaller bundle directory (``sys._MEIPASS``), where the spec
   file packs ``content/``;
3. the repository layout (two directories up from this file).

The returned directory *directly contains* ``content/level`` and
``content/sounds``.
"""

from __future__ import annotations

import os
import sys


def _has_content(directory: str) -> bool:
    return os.path.isdir(os.path.join(directory, "content", "level"))


def assets_root() -> str:
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "assets"))
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(meipass)
    else:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(repo, "assets"))
    candidates.append(os.path.join(os.getcwd(), "assets"))
    for c in candidates:
        if _has_content(c):
            return c
    # default: repo layout (find_level_dir reports a friendly error anyway)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo, "assets")
