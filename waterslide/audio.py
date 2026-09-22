"""Audio for Waterslide Fusion: original SFX, procedural water/wind, panning.

Uses pygame's mixer when available (all original WAVs are plain PCM16
stereo).  If the mixer cannot start, falls back to Windows winmm
``PlaySound`` for one-shot effects and silently disables panning.

Positional sounds are stereo-panned by horizontal angle so a blind player
can hear *where* a diamond or an obstacle is: hard left ... centre ... hard
right.  Volume carries distance.

The water/wind loop is synthesised at runtime (numpy) so it needs no extra
asset: bubbling noise whose intensity follows speed, plus wind above a
speed threshold.
"""

from __future__ import annotations

import ctypes
import math
import os
from typing import Dict, List, Optional, Tuple

SAMPLE_RATE = 22050

from .paths import assets_root

ASSETS = assets_root()

SOUND_FILES = {
    "countdown": "content/sounds/3-2-1.wav",
    "start": "content/sounds/start.wav",
    "menu": "content/sounds/Menu-Button-Press.wav",
    "diamond": "content/sounds/Pick-Up-Diamond.wav",
    "heart": "content/sounds/Pick-Up-Heart.wav",
    "godmode": "content/sounds/Pick-Up-God-Mode.wav",
    "chevron": "content/sounds/Hit-Cheveron.wav",
    "crab": "content/sounds/Crab.wav",
    "duck": "content/sounds/Evil-Duck.wav",
    "crab_hit": "content/sounds/Female-Hit-Crab.wav",
    "crab_hit_male": "content/sounds/Male-Hit-Crab.wav",
    "crab_hit_female": "content/sounds/Female-Hit-Crab.wav",
    "fall": "content/sounds/female-fall.wav",
    "fall_male": "content/sounds/Male-Fall-Out-Of-Slide.wav",
    "fall_female": "content/sounds/female-fall.wav",
    "loop": "content/sounds/Male-Loop-The-Loop.wav",
    "loop_male": "content/sounds/Male-Loop-The-Loop.wav",
    "loop_female": "content/sounds/Female-Loop-The-Loop.wav",
    "godhit": "content/sounds/hit-duck-god-mode.wav",
    "brake": "content/sounds/Brake.wav",
}


def sfx_key(base: str, voice: str) -> str:
    """Voice-specific variant of a one-shot ('loop' + 'female' -> 'loop_female').

    Callers fall back to the base key when the variant was not loaded.
    """
    return f"{base}_{voice}" if voice else base

MUSIC_FILE = "content/sounds/evening_no_intro.mp3"


def _asset(path: str) -> str:
    return os.path.join(ASSETS, *path.split("/"))


def pan_from_angle(angle: float) -> float:
    """Horizontal angle (radians, 0 = straight ahead) -> pan in [-1, 1]."""
    return math.sin(max(-math.pi / 2, min(math.pi / 2, angle)))


