"""High scores and options for Waterslide Fusion (RecordHandler-style).

Stored as an INI in the user's home directory:
``%APPDATA%\\WaterslideFusion\\records.cfg`` with sections
``[score]`` (top ten), ``[options]`` and ``[progress]`` (unlocked stages).
Scores are ``name,score,stage`` rows, sorted descending.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

DEFAULT_NAME = "player"


def records_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WaterslideFusion")


def records_path() -> str:
    return os.path.join(records_dir(), "records.cfg")


@dataclass
class ScoreRow:
    name: str
    score: int
    stage: int

    def as_line(self) -> str:
        safe = self.name.replace(",", " ").replace(";", " ") or DEFAULT_NAME
        return f"{safe},{self.score},{self.stage}"


@dataclass
class Options:
    # Speech mode: "auto" follows a running screen reader (sighted players
    # get the 3D view and no speech), "on"/"off" force it.
    speech: str = "auto"
    sound: bool = True
    music: bool = True   # on by default - the original menu music is nostalgia
    # Speech engine stack: "prism" (Windows 10+) or "ao2" (older Windows).
    speech_engine: str = "prism"
    # Visual theme: "auto" follows the OS dark mode / high contrast;
    # "dark" or "light" overrides the OS detection.
    theme_mode: str = "auto"
    # Output within the engine: "auto" or a backend name (Prism: NVDA,
    # SAPI, OneCore, JAWS, UIA, ZoomText...; ao2: nvda, jaws, sapi5, sapi4,
    # system_access, dolphin, pc_talker, window_eyes, espeak).
    speech_output: str = "auto"
    # Voice: canonical voice name; empty = engine default.
    speech_voice: str = ""
    speech_rate: int = 50        # 0..100 (50 = engine default pace)
    speech_volume: int = 100     # 0..100
    speech_pitch: int = 50       # 0..100 (50 = engine default)
    speech_braille: bool = False # mirror spoken text to a braille display
    voice: str = "male"          # which voice acts: male or female (SFX variants)
    music_volume: int = 50       # 0..100, music bed level (PgUp/PgDn)
    sound_volume: int = 80       # 0..100, sound effect level (Shift+PgUp/PgDn)
    speedrun_mode: bool = False  # survival mode = fall ends the stage
    speed_units: str = "mph"     # spoken speed unit: mph or kmh
    assist_pan_ahead: float = 0.12  # lookahead seconds for auto-centering

    def as_lines(self) -> List[str]:
        return [
            "speech,%s" % self.speech,
            "sound,%d" % (1 if self.sound else 0),
            "music,%d" % (1 if self.music else 0),
            "speech_engine,%s" % self.speech_engine,
            "theme_mode,%s" % self.theme_mode,
            "speech_output,%s" % self.speech_output,
            "speech_voice,%s" % self.speech_voice,
            "speech_rate,%d" % self.speech_rate,
            "speech_volume,%d" % self.speech_volume,
            "speech_pitch,%d" % self.speech_pitch,
            "speech_braille,%d" % (1 if self.speech_braille else 0),
            "voice,%s" % self.voice,
            "music_volume,%d" % self.music_volume,
            "sound_volume,%d" % self.sound_volume,
            "speedrun_mode,%d" % (1 if self.speedrun_mode else 0),
            "speed_units,%s" % self.speed_units,
            "assist_pan_ahead,%.3f" % self.assist_pan_ahead,
        ]


@dataclass
class Records:
    scores: List[ScoreRow] = field(default_factory=list)
    options: Options = field(default_factory=Options)
    unlocked_stage: int = 1
    last_stage: int = 1
    player_name: str = DEFAULT_NAME

    # ------------------------------------------------------------------- io
    @classmethod
    def load(cls) -> "Records":
        rec = cls()
        path = records_path()
        if not os.path.exists(path):
            return rec
        section = ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        section = line[1:-1].lower()
                        continue
                    if section == "score":
                        parts = line.split(",")
                        if len(parts) >= 3:
                            try:
                                rec.scores.append(
                                    ScoreRow(parts[0], int(parts[1]), int(parts[2]))
                                )
                            except ValueError:
                                continue
                    elif section == "options":
                        k, _, v = line.partition(",")
                        rec._apply_option(k.strip(), v.strip())
                    elif section == "progress":
                        k, _, v = line.partition(",")
                        if k.strip() == "unlocked_stage":
                            rec.unlocked_stage = max(1, _to_int(v, 1))
                        elif k.strip() == "last_stage":
                            rec.last_stage = max(1, _to_int(v, 1))
                        elif k.strip() == "player_name":
                            rec.player_name = v.strip() or DEFAULT_NAME
        except OSError:
            pass
        rec.scores.sort(key=lambda r: r.score, reverse=True)
        rec.scores = rec.scores[:10]
        return rec

    def _apply_option(self, key: str, value: str) -> None:
        o = self.options
        mapping = {
            # legacy booleans: '1' meant "the normal speech experience",
            # which is now auto-detection (a blind player's reader is always
            # running, so auto resolves to on for them); '0' meant off.
            "speech": lambda v: setattr(
                o, "speech", {"1": "auto", "0": "off"}.get(
                    v.strip(), v.strip() if v.strip() in ("auto", "on", "off") else "auto"
                )
            ),
            "sound": lambda v: setattr(o, "sound", v == "1"),
            "music": lambda v: setattr(o, "music", v == "1"),
            "speech_engine": lambda v: setattr(
                # "tones" configs predate the built-in tone announcer's
                # removal; they snap onto the default engine
                o, "speech_engine", v if v in ("prism", "ao2") else "prism"
            ),
            "theme_mode": lambda v: setattr(
                o, "theme_mode", v if v in ("auto", "dark", "light") else "auto"
            ),
            "speech_output": lambda v: setattr(o, "speech_output", v.strip() or "auto"),
            "speech_voice": lambda v: setattr(o, "speech_voice", v.strip()),
            "speech_rate": lambda v: setattr(o, "speech_rate", _clamp(_to_int(v, 50), 0, 100)),
            "speech_volume": lambda v: setattr(o, "speech_volume", _clamp(_to_int(v, 100), 0, 100)),
            "speech_pitch": lambda v: setattr(o, "speech_pitch", _clamp(_to_int(v, 50), 0, 100)),
            "speech_braille": lambda v: setattr(o, "speech_braille", v == "1"),
            "voice": lambda v: setattr(o, "voice", v if v in ("male", "female") else "male"),
            "music_volume": lambda v: setattr(o, "music_volume", _clamp(_to_int(v, 50), 0, 100)),
            "sound_volume": lambda v: setattr(o, "sound_volume", _clamp(_to_int(v, 80), 0, 100)),
            "speedrun_mode": lambda v: setattr(o, "speedrun_mode", v == "1"),
            "speed_units": lambda v: setattr(o, "speed_units", v if v in ("mph", "kmh") else "mph"),
            "assist_pan_ahead": lambda v: setattr(
                o, "assist_pan_ahead", _clamp(_to_float(v, 0.12), 0.0, 0.4)
            ),
        }
        fn = mapping.get(key)
        if fn:
            try:
                fn(value)
            except Exception:
                pass

    def save(self) -> None:
        d = records_dir()
        try:
            os.makedirs(d, exist_ok=True)
            with open(records_path(), "w", encoding="utf-8") as fh:
                fh.write("[score]\n")
                for row in self.scores[:10]:
                    fh.write(row.as_line() + "\n")
                fh.write("\n[options]\n")
                for line in self.options.as_lines():
                    fh.write(line + "\n")
                fh.write("\n[progress]\n")
                fh.write("unlocked_stage,%d\n" % self.unlocked_stage)
                fh.write("last_stage,%d\n" % self.last_stage)
                fh.write("player_name,%s\n" % self.player_name.replace(",", " "))
        except OSError:
            pass

    # ---------------------------------------------------------------- score
    def add_score(self, name: str, score: int, stage: int = 1) -> Optional[int]:
        """Add a score; return its 1-based rank if it made the table."""
        row = ScoreRow(name or DEFAULT_NAME, int(score), int(stage))
        self.scores.append(row)
        self.scores.sort(key=lambda r: r.score, reverse=True)
        self.scores = self.scores[:10]
        try:
            return self.scores.index(row) + 1
        except ValueError:
            return None

    def qualifies(self, score: int) -> bool:
        if len(self.scores) < 10:
            return True
        return score > self.scores[-1].score


def _to_int(v: str, default: int) -> int:
    try:
        return int(v.strip())
    except (ValueError, AttributeError):
        return default


def _to_float(v: str, default: float) -> float:
    try:
        return float(v.strip())
    except (ValueError, AttributeError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
