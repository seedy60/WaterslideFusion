"""Speech output for Waterslide Fusion (accessible port of Waterslide
Extreme, the 2009 iOS classic).

The game can auto-detect whether a screen reader is running and adapt
(see :func:`screen_reader_running`): sighted players get the 3D view and
no speech, blind players get the full self-voicing interface - with no
configuration needed on either side.

Engine stack (in preference order, each falling back to the next):

* **Prism** (:mod:`prism`, the Platform-agnostic Reader Interface for
  Speech and Messages, PyPI package ``prismatoid``) - Windows 10+.
  Speaks through NVDA, JAWS, SAPI, OneCore, UIA, ZoomText, SystemAccess
  and more.  SAPI-style backends expose rate/volume/pitch (0..1 floats)
  and voice selection by index; screen-reader backends speak in their own
  voice and usually support braille.
* **accessible_output2** (PyPI ``accessible_output2``) - the classic
  fallback for Windows 8.1 and earlier (and any system where Prism will
  not load).  Outputs: auto, NVDA, JAWS, SAPI5, SAPI4, SystemAccess,
  Dolphin, PC Talker, WindowEyes, eSpeak.  SAPI5 exposes voice names,
  rate -10..10, volume 0..100, pitch -10..10.

Every option the engines expose is surfaced in the in-game speech settings
menu: engine, output/backend, voice, rate, volume, pitch, and braille
mirroring.  All engine calls are wrapped so a broken backend can never
crash gameplay.
"""

from __future__ import annotations

import ctypes
import math
import os
import queue
import sys
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

SR_CONTROLLER_NAMES = ("nvda", "jaws", "system access", "window eyes",
                       "zoomtext", "zdsr", "sense reader", "boy pc reader",
                       "pc talker", "pctalker")


def _prism_controller_running() -> Optional[bool]:
    """True/False if Prism can tell whether a screen reader is running.

    ``create_best()`` is the honest signal: it prefers controller backends
    (NVDA, JAWS, ...) which only exist while that reader is actually
    running, and falls through to SAPI/OneCore (always present) when none
    is.  The backend's own name is the classification, so on a machine
    without any reader this reports False instead of probing each backend.
    """
    try:
        from prism import Context  # type: ignore
    except Exception:
        return None
    try:
        backend = Context().create_best()
    except Exception:
        return None
    if backend is None:
        return False
    name = str(getattr(backend, "name", "") or "").lower()
    return any(sr in name for sr in SR_CONTROLLER_NAMES)


def _ao2_controller_running() -> bool:
    """accessible_output2 path (Windows 8.1 and earlier)."""
    try:
        import accessible_output2.outputs  # noqa: F401

        from accessible_output2.outputs.jaws import Jaws
        from accessible_output2.outputs.nvda import NVDA
    except Exception:
        return False
    for cls in (NVDA, Jaws):
        try:
            if cls().is_active():
                return True
        except Exception:
            continue
    return False


def screen_reader_running() -> bool:
    """Is a screen reader actually running right now?

    Uses Prism's ``create_best()`` on Windows 10+ and accessible_output2's
    controller checks (NVDA / JAWS) on older Windows.  When neither
    library can answer, assume yes - the game was built speech-first, and
    a false 'no reader' is far worse for a blind player than a false
    'reader present' is for a sighted one.
    """
    if prism_supported():
        result = _prism_controller_running()
        if result is not None:
            return result
    return _ao2_controller_running()

SAMPLE_RATE = 22050
WAVE_MAPPER = 0xFFFFFFFF

# Windows 10 build number: Prism requires Windows 10 or later.
WIN10_BUILD = 10240


def _windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


def prism_supported() -> bool:
    return _windows_build() >= WIN10_BUILD or _windows_build() == 0


