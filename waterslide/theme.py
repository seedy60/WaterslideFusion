"""OS-aware colour themes for the visual mirror and the 3D view.

Visual impairment is a spectrum: the 3D view is on for everyone, so the
paint must respect what the OS already knows about the player's eyes.

Resolution precedence (first hit wins):

1. **High contrast** (Windows ``HighContrast`` flag): the player has
   chosen a scheme with maximally contrasting colours - honour it and
   stand down from any styling of our own (flat black, pure white).
2. **Dark or light** (Windows ``AppsUseLightTheme``): follow the OS's
   app mode, so the game matches every other window on screen.
3. Anything unreadable or non-Windows defaults to dark, which suits a
   game better than a bright window.

The palette is re-resolved while the game runs: the Settings app flips
``AppsUseLightTheme`` the moment the user changes the OS theme, and the
watcher thread (1 s poll) picks the change up and tells the app to
repaint.  All reads are guarded - a missing key, a headless CI box or a
non-Windows platform yields the dark default rather than an exception.
"""

import sys
import threading
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------- palettes
# rgb tuples: bg window fill, fg main text, dim secondary/locked text,
# hud_* the 3D HUD variants, fog3 the GL fog colour (0..1 floats).

Palette = Dict[str, object]

DARK: Palette = {
    "name": "dark",
    "bg": (13, 20, 34),
    "fg": (220, 240, 255),
    "dim": (140, 160, 185),
    "hud_bg": (10, 12, 18),
    "hud_fg": (230, 240, 255),
    "hud_dim": (160, 175, 200),
    "fog3": (0.05, 0.08, 0.13),
}

LIGHT: Palette = {
    "name": "light",
    "bg": (238, 242, 247),
    "fg": (24, 34, 51),
    "dim": (95, 110, 130),
    "hud_bg": (250, 252, 255),
    "hud_fg": (20, 28, 44),
    "hud_dim": (90, 105, 125),
    "fog3": (0.90, 0.93, 0.97),
}

# High contrast: flat black, pure white.  No gradients, nothing quiet -
# the maximum-contrast pairing the OS scheme promises, and nothing that
# fights the user's chosen colours.
HC: Palette = {
    "name": "high contrast",
    "bg": (0, 0, 0),
    "fg": (255, 255, 255),
    "dim": (255, 255, 255),
    "hud_bg": (0, 0, 0),
    "hud_fg": (255, 255, 255),
    "hud_dim": (255, 255, 255),
    "fog3": (0.0, 0.0, 0.0),
}

_PALETTES = {"dark": DARK, "light": LIGHT, "hc": HC}


# ---------------------------------------------------------------- detection
def _read_windows() -> Tuple[Optional[str], Optional[str]]:
    """Read the OS's theme hints: (high_contrast, dark_or_light).

    ``HighContrast`` is a string flag; ``AppsUseLightTheme`` a DWORD the
    Settings app flips the instant the theme changes.  Unreadable or
    non-Windows returns (None, None).
    """
    if sys.platform != "win32":
        return None, None
    try:
        import winreg
    except ImportError:
        return None, None
    hc = None
    theme = None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            try:
                value, _kind = winreg.QueryValueEx(key, "HighContrast")
                hc = "hc" if str(value) == "1" else "not-hc"
            except OSError:
                pass
            try:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
                # 1 = light apps, 0 = dark apps; a missing flag keeps None
                theme = "light" if int(value) == 1 else "dark"
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return hc, theme


def detect() -> str:
    """Resolve the palette key: 'hc', 'dark' or 'light'."""
    hc, theme = _read_windows()
    if hc == "hc":
        return "hc"
    if theme in ("dark", "light"):
        return theme
    return "dark"


# ---------------------------------------------------------------- the theme
class Theme:
    """The resolved palette plus a background watcher that re-resolves it.

    ``scheme`` pins the resolution (tests); ``watch=False`` disables the
    background thread (tests, and the instant-recheck path uses poll()).
    """

    def __init__(self, scheme: Optional[str] = None, watch: bool = True) -> None:
        self._pinned = scheme is not None
        self.scheme: str = scheme or detect()
        self._lock = threading.Lock()
        self._changed = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if watch:
            self._thread = threading.Thread(target=self._watch, daemon=True)
            self._thread.start()

    def set_override(self, mode: str) -> None:
        """Apply the user's theme option: 'auto', 'dark' or 'light'.

        'auto' unpins and re-resolves from the OS; an explicit mode pins
        the scheme so the watcher can no longer change it (the user's
        choice owns the palette until they go back to auto).
        """
        if mode == "auto":
            with self._lock:
                self._pinned = False
            self.refresh()
        elif mode in ("dark", "light"):
            with self._lock:
                self._pinned = True
                if self.scheme != mode:
                    self.scheme = mode
                    self._changed = True
        else:
            raise ValueError(f"unknown theme mode {mode!r}")

    def refresh(self) -> bool:
        """Re-resolve from the OS.  Returns True when the scheme changed."""
        if self._pinned:
            return False
        new = detect()
        changed = False
        with self._lock:
            if new != self.scheme:
                self.scheme = new
                changed = True
        if changed:
            self._changed = True
        return changed

    def poll(self) -> bool:
        """True when the palette changed since the last call (repaint)."""
        if self._thread is None:
            # no watcher: re-resolve on demand (headless tests)
            return self.refresh()
        if self._changed:
            self._changed = False
            return True
        return False

    @property
    def override(self) -> str:
        """The user-facing mode: the pin if pinned, else 'auto'."""
        with self._lock:
            return self.scheme if self._pinned else "auto"

    def _watch(self) -> None:
        while not self._stop.wait(1.0):
            self.refresh()

    def stop(self) -> None:
        """Detach the watcher; poll() then re-resolves on demand."""
        self._stop.set()
        self._thread = None

    def quit(self) -> None:
        self.stop()

    # ---------------------------------------------------------- palette api
    @property
    def palette(self) -> Palette:
        with self._lock:
            return _PALETTES[self.scheme]

    def __getattr__(self, name: str):
        # colour lookups only; never intercept private/init machinery
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self.palette[name]
        except KeyError:
            raise AttributeError(f"theme palette has no colour {name!r}") from None

    def describe(self) -> str:
        return f"{self.palette['name']} mode"


_theme: Optional[Theme] = None


def get_theme() -> Theme:
    """Process-wide theme with its own watcher; created on first use."""
    global _theme
    if _theme is None:
        _theme = Theme()
    return _theme