class AudioEngine:
    """Load the original effects, synthesise ambience, play positionally."""

    def __init__(self, app=None) -> None:
        self.app = app
        self.mode = "none"
        self._sounds: Dict[str, object] = {}
        self._channels: List[object] = []
        self._ambient_channel = None
        self._music_channel = None
        self._music_snd = None
        self._winmm = None
        self.music_volume = 50
        self.sound_volume = 80
        self._water = None
        self._wind = None
        self._water_level = 0.0
        self._wind_level = 0.0
        self.enabled = True
        self._init_mixer()
        self._load_sounds()
        self._build_ambience()

    # ---------------------------------------------------------------- setup
    def _init_mixer(self) -> None:
        try:
            import pygame

            pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 1024)
            if pygame.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(SAMPLE_RATE, -16, 2, 1024)
            pygame.mixer.set_num_channels(16)
            # Reserve the last three channels for the ambience beds (water,
            # wind) and the music theme.  Without a reservation,
            # pygame.mixer.find_channel(True) -- which play() uses when all
            # channels are busy -- steals the *longest-playing* channel,
            # which is always a bed: a dense burst of pickups (more
            # concurrent one-shots than the 13 free channels) used to evict
            # the water/wind loop for the rest of the stage.
            pygame.mixer.set_reserved(3)
            self.mode = "pygame"
            self._music_channel = pygame.mixer.Channel(15)
            return
        except Exception:
            self.mode = "none"
        # winmm fallback (Windows): one-shot, no panning
        try:
            self._winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
            self.mode = "winmm"
        except Exception:
            self.mode = "none"

    def _load_sounds(self) -> None:
        if self.mode != "pygame":
            return
        import pygame

        for key, rel in SOUND_FILES.items():
            path = _asset(rel)
            try:
                self._sounds[key] = pygame.mixer.Sound(path)
            except Exception:
                self._sounds[key] = None

    # ------------------------------------------------------------- ambience
    def _build_ambience(self) -> None:
        if self.mode != "pygame":
            return
        try:
            import numpy
            import pygame
        except ImportError:
            return

        def make(duration: float, lowcut: bool) -> object:
            n = int(SAMPLE_RATE * duration)
            rng = numpy.random.default_rng(20090710)
            noise = rng.standard_normal(n).astype(numpy.float32)
            # cheap one-pole lowpass for "water" body
            out = numpy.empty_like(noise)
            acc = 0.0
            alpha = 0.25 if lowcut else 0.7
            for i in range(n):
                acc += alpha * (noise[i] - acc)
                out[i] = acc
            # amplitude wobble so it "flows"
            t = numpy.arange(n) / SAMPLE_RATE
            wobble = 0.6 + 0.4 * numpy.sin(2 * numpy.pi * 2.1 * t + 1.3)
            mono = (out / (numpy.max(numpy.abs(out)) + 1e-6)) * wobble
            stereo = numpy.zeros((n, 2), dtype=numpy.int16)
            # slow L/R decorrelation for width
            phase = numpy.roll(mono, 220)
            stereo[:, 0] = (0.5 * (0.8 * mono + 0.2 * phase) * 32767 * 0.95).astype(numpy.int16)
            stereo[:, 1] = (0.5 * (0.8 * phase + 0.2 * mono) * 32767 * 0.95).astype(numpy.int16)
            arr = pygame.sndarray.make_sound(numpy.ascontiguousarray(stereo))
            return arr

        self._water = make(4.0, lowcut=True)
        self._wind = make(6.0, lowcut=False)

    def _set_channel_volume(self, ch, left: float, right: float) -> None:
        try:
            ch.set_volume(left, right)
        except Exception:
            ch.set_volume(max(left, right))

    # ----------------------------------------------------------------- play
    def play(self, key: str, pan: float = 0.0, volume: float = 1.0) -> None:
        """Play a one-shot; pan in [-1, 1], volume 0..1 (scaled by sound_volume).

        Never steals a busy channel: force mode would grab the ambience
        beds (the longest-playing sounds) during pickup storms.  With all
        free channels busy the one-shot is simply dropped.
        """
        if not self.enabled or volume <= 0.01:
            return
        if self.mode == "pygame":
            import pygame

            snd = self._sounds.get(key)
            if not snd and "_" in key:
                # voice-variant key missing: fall back to the base sound
                snd = self._sounds.get(key.split("_", 1)[0])
            if not snd:
                return
            ch = pygame.mixer.find_channel(False)
            if ch is None:
                return  # every free channel busy: drop the one-shot, keep the beds
            level = volume * (self.sound_volume / 100.0)
            left = level * min(1.0, 1.0 - max(0.0, pan))
            right = level * min(1.0, 1.0 + min(0.0, pan))
            self._set_channel_volume(ch, left, right)
            ch.play(snd)
        elif self.mode == "winmm":
            rel = SOUND_FILES.get(key)
            if not rel:
                return
            flags = 0x20000 | 0x2000  # SND_FILENAME | SND_ASYNC
            try:
                self._winmm.PlaySoundW(_asset(rel), None, flags)  # type: ignore[union-attr]
            except Exception:
                pass

    def sound_length(self, key: str) -> float:
        """Duration of a one-shot in seconds (0.0 if unknown/unavailable).

        Used to sequence the UI against the soundscape - e.g. the death
        dialog waits for the fall cry to finish before appearing.
        """
        if not self.enabled or self.mode != "pygame":
            return 0.0
        try:
            snd = self._sounds.get(key)
            if not snd and "_" in key:
                snd = self._sounds.get(key.split("_", 1)[0])
            return round(snd.get_length(), 2) if snd is not None else 0.0
        except Exception:
            return 0.0

    def ui_select(self) -> None:
        self.play("menu", 0.0, 0.9)

    def self_check(self) -> str:
        """Probe the audio pipeline; return a short human-readable report.

        The game self-voices, so 'no sound at all' should always be
        explainable: this reports which mixer mode came up, whether the
        original WAVs actually loaded, and whether the ambience beds were
        synthesised.
        """
        if self.mode == "pygame":
            loaded = sum(1 for s in self._sounds.values() if s is not None)
            parts = [f"pygame mixer, {loaded}/{len(SOUND_FILES)} original sounds loaded"]
            if self._water is None or self._wind is None:
                parts.append("ambience synthesis FAILED (numpy missing?)")
            else:
                parts.append("procedural water/wind ready")
            if loaded == 0:
                parts.append("NOTE: no WAVs loaded - assets missing? run the "
                             "ipa extractor to rebuild assets/content/sounds")
            return "; ".join(parts)
        if self.mode == "winmm":
            return "winmm fallback (no panning; pygame mixer unavailable)"
        return "NO audio device available - sound effects disabled"

    def announce_self_check(self) -> None:
        """Speak the audio self-check (spoken right after the welcome)."""
        if self.app is not None:
            self.app.speech.say(f"Audio: {self.self_check()}.", urgent=True)

    # -------------------------------------------------------------- ambient
    def start_ambience(self) -> None:
        """Start the looping water + wind beds on reserved channels.

        The last three mixer channels are reserved (set_reserved in
        _init_mixer): beds here on 13/14 and music on 15, so one-shot
        effects can never steal them.  Volume starts at an audible floor
        and :meth:`set_splash` raises it with speed.
        """
        if self.mode != "pygame":
            return
        import pygame

        try:
            n = pygame.mixer.get_num_channels()
            if n < 16:
                pygame.mixer.set_num_channels(16)
            for attr, snd in (("_water_ch", self._water), ("_wind_ch", self._wind)):
                if snd is None:
                    continue
                ch = getattr(self, attr, None)
                if ch is None:
                    ch = pygame.mixer.Channel(13 if attr == "_water_ch" else 14)
                    setattr(self, attr, ch)
                ch.play(snd, loops=-1)
                self._set_channel_volume(ch, 0.45, 0.45)
        except Exception:
            import traceback

            traceback.print_exc()  # never fail silently again

    def stop_ambience(self) -> None:
        if self.mode != "pygame":
            return
        try:
            if getattr(self, "_water_ch", None):
                self._water_ch.stop()
            if getattr(self, "_wind_ch", None):
                self._wind_ch.stop()
        except Exception:
            pass

    def pause_ambience(self) -> None:
        """Suspend the water + wind beds in place (pausing the stage hushes
        the whole soundscape, not just the music).

        Resuming is explicit via :meth:`resume_ambience`; stopping the beds
        (finish, death, leaving for a menu) clears the suspension.
        """
        if self.mode != "pygame":
            return
        try:
            for attr in ("_water_ch", "_wind_ch"):
                ch = getattr(self, attr, None)
                if ch is not None and ch.get_busy():
                    ch.pause()
        except Exception:
            pass

    def resume_ambience(self) -> None:
        """Continue suspended beds exactly where they left off."""
        if self.mode != "pygame":
            return
        try:
            for attr in ("_water_ch", "_wind_ch"):
                ch = getattr(self, attr, None)
                if ch is not None:
                    ch.unpause()
        except Exception:
            pass

    def set_splash(self, water: float, wind: float, pan: float = 0.0) -> None:
        """Update ambience intensity (0..1 each) and stereo position."""
        if self.mode != "pygame":
            return
        water = max(0.0, min(1.0, water))
        wind = max(0.0, min(1.0, wind))
        lpan = 1.0 - max(0.0, pan)
        rpan = 1.0 + min(0.0, pan)
        try:
            if getattr(self, "_water_ch", None):
                self._set_channel_volume(
                    self._water_ch, 1.0 * water * lpan, 1.0 * water * rpan
                )
            if getattr(self, "_wind_ch", None):
                self._set_channel_volume(self._wind_ch, 0.6 * wind * lpan, 0.6 * wind * rpan)
        except Exception:
            pass

    # ---------------------------------------------------------------- music
    def play_music(self) -> None:
        """Loop the original menu theme on the reserved music channel.

        The MP3's own tail is dead air, so a plain ``loops=-1`` leaves an
        audible hole every cycle.  Instead we loop a trimmed copy (decoded
        once, silence stripped) for a seamless repeat.
        """
        if self.mode != "pygame":
            return
        try:
            import pygame

            path = _asset(MUSIC_FILE)
            if not os.path.exists(path):
                return
            if self._music_channel is None:
                return
            if self._music_channel.get_busy():
                # already playing - or suspended in place by a pause, in
                # which case continue the song where it left off.  (A
                # paused channel still reports busy; unpause is a no-op
                # when it was not paused.)
                self._music_channel.unpause()
                self._apply_music_volume()
                return
            if self._music_snd is None:
                snd = pygame.mixer.Sound(path)
                self._music_snd = self._trim_silence(snd)
            self._music_channel.play(self._music_snd, loops=-1, fade_ms=800)
            self._apply_music_volume()
        except Exception:
            import traceback

            traceback.print_exc()

    @staticmethod
    def _trim_silence(snd):
        """Trim trailing (and leading) near-silence so looping is seamless."""
        try:
            import pygame
            import numpy as np

            arr = pygame.sndarray.array(snd).astype(np.int16)
            mag = np.abs(arr).max(axis=1)
            thresh = max(64, int(mag.max() * 0.004))
            loud = np.flatnonzero(mag > thresh)
            if len(loud) and (loud[-1] + 1 - loud[0]) > 4410:  # span > ~0.2 s
                arr = arr[loud[0]:loud[-1] + 1]
            return pygame.sndarray.make_sound(np.ascontiguousarray(arr))
        except Exception:
            return snd

    def set_music_volume(self, pct: int) -> None:
        self.music_volume = max(0, min(100, int(pct)))
        self._apply_music_volume()

    def _apply_music_volume(self) -> None:
        try:
            if self._music_channel is not None:
                self._music_channel.set_volume(self.music_volume / 100.0)
        except Exception:
            pass

    def stop_music(self) -> None:
        try:
            if self._music_channel is not None:
                self._music_channel.stop()
        except Exception:
            pass

    def pause_music(self) -> None:
        """Suspend the theme in place (pausing the stage pauses the song).

        Resuming is play_music()'s job: a paused channel still reports
        busy, so the next policy tick unpauses and the song continues
        exactly where it left off.
        """
        try:
            if self._music_channel is not None and self._music_channel.get_busy():
                self._music_channel.pause()
        except Exception:
            pass

    # --------------------------------------------------------------- control
    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if not on:
            self.stop_ambience()
            self.set_splash(0, 0)

    def quit(self) -> None:
        self.stop_ambience()
        self.stop_music()
        if self.mode == "winmm" and self._winmm is not None:
            try:
                self._winmm.PlaySoundW(None, None, 0)  # stop async playback
            except Exception:
                pass


def describe_position(angle: float) -> str:
    """Human-readable stereo position for speech: 'left', 'ahead', 'right'..."""
    deg = math.degrees(angle)
    if deg < -60:
        return "far left"
    if deg < -20:
        return "left"
    if deg <= 20:
        return "ahead"
    if deg <= 60:
        return "right"
    return "far right"