def _prime_prism_native() -> str:
    """Pre-load Prism's native CFFI extension in frozen (onefile) builds.

    prism/_native.py makes ``prism._prism_cffi`` importable by appending
    its folder to the package ``__path__`` at import time, but
    PyInstaller's frozen importer ignores runtime ``__path__`` changes.
    Loading the bundled .pyd explicitly (and registering its DLL
    directory) makes ``from ._prism_cffi import ffi, lib`` work.

    Returns a short status string for diagnostics.
    """
    import importlib.util

    if "prism._prism_cffi" in sys.modules:
        return "already loaded"
    meipass = getattr(sys, "_MEIPASS", "")
    if not meipass:
        return "not frozen"
    native_dir = os.path.join(meipass, "prism", "_native")
    pyd = os.path.join(native_dir, "_prism_cffi.pyd")
    if not os.path.exists(pyd):
        return f"pyd missing: {pyd}"
    try:
        os.add_dll_directory(native_dir)
    except Exception:
        pass
    try:
        spec = importlib.util.spec_from_file_location("prism._prism_cffi", pyd)
        if spec is None or spec.loader is None:
            return "spec unavailable"
        mod = importlib.util.module_from_spec(spec)
        sys.modules["prism._prism_cffi"] = mod
        spec.loader.exec_module(mod)
        return "loaded"
    except Exception as exc:
        sys.modules.pop("prism._prism_cffi", None)
        return f"load failed: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------- dsp
def _env(i: int, n: int, attack: int = 60, release: int = 220) -> float:
    a = min(1.0, i / max(1, attack))
    r = min(1.0, (n - i) / max(1, release))
    return a * r


def _tone(freq: float, ms: float, vol: float = 0.5, shape: str = "sine") -> List[float]:
    n = int(SAMPLE_RATE * ms / 1000)
    out: List[float] = []
    for i in range(n):
        ph = 2 * math.pi * freq * i / SAMPLE_RATE
        if shape == "square":
            v = 0.6 * (1.0 if math.sin(ph) >= 0 else -1.0)
        elif shape == "tri":
            v = 2 / math.pi * math.asin(math.sin(ph))
        else:
            v = math.sin(ph)
        out.append(v * vol * _env(i, n))
    return out


def _glide(f0: float, f1: float, ms: float, vol: float = 0.5) -> List[float]:
    n = int(SAMPLE_RATE * ms / 1000)
    out: List[float] = []
    for i in range(n):
        f = f0 + (f1 - f0) * (i / max(1, n - 1))
        out.append(math.sin(2 * math.pi * f * i / SAMPLE_RATE) * vol * _env(i, n))
    return out


def _silence(ms: float) -> List[float]:
    return [0.0] * int(SAMPLE_RATE * ms / 1000)


def _join(*parts: Sequence[float]) -> List[float]:
    out: List[float] = []
    for p in parts:
        out.extend(p)
    return out


class BeepCue:
    """Named instant cue patterns (freq, ms[, shape]) tuples."""

    DIAMOND = [(880, 50), (1174, 50), (1568, 70)]
    HEART = [(1046, 60), (1318, 60), (1568, 90)]
    GOD = [(659, 55), (830, 55), (1046, 55), (1318, 90)]
    BOOST = [(440, 40), (554, 40), (659, 40), (880, 40), (1108, 60)]
    CRAB = [(200, 90, "square")]
    DUCK = [(300, 70, "square"), (240, 90, "square")]
    TICK = [(880, 45)]
    TICK_HI = [(1320, 45)]
    FINISH = [(659, 60), (880, 60), (1046, 60), (1318, 60), (1568, 120)]
    ERROR = [(220, 140, "square")]

    @staticmethod
    def render(pattern: Sequence[Tuple]) -> List[float]:
        out: List[float] = []
        for item in pattern:
            freq, ms = item[0], item[1]
            shape = item[2] if len(item) > 2 else "sine"
            out.extend(_tone(freq, ms, 0.5, shape))
        return out


# ------------------------------------------------------------- winmm out
class _WaveOut:
    """Persistent winmm waveOut wrapper for tone cues.

    One device is opened on first use and kept for the session: re-opening
    waveOut per beep is slow (tens of ms) and can fail outright under load.
    Playback waits for the buffer to finish with a short, bounded timeout
    so a wedged device can never stall the speech worker.
    """

    def __init__(self) -> None:
        self.ok = False
        self._hwo = None
        try:
            self.winmm = ctypes.windll.winmm
            self.ok = True
        except Exception:
            self.winmm = None

    class _Format(ctypes.Structure):
        _fields_ = [
            ("wFormatTag", ctypes.c_ushort),
            ("nChannels", ctypes.c_ushort),
            ("nSamplesPerSec", ctypes.c_ulong),
            ("nAvgBytesPerSec", ctypes.c_ulong),
            ("nBlockAlign", ctypes.c_ushort),
            ("wBitsPerSample", ctypes.c_ushort),
            ("cbSize", ctypes.c_ushort),
        ]

    class _Header(ctypes.Structure):
        _fields_ = [
            ("lpData", ctypes.c_void_p),
            ("dwBufferLength", ctypes.c_ulong),
            ("dwBytesRecorded", ctypes.c_ulong),
            ("dwUser", ctypes.c_void_p),
            ("dwFlags", ctypes.c_ulong),
            ("dwLoops", ctypes.c_ulong),
            ("lpNext", ctypes.c_void_p),
            ("reserved", ctypes.c_void_p),
        ]

    def _device(self):
        if self._hwo is not None:
            return self._hwo
        if not self.ok:
            return None
        fmt = self._Format(
            wFormatTag=1,
            nChannels=1,
            nSamplesPerSec=SAMPLE_RATE,
            nAvgBytesPerSec=SAMPLE_RATE * 2,
            nBlockAlign=2,
            wBitsPerSample=16,
            cbSize=0,
        )
        hwo = ctypes.c_void_p()
        try:
            rc = self.winmm.waveOutOpen(
                ctypes.byref(hwo), WAVE_MAPPER, ctypes.byref(fmt), 0, 0, 0
            )
        except Exception:
            return None
        if rc != 0:
            return None
        self._hwo = hwo
        return hwo

    def selftest(self) -> bool:
        """Try to open the device and play a 60 ms tone; report success."""
        try:
            self.play(_tone(880, 60, 0.4), timeout=1.5)
            return self._hwo is not None
        except Exception:
            return False

    def close(self) -> None:
        """Release the persistent device (session shutdown)."""
        if self._hwo is not None and self.winmm is not None:
            try:
                self.winmm.waveOutClose(self._hwo)
            except Exception:
                pass
            self._hwo = None

    def play(self, samples: List[float], timeout: float = 6.0) -> None:
        hwo = self._device()
        if hwo is None:
            return
        import array

        pcm = array.array("h", (max(-32767, min(32767, int(s * 32767))) for s in samples))
        data = pcm.tobytes()
        buf = ctypes.create_string_buffer(data, len(data))
        hdr = self._Header()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(data)
        rc = self.winmm.waveOutPrepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if rc != 0:
            return
        rc = self.winmm.waveOutWrite(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if rc != 0:
            self.winmm.waveOutUnprepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            return
        deadline = time.monotonic() + timeout
        WHDR_DONE = 0x1
        while time.monotonic() < deadline and not (hdr.dwFlags & WHDR_DONE):
            time.sleep(0.005)
        try:
            self.winmm.waveOutUnprepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
        except Exception:
            pass


# =====================================================================
# Output engines.  All expose:
#   kind, name (display), speak(text, urgent, cue), stop(), braille(text)
#   voices() -> [names], outputs() -> [names], apply(settings dict)
# =====================================================================
def _estimate_seconds(text: str, rate: int = 50) -> float:
    """Rough spoken duration of ``text`` at the given 0-100 rate setting.

    Used purely to sequence non-urgent messages; screen-reader and SAPI
    voices vary, so the estimate is deliberately conservative (slightly
    long) and a real interruption is always allowed to preempt it.
    """
    words = max(1, len(text.split()))
    wps = 3.6 * (0.7 + (max(0, min(100, int(rate))) / 100.0) * 0.6)
    return words / wps + 0.30


class PrismOutput:
    """Prism backend (prismatoid).  Windows 10+."""

    kind = "prism"
    supports_volume = True
    supports_pitch = True

    def __init__(self, output_pref: str = "auto") -> None:
        self.ok = False
        self.name = "Prism"
        self.error = ""
        self.status = ""
        self._key = None
        self._backend = None
        self._sapi_like = False
        self._braille_ok = False
        self._output_pref = output_pref
        try:
            self.status = _prime_prism_native()
            from prism import Context  # type: ignore

            self._key = Context()
            self._backend = self._open(output_pref)
            if self._backend is not None:
                self.ok = True
                self.name = f"Prism ({self._backend.name})"
                feats = self._backend.features
                self._sapi_like = bool(getattr(feats, "supports_set_rate", False))
                self._braille_ok = bool(getattr(feats, "supports_braille", False))
        except Exception as exc:
            self.ok = False
            self.error = f"{type(exc).__name__}: {exc}"

    def _open(self, output_pref: str):
        key = self._key
        if key is None:
            return None
        try:
            if output_pref and output_pref != "auto":
                if key.exists(key.id_of(output_pref)):
                    return key.create(key.id_of(output_pref))
        except Exception as exc:
            self.error = f"open({output_pref}): {type(exc).__name__}: {exc}"
        try:
            return key.create_best()
        except Exception as exc:
            self.error = f"create_best: {type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------- queries
    def outputs(self) -> List[str]:
        names: List[str] = []
        if self._key is None:
            return names
        for known in ("NVDA", "SAPI", "OneCore", "JAWS", "UIA", "ZoomText",
                      "SystemAccess", "WindowEyes", "PCTalker", "ZDSR",
                      "SenseReader", "BoyPCReader"):
            try:
                if self._key.exists(self._key.id_of(known)):
                    names.append(known)
            except Exception:
                continue
        return names

    def voices(self) -> List[str]:
        b = self._backend
        if b is None:
            return []
        try:
            n = b.voices_count
            return [b.get_voice_name(i) for i in range(n)]
        except Exception:
            return []

    # ------------------------------------------------------------- control
    def apply(self, settings: Dict) -> None:
        b = self._backend
        if b is None or not self._sapi_like:
            return
        rate = settings.get("rate", 50)
        volume = settings.get("volume", 100)
        pitch = settings.get("pitch", 50)
        voice = settings.get("voice", "")
        try:
            b.rate = max(0.0, min(1.0, rate / 100.0))
        except Exception:
            pass
        try:
            b.volume = max(0.0, min(1.0, volume / 100.0))
        except Exception:
            pass
        try:
            b.pitch = max(0.0, min(1.0, pitch / 100.0))
        except Exception:
            pass
        if voice:
            try:
                names = self.voices()
                if voice in names:
                    b.voice = names.index(voice)
            except Exception:
                pass

    def speak(self, text: str, urgent: bool = False, cue: Optional[str] = None) -> None:
        b = self._backend
        if b is None:
            return
        try:
            b.speak(text, interrupt=bool(urgent))
        except Exception as exc:
            # A broken voice must never crash gameplay: raise so the worker
            # marks the engine dead instead of retrying the same failure.
            raise RuntimeError(f"prism speak failed: {exc}") from exc

    def stop(self) -> None:
        try:
            if self._backend is not None:
                self._backend.stop()
        except Exception:
            pass

    def braille(self, text: str) -> None:
        if self._braille_ok and self._backend is not None:
            try:
                self._backend.braille(text)
            except Exception:
                pass


class AO2Output:
    """accessible_output2 backend - classic fallback (Windows 8.1 and earlier)."""

    kind = "ao2"
    supports_volume = True
    supports_pitch = True

    _CLASSES = {
        "auto": ("accessible_output2.outputs.auto", "Auto"),
        "nvda": ("accessible_output2.outputs.nvda", "NVDA"),
        "jaws": ("accessible_output2.outputs.jaws", "Jaws"),
        "sapi5": ("accessible_output2.outputs.sapi5", "SAPI5"),
        "sapi4": ("accessible_output2.outputs.sapi4", "Sapi4"),
        "system_access": ("accessible_output2.outputs.system_access", "SystemAccess"),
        "dolphin": ("accessible_output2.outputs.dolphin", "Dolphin"),
        "pc_talker": ("accessible_output2.outputs.pc_talker", "PCTalker"),
        "window_eyes": ("accessible_output2.outputs.window_eyes", "WindowEyes"),
        "espeak": ("accessible_output2.outputs.e_speak", "ESpeak"),
        "voiceover": ("accessible_output2.outputs.voiceover", "VoiceOver"),
    }

    def __init__(self, output_pref: str = "auto") -> None:
        self.ok = False
        self.name = "accessible_output2"
        self.error = ""
        self._inst = None
        self._sapi5 = False
        self._output_pref = output_pref
        self._open(output_pref)

    def _open(self, output_pref: str) -> None:
        order = [output_pref] if output_pref and output_pref != "auto" else []
        order.append("auto")
        for pref in order:
            info = self._CLASSES.get(pref)
            if info is None:
                continue
            mod_name, cls_name = info
            try:
                import importlib

                mod = importlib.import_module(mod_name)
                cls = getattr(mod, cls_name)
                inst = cls()
                self._inst = inst
                self.ok = True
                self._sapi5 = cls_name == "SAPI5"
                self.name = f"accessible_output2 ({cls_name})"
                return
            except Exception as exc:
                self.error = f"{cls_name}: {type(exc).__name__}: {exc}"
                continue

    def outputs(self) -> List[str]:
        # Everything with a class; Auto.first probes availability.
        available = list(self._CLASSES.keys())
        return available

    def voices(self) -> List[str]:
        if self._sapi5 and self._inst is not None:
            try:
                return list(self._inst.list_voices())
            except Exception:
                return []
        return []

    def apply(self, settings: Dict) -> None:
        inst = self._inst
        if inst is None or not self._sapi5:
            return
        voice = settings.get("voice", "")
        if voice:
            try:
                inst.set_voice(voice)
            except Exception:
                pass
        try:
            inst.set_rate(max(-10, min(10, settings.get("rate", 50) / 5.0 - 10.0)))
        except Exception:
            pass
        try:
            inst.set_volume(max(0, min(100, int(settings.get("volume", 100)))))
        except Exception:
            pass
        try:
            inst.set_pitch(max(-10, min(10, settings.get("pitch", 50) / 5.0 - 10.0)))
        except Exception:
            pass

    def speak(self, text: str, urgent: bool = False, cue: Optional[str] = None) -> None:
        if self._inst is None:
            raise RuntimeError("ao2 not initialised")
        try:
            self._inst.speak(text, interrupt=bool(urgent))
        except Exception as exc:
            raise RuntimeError(f"ao2 speak failed: {exc}") from exc

    def stop(self) -> None:
        inst = self._inst
        if inst is None:
            return
        if self._sapi5:
            try:
                inst.silence()
            except Exception:
                pass

    def braille(self, text: str) -> None:
        try:
            if self._inst is not None and hasattr(self._inst, "braille"):
                self._inst.braille(text)
        except Exception:
            pass


# =====================================================================
# Facade
# =====================================================================
class Speech:
    """Queued, interruptible speech with engine fallbacks + beep cues.

    All engine work happens on a private worker thread because both Prism
    and accessible_output2 prefer single-threaded use.  ``configure()``
    rebuilds the engine on the worker thread; ``probe()`` asks it for
    capabilities (used by the settings menu).
    """

    def __init__(self, app=None) -> None:
        self.app = app
        self.enabled = True
        self.audio_mode = "none"  # filled in by the app's audio self-check
        self.audio_detail = ""
        opts = getattr(app, "options", None)
        self.engine_pref: str = getattr(opts, "speech_engine", "prism") or "prism"
        self.output_pref: str = getattr(opts, "speech_output", "auto") or "auto"
        self.rate: int = getattr(opts, "speech_rate", 50)
        self.volume: int = getattr(opts, "speech_volume", 100)
        self.pitch: int = getattr(opts, "speech_pitch", 50)
        self.voice: str = getattr(opts, "speech_voice", "")
        self.braille: bool = getattr(opts, "speech_braille", False)
        # "auto" follows screen_reader_running(); "on"/"off" are explicit.
        self.mode_pref: str = getattr(opts, "speech", "auto")
        if self.mode_pref not in ("auto", "on", "off"):
            self.mode_pref = "on" if self.mode_pref else "off"
        # In auto mode the engine itself is only built if a screen reader is
        # actually running - otherwise Prism would happily fall through to
        # SAPI/OneCore and talk over a sighted player's games.
        self._defer_engine = self.mode_pref == "auto" and not screen_reader_running()
        self.enabled = not self._defer_engine if self.mode_pref == "auto" \
            else self.mode_pref == "on"
        self.engine_display = "speech starting..."
        self.engine_kind = "none"
        self.build_errors: List[str] = []
        # monotonic time until which the engine is expected to be busy;
        # non-urgent messages wait past this instead of interrupting
        self._busy_until = 0.0
        self._queue: "queue.Queue[Optional[Tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._wave = _WaveOut()
        self._last_spoken: Dict[str, float] = {}
        self._probe_cache: Optional[Dict] = None  # memoized engine capabilities
        self._force_build = False  # set when leaving auto-deferred state
        self._start_thread()

    # ------------------------------------------------------------------ api
    def describe_mode(self) -> str:
        """Spoken/printed line about the current speech mode and state."""
        if self.mode_pref == "on":
            return "Speech on."
        if self.mode_pref == "off":
            return "Speech off. Turn it on in the options menu."
        return ("No screen reader running. Speech off; press shift V to turn "
                "speech on, or set it in options." if not self.enabled
                else "Screen reader detected. Speech on.")

    def say(self, text: str, urgent: bool = False, cue: Optional[str] = None) -> None:
        """Queue a message.

        Non-urgent messages *wait their turn*: the worker delays them until
        the engine should be idle, so a line queued right after another
        (results, then the menu) is appended rather than interrupting
        mid-sentence.  Urgent messages still preempt immediately.
        """
        if not self.enabled or not text:
            return
        now = time.monotonic()
        if self._last_spoken.get(text, 0.0) > now - 1.0:
            return
        self._last_spoken[text] = now
        # Decide the wait at enqueue time: a non-urgent line is pinned to
        # speak after whatever was talking when it was queued.  The worker
        # sleeps out the wait only once the item reaches the head of the
        # queue, so items behind it keep their own order.
        delay = Speech._queue_delay(bool(urgent), self._busy_until, now)
        self._queue.put(("say", text, bool(urgent), None, cue, now + delay))

    def beep(self, pattern: Sequence[Tuple], urgent: bool = False) -> None:
        if not self.enabled:
            return
        self._queue.put(("beep", BeepCue.render(list(pattern))))

    def configure(self, engine: Optional[str] = None, output: Optional[str] = None,
                  rate: Optional[int] = None, volume: Optional[int] = None,
                  pitch: Optional[int] = None, voice: Optional[str] = None,
                  braille: Optional[bool] = None, rebuild: bool = False) -> None:
        """Update settings; ``rebuild=True`` also re-opens the backend."""
        if engine is not None:
            self.engine_pref = engine
        if output is not None:
            self.output_pref = output
        if rate is not None:
            self.rate = int(rate)
        if volume is not None:
            self.volume = int(volume)
        if pitch is not None:
            self.pitch = int(pitch)
        if voice is not None:
            self.voice = voice
        if braille is not None:
            self.braille = bool(braille)
        # drop the memoized capabilities now (calling thread): probes after
        # this must reflect the new engine, and the worker may still be
        # processing the queued rebuild for a moment
        self._probe_cache = None
        self._queue.put(("configure", rebuild))

    def shut_up(self, drop_pending: bool = False) -> None:
        # Clear tracker state on the calling thread too: navigation lines
        # are queued immediately after this, and they must not inherit a
        # stale busy estimate from the speech being interrupted (that was
        # the arrow-key lag) nor be swallowed by the 1-second duplicate
        # guard (that was the silent first press).
        self._busy_until = 0.0
        self._last_spoken.clear()
        try:
            self._queue.put(("stop",))
        except Exception:
            pass
        # everything already queued: re-queue it (default), or drop it
        # when a dialog takes over and stale chatter has no business
        # playing after the new message
        drained: List[Tuple] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item[0] != "say" or not drop_pending:
                drained.append(item)
        for item in drained:
            self._queue.put(item)

    def quit(self) -> None:
        self.shut_up()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._wave.close()

    # ---------------------------------------------------------------- probe
    def probe(self, timeout: float = 2.0) -> Dict:
        """Ask the worker for engine capabilities (used by settings menus).

        Memoized: option choosers probe on every keypress, and while the
        worker is busy speaking the last confirmation a live probe can
        time out and return empty lists - which made the choice lists
        (and their wrap points) change between keypresses.  The cache is
        dropped whenever the engine is rebuilt or speech is stopped, and
        empty answers are never cached so a cold probe retries.
        """
        if self._probe_cache is not None:
            return self._probe_cache
        reply: "queue.Queue[Dict]" = queue.Queue(maxsize=1)
        self._queue.put(("probe", reply))
        try:
            info = reply.get(timeout=timeout)
        except queue.Empty:
            info = {"engine": self.engine_kind, "name": self.engine_display,
                    "outputs": [], "voices": [], "ok": False}
        if info.get("outputs") or info.get("voices") or info.get("ok"):
            self._probe_cache = info
        return info

    # --------------------------------------------------------------- worker
    def _start_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    @staticmethod
    def _queue_delay(urgent: bool, busy_until: float, now: float) -> float:
        """Seconds a message must wait before speaking.

        Urgent messages preempt immediately.  Non-urgent lines wait until
        the engine should be idle so they append instead of interrupting
        what is being spoken (the engine backends give us no reliable
        is-speaking query - Prism's NVDA backend does not implement it -
        so we track expected speech time).
        """
        if urgent:
            return 0.0
        return max(0.0, busy_until - now - 0.05)

    def _worker(self) -> None:
        out = None
        built_for = None  # (engine, output) the current `out` was built for
        while True:
            item = self._queue.get()
            if item is None:
                break
            tag = item[0]
            try:
                if tag == "configure":
                    rebuild = bool(item[1])
                    if self._defer_engine and not self._force_build:
                        # auto mode with no screen reader: stay silent and
                        # never build an engine (no SAPI/OneCore fallback
                        # talking over a sighted player).
                        out = None
                        self.engine_display = "speech off (no screen reader)"
                        self.engine_kind = "none"
                        self._probe_cache = None
                    elif rebuild or out is None:
                        out = self._build()
                        built_for = (self.engine_pref, self.output_pref)
                        self._probe_cache = None  # a new engine, new capabilities
                    else:
                        out.apply(self._settings())
                elif tag == "stop":
                    if out is not None:
                        out.stop()
                    self._busy_until = 0.0  # interrupted: engine is idle now
                elif tag == "probe":
                    reply = item[1]
                    if self._defer_engine and not self._force_build:
                        reply.put({"engine": "none", "name": self.engine_display,
                                   "outputs": [], "voices": [], "ok": False,
                                   "error": "", "build_errors": []})
                        continue
                    if out is None:
                        out = self._build()
                        built_for = (self.engine_pref, self.output_pref)
                    reply.put({
                        "engine": out.kind if out else "none",
                        "name": out.name if out else "no engine",
                        "outputs": out.outputs() if out else [],
                        "voices": out.voices() if out else [],
                        "ok": bool(out and out.ok),
                        "error": str(getattr(out, "error", "") or ""),
                        "build_errors": list(getattr(self, "build_errors", []) or []),
                    })
                elif tag == "beep":
                    self._play_samples(item[1])
                elif tag == "say":
                    _, text, urgent, samples, cue, speak_at = item
                    if samples is not None:
                        self._play_samples(samples)
                        continue
                    if self._defer_engine and not self._force_build:
                        continue
                    if out is None:
                        out = self._build()
                        built_for = (self.engine_pref, self.output_pref)
                    if out is None:
                        continue
                    # The wait was computed at enqueue time; sleeping only
                    # for this item's own remainder keeps navigation lines
                    # from inheriting each other's delays.
                    wait = speak_at - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    if not self.enabled:
                        continue
                    try:
                        out.speak(text, urgent, cue)
                        if self.braille:
                            out.braille(text)
                        self._busy_until = time.monotonic() + _estimate_seconds(text, self.rate)
                    except Exception:
                        # engine broke mid-session: give up on this line and
                        # mark the engine dead (next configure() rebuilds)
                        out = None
                        self.engine_display = "no speech engine"
                        self.engine_kind = "none"
            except Exception:
                continue

    def _settings(self) -> Dict:
        return {
            "rate": self.rate,
            "volume": self.volume,
            "pitch": self.pitch,
            "voice": self.voice,
        }

    def _build(self):
        """Build the preferred engine, falling back down the stack."""
        errors: List[str] = []
        prefs = [self.engine_pref] if self.engine_pref in ("prism", "ao2") else []
        prefs += [p for p in ("prism", "ao2") if p not in prefs]
        if not prism_supported():
            # Prism requires Windows 10+; on 8.1 and earlier use ao2.
            prefs = [p for p in prefs if p != "prism"]
        for kind in prefs:
            out = None
            if kind == "prism":
                out = PrismOutput(self.output_pref)
            elif kind == "ao2":
                out = AO2Output(self.output_pref)
            if out is not None and out.ok:
                out.apply(self._settings())
                self.engine_display = out.name
                self.engine_kind = out.kind
                self.build_errors = errors
                return out
            if out is not None:
                errors.append(f"{kind}: {getattr(out, 'error', '') or 'unavailable'}")
        # No speech engine could be built: speech stays silent rather than
        # beeping words.  Instant beep cues (diamonds, god mode, ...) still
        # work through the shared winmm wrapper.
        self.engine_display = "no speech engine"
        self.engine_kind = "none"
        self.build_errors = errors
        return None

    def _play_samples(self, samples: List[float]) -> None:
        if self._wave.ok:
            self._wave.play(samples)
            return
        try:  # final fallback: pygame
            import numpy as np
            import pygame

            arr = (np.array(samples, dtype=np.float32) * 32000).astype(np.int16)
            stereo = np.ascontiguousarray(np.column_stack([arr, arr]))
            snd = pygame.sndarray.make_sound(stereo)
            snd.play()
            while pygame.mixer.get_busy():
                time.sleep(0.02)
        except Exception:
            time.sleep(min(0.5, len(samples) / SAMPLE_RATE))
