"""Waterslide Fusion - accessible Windows port of the 2009 iOS classic.

Original game by Marten Palu / FaunaCult, published as Waterslide Extreme
by Connect2Media (previously on BREW phones, per config.cfg).  This port
re-uses the original level files, sounds and menu strings from the .ipa
and rebuilds the gameplay for keyboard play with a fully self-voicing,
audio-first interface for blind players.

Run:   python -m waterslide            (or: python run_game.py)
Setup: python -m waterslide.ipa_extract --ipa "IPA/Waterslide Extreme (iOS).ipa"

Controls (in game): left/right or A/D steer, down/S brake, space boost,
P progress, C score, I diamonds, L lives, R radar, E scan, M cycle audio,
Escape pause.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional

from .audio import AudioEngine
from .game import PlayState, read_steering
from .records import DEFAULT_NAME, Options, Records
from .speech import Speech
from .theme import Theme, get_theme
from .track import Track, load_all_tracks
from .tutorial import Tutorial

WINDOW_SIZE = (720, 480)
CAPTION = "Waterslide Fusion"

SPEED_UNIT_NAMES = {"mph": "miles per hour", "kmh": "kilometres per hour"}
SPEECH_MODE_NAMES = {"auto": "auto detect", "on": "always on", "off": "off"}

HELP_TEXT = (
    "Steer your way down the waterslide, winning points by collecting items as "
    "you go. Watch out for obstacles that will slow you down. There are nine "
    "stages in total. Can you complete them all?"
)

STAGE_NAMES = [
    "Stage 1, City Splash",
    "Stage 2, Down Town",
    "Stage 3, Sky Scraper",
    "Stage 4, Neon Rapids",
    "Stage 5, Twilight Chute",
    "Stage 6, Duck Pond",
    "Stage 7, Crab Alley",
    "Stage 8, Midnight Slide",
    "Stage 9, Hydro Thunder",
]


class MenuItem:
    def __init__(self, label: str, action) -> None:
        self.label = label
        self.action = action


class App:
    """Screen-flow + glue.  Screens are small state machines on self.screen."""

    def __init__(self, headless: bool = False, music: Optional[bool] = None) -> None:
        self.headless = headless
        self.records = Records.load()
        self.options: Options = self.records.options
        if music is not None:
            self.options.music = music
        self.tracks: List[Track] = []
        self.screen = "loading"
        self.running = True
        self.audio = AudioEngine(self)
        self.audio.enabled = self.options.sound
        self.audio.music_volume = self.options.music_volume
        self.audio.sound_volume = self.options.sound_volume
        self._audio_check = self.audio.self_check()
        self.speech = Speech(self)
        self.speech.audio_mode = self.audio.mode
        self.speech.audio_detail = self._audio_check
        # speech.enabled was resolved inside Speech.__init__ from the
        # speech mode (auto detection or explicit on/off)
        self.play: Optional[PlayState] = None
        self.tutorial: Optional[Tutorial] = None
        self.renderer = None
        # The 3D window is always on: visual impairment is a spectrum, and
        # a low-vision screen-reader user still benefits from seeing the
        # slide.  --no-3d turns it off for one session; V toggles it live.
        self.use_3d = "--no-3d" not in sys.argv
        self.menu_index = 0
        self.menu_items: List[MenuItem] = []
        self.stage_index = 0
        self.option_index = 0
        self._chooser_lines: List[str] = []  # last adjusted choice list (visual mirror)
        self._keys = set()
        self.last_stage_result: str = ""
        self.gameover_index = 0
        # Modal dialog (results / death): blocks gameplay input until the
        # player presses Enter or clicks, so speech can never run ahead of
        # the acknowledgement.
        self.dialog: Optional[dict] = None
        # OS-aware palette: dark mode follows the OS, high contrast stands
        # down, and the user's theme option can override the OS.  A shared
        # watcher thread re-resolves it once a second.
        self.theme = get_theme()
        self.theme.set_override(getattr(self.options, "theme_mode", "auto"))

    # ------------------------------------------------------------ lifecycle

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.headless:
            import pygame

            if self.use_3d and "--3d" in sys.argv:
                # An explicit --3d needs a real (accelerated) GL context; a
                # forced dummy driver would produce a dead window.  When 3D
                # was only auto-detected (tests, CI) a preset dummy driver
                # is honoured instead.
                os.environ.pop("SDL_VIDEODRIVER", None)
            pygame.init()
            try:
                pygame.display.set_caption(CAPTION)
                flags = pygame.OPENGL | pygame.DOUBLEBUF if self.use_3d else 0
                self._surface = pygame.display.set_mode(WINDOW_SIZE, flags)
            except Exception:
                self._surface = None
        self.tracks = load_all_tracks()
        if self.use_3d and not self.headless:
            self._init_renderer()
        if not self.headless:
            self.audio.announce_self_check()
            self.speech.say("Welcome to Waterslide Fusion. " + self.speech.describe_mode(),
                            urgent=True)
        self.show_main_menu()

    def _init_renderer(self) -> None:
        """Create the 3D renderer; on failure stay in 2D silently."""
        try:
            from .render3d import Renderer3D

            self.renderer = None  # rebuilt per-stage (needs a track)
            self._renderer3d_cls = Renderer3D
        except Exception:
            self.renderer = None
            self.use_3d = False

    def quit(self) -> None:
        self.running = False
        try:
            self.audio.quit()
            self.speech.quit()
            # the theme is process-wide: its 1 Hz daemon watcher outlives
            # this App on purpose (another App may take over)
        except Exception:
            pass
        # NOTE: pygame.quit() deliberately does NOT happen here.  quit() is
        # called from inside the event loop (e.g. Escape on the main menu);
        # tearing the display down mid-frame made draw() raise "Surface is
        # not initialized".  The run loop owns pygame teardown.

    # ---------------------------------------------------------------- menus
    def show_main_menu(self) -> None:
        self.screen = "main"
        self.dialog = None
        self.menu_index = 0
        items = [("play", self.menu_play), ("tutorial", self.menu_tutorial),
                 ("instructions", self.menu_help),
                 ("top scores", self.menu_scores), ("player name", self.menu_player_name),
                 ("options", self.menu_options),
                 ("quit", self.menu_quit)]
        self.menu_items = [MenuItem(lbl, act) for lbl, act in items]
        self._announce_menu("Main menu.")

    def _announce_menu(self, intro: str = "") -> None:
        if not self.menu_items:
            return
        item = self.menu_items[self.menu_index]
        text = (intro + " " if intro else "") + (
            f"{item.label}, {self.menu_index + 1} of {len(self.menu_items)}."
        )
        self.speech.shut_up(drop_pending=True)
        self.speech.say(text, urgent=False)
        self.audio.ui_select()

    def menu_move(self, delta: int) -> None:
        if not self.menu_items:
            return
        self.menu_index = (self.menu_index + delta) % len(self.menu_items)
        self._announce_menu()

    def menu_select(self) -> None:
        if not self.menu_items:
            return
        self.audio.ui_select()
        self.menu_items[self.menu_index].action()

    def menu_play(self) -> None:
        self.screen = "stages"
        self.stage_index = min(self.records.unlocked_stage, len(self.tracks)) - 1
        self._announce_stage()

    def _announce_stage(self) -> None:
        idx = self.stage_index
        locked = (idx + 1) > self.records.unlocked_stage
        best = self._best_for(idx + 1)
        extra = f" Best score {best}." if best else ""
        status = "locked. " if locked else ""
        self.speech.shut_up(drop_pending=True)
        self.speech.say(f"{STAGE_NAMES[idx]}, {idx + 1} of {len(self.tracks)}. {status}{extra}", urgent=False)

    def _best_for(self, stage: int) -> Optional[int]:
        rows = [r.score for r in self.records.scores if r.stage == stage]
        return max(rows) if rows else None

    def menu_tutorial(self) -> None:
        if not self.tracks:
            self.speech.say("No tracks loaded - run the asset extraction first.", urgent=True)
            return
        self.tutorial = Tutorial(self, self.tracks[0])
        self.screen = "tutorial"
        self.tutorial.update(0.0, 0.0, False)  # triggers briefing -> start

    def tutorial_done(self) -> None:
        self.tutorial = None
        self.show_main_menu()

    def menu_help(self) -> None:
        self.screen = "help"
        self.speech.say(HELP_TEXT + " " + self._controls_text(), urgent=True)

    def _controls_text(self) -> str:
        return ("Controls: left and right arrow to steer, down to brake, space for boost, "
                "P for progress, C for score, I for diamonds, L for lives, "
                "R for radar, E to scan ahead, V for the 3D view, shift V for speech on or off, "
                "M to cycle the speech mode, "
                "page up and page down for music volume, shift with those for sound "
                "effects volume, escape to pause.")

    def menu_scores(self) -> None:
        self.screen = "scores"
        if not self.records.scores:
            self.speech.say("No high scores yet. Go set one!", urgent=True)
            return
        lines = []
        for i, row in enumerate(self.records.scores[:5], 1):
            lines.append(f"{i}. {row.name}, {row.score}, stage {row.stage}.")
        self.speech.shut_up(drop_pending=True)
        self.speech.say("Top scores. " + " ".join(lines), urgent=False)
        if self.records.player_name == DEFAULT_NAME:
            self.speech.say(
                "Set your name with the player name item on the main menu.",
                urgent=False,
            )

    def menu_options(self) -> None:
        self.screen = "options"
        self.option_index = 0
        self._chooser_lines = []
        self._announce_option()

    # ------------------------------------------------------------ name entry
    def menu_player_name(self) -> None:
        """Name entry screen: high scores are recorded under this name."""
        self.screen = "nameentry"
        self._name_buffer = []
        self.speech.shut_up(drop_pending=True)
        self.speech.say(
            f"Player name. The current name is {self.records.player_name}. "
            "Type the new name, then press enter. Backspace deletes. "
            "Escape cancels.",
            urgent=True,
        )

    def _name_commit(self) -> None:
        name = "".join(self._name_buffer).strip()
        if name:
            self.records.player_name = name
            self.records.save()
            self.speech.say(f"Player name {name}.", urgent=True)
        else:
            self.speech.say("No name entered. Name kept.", urgent=True)
        self.show_main_menu()

    def _nameentry_key(self, key: int, unicode: str) -> None:
        """Typing on the name screen: letters append and are spoken."""
        if key in (13, 10):  # enter: accept
            self._name_commit()
            return
        if key == 27:  # escape: cancel, keep the old name
            self.show_main_menu()
            return
        if key in (8, 259):  # backspace (or keypad equivalent)
            if self._name_buffer:
                ch = self._name_buffer.pop()
                self.speech.shut_up(drop_pending=True)
                self.speech.say(f"{ch} deleted.", urgent=True)
            else:
                self.speech.say("Nothing to delete.", urgent=True)
            return
        ch = unicode if unicode and unicode.isprintable() and unicode.strip() else ""
        if ch and len(self._name_buffer) < 24:
            self._name_buffer.append(ch)
            self.speech.shut_up(drop_pending=True)
            # the unicode char already carries shift state, so it speaks
            # as typed (upper or lower) - no key-mod query needed
            self.speech.say(ch, urgent=True)

    MULTI_CHOICE = frozenset({
        "speech", "voice", "speed units", "speech engine", "speech output",
        "speech voice", "speech rate", "speech volume", "speech pitch",
        "music volume", "sound volume", "theme",
    })

    def _option_rows(self) -> List[tuple]:
        o = self.options
        eng_desc = {
            "prism": "Prism, Windows 10 and later",
            "ao2": "accessible output 2, older Windows",
        }
        return [
            ("speech", f"speech mode, {SPEECH_MODE_NAMES.get(o.speech, o.speech)}."),
            ("theme", f"theme, {self._theme_row_value()}."),
            ("sound effects", f"sound effects, {on_off(o.sound)}"),
            ("music", f"music, {on_off(o.music)}"),
            ("music volume", f"music volume, {o.music_volume} percent"),
            ("sound volume", f"sound effects volume, {o.sound_volume} percent"),
            ("voice", f"voice, {o.voice}."),
            ("speed units", f"speed units, {SPEED_UNIT_NAMES.get(o.speed_units, o.speed_units)}."),
            ("speech engine", f"speech engine, {eng_desc.get(o.speech_engine, o.speech_engine)}."),
            ("speech output", f"speech output, {o.speech_output}."),
            ("speech voice", f"speech voice, {o.speech_voice or 'engine default'}."),
            ("speech rate", f"speech rate, {o.speech_rate} percent."),
            ("speech volume", f"speech volume, {o.speech_volume} percent."),
            ("speech pitch", f"speech pitch, {o.speech_pitch} percent."),
            ("speech braille", f"braille mirror, {on_off(o.speech_braille)}"),
            ("speedrun mode", f"speedrun mode, {on_off(o.speedrun_mode)}"),
            ("reset high scores", "reset high scores"),
            ("back", "back"),
        ]

    def _theme_row_value(self) -> str:
        o = self.options
        mode = getattr(o, "theme_mode", "auto")
        if mode == "auto":
            os_scheme = self.theme.scheme
            label = {"dark": "dark", "light": "light", "hc": "high contrast"}.get(os_scheme, os_scheme)
            return f"auto, following the operating system's {label} mode"
        return mode

    def _announce_option(self) -> None:
        rows = self._option_rows()
        label, text = rows[self.option_index]
        if label in self.MULTI_CHOICE:
            hint = "Left and right to adjust. "
        else:
            hint = "Enter to change. "
        self.speech.shut_up(drop_pending=True)
        self.speech.say(f"{text}, {self.option_index + 1} of {len(rows)}. {hint}", urgent=False)

    # ------------------------------------------------ speech settings menu
    def _speech_outputs(self) -> List[str]:
        info = self.speech.probe()
        outs = info.get("outputs") or []
        return ["auto"] + [o for o in outs if o != "auto"]

    def _speech_voices(self) -> List[str]:
        info = self.speech.probe()
        return info.get("voices") or []

    def _after_speech_change(self, confirm: str) -> None:
        self.records.save()
        self.speech.say(confirm, urgent=True)
        self._announce_option()

    def option_toggle(self) -> None:
        key = self._option_rows()[self.option_index][0]
        if key in self.MULTI_CHOICE:
            # list options open their chooser instead of cycling blind
            self.option_adjust(1)
            return
        o = self.options
        if key == "sound effects":
            o.sound = not o.sound
            self.audio.enabled = o.sound
            self.speech.say(f"Sound effects {on_off(o.sound)}.", urgent=True)
        elif key == "music":
            o.music = not o.music
            self.speech.say(f"Music {on_off(o.music)}. "
                            "The theme plays while a stage is in progress.", urgent=True)
        elif key == "music volume":
            # slider: Enter plays the current level as feedback
            self.audio.set_music_volume(o.music_volume)
            self.speech.say(f"Music volume {o.music_volume} percent. "
                            "Left and right to adjust; page up and page down "
                            "work any time.", urgent=True)
            self._announce_option()
            return
        elif key == "sound volume":
            # slider: Enter plays a sample so the level can be judged by ear
            self.audio.sound_volume = o.sound_volume
            self.records.save()
            self.audio.play("diamond", 0.0, 1.0)
            self.speech.say(f"Sound effects volume {o.sound_volume} percent. "
                            "Left and right to adjust; shift with page up and "
                            "page down works any time.", urgent=True)
            self._announce_option()
            return
        elif key == "speech braille":
            o.speech_braille = not o.speech_braille
            self.speech.configure(braille=o.speech_braille)
            self._after_speech_change(f"Braille mirror {on_off(o.speech_braille)}.")
            return
        elif key == "speedrun mode":
            o.speedrun_mode = not o.speedrun_mode
            self.speech.say(
                "Speedrun mode on. Obstacles cost points and slow you down hard." if o.speedrun_mode
                else "Practice mode. Obstacles are gentle and cost no points.",
                urgent=True,
            )
        elif key == "reset high scores":
            self.records.scores = []
            self.records.save()
            self.speech.say("High scores cleared.", urgent=True)
        elif key == "back":
            self.records.save()
            self.show_main_menu()
            return
        self.records.save()
        self._announce_option()

    def _option_choices(self, key: str) -> List:
        """The stable, canonical value list of a multi-choice option.

        The list must not depend on the current value: building it as
        'current first' made right-arrow land on the same 'next' entry
        every time (ping-pong between two values).  Fixed orderings give
        a real cycle through every choice; if the stored value is not in
        the list (stale config), stepping snaps it in at the top.
        """
        o = self.options
        if key == "theme":
            return ["auto", "dark", "light"]
        if key == "speech":
            return ["auto", "on", "off"]
        if key == "voice":
            return ["male", "female"]
        if key == "speed units":
            return ["mph", "kmh"]
        if key == "speech engine":
            if o.speech_engine not in ("prism", "ao2"):
                # configs from releases that still had a built-in tone
                # announcer: snap the stale value onto a real engine
                o.speech_engine = "prism"
                self.records.save()
            return ["prism", "ao2"]
        if key == "speech output":
            outs = [x for x in (self._speech_outputs() or ["auto"]) if x != "auto"]
            return ["auto"] + sorted(outs)
        if key == "speech voice":
            voices = [v for v in self._speech_voices() if v]
            # keep original case: the stored value must be found by index
            return ([""] + sorted(voices)) if voices else [""]
        if key == "speech rate":
            return [0, 25, 50, 75, 100]
        if key == "speech volume":
            return [0, 25, 50, 75, 100]
        if key == "speech pitch":
            return [0, 25, 50, 75, 100]
        if key == "music volume":
            return list(range(0, 101, 5))
        if key == "sound volume":
            return list(range(0, 101, 5))
        return []

    def _option_apply(self, key: str, value) -> str:
        """Store a chosen value for a multi-choice option; returns a spoken label."""
        o = self.options
        if key == "speech":
            self._apply_speech_mode(value)
            self.records.save()
            return {
                "auto": "Speech auto. A running screen reader gets speech; "
                        "otherwise the 3D view with no speech.",
                "on": "Speech on.",
                "off": "Speech off.",
            }.get(value, f"Speech mode {value}.")
        if key == "voice":
            o.voice = value
            self.records.save()
            return f"Voice {value}. Falls, crashes and loop-the-loop screams use this voice."
        if key == "speed units":
            o.speed_units = value
            self.records.save()
            return f"Speed measured in {SPEED_UNIT_NAMES[value]}."
        if key == "speech engine":
            o.speech_engine = value
            self.speech.configure(engine=value, rebuild=True)
            # the rebuilt engine has its own outputs and voices: drop
            # now-invalid stored choices so the rows show the new truth
            outs = self._speech_outputs()
            if o.speech_output != "auto" and o.speech_output not in outs:
                o.speech_output = "auto"
            voices = [v for v in self._speech_voices() if v]
            if o.speech_voice and o.speech_voice not in voices:
                o.speech_voice = ""
            self.records.save()
            return {
                "prism": "Prism engine. Windows 10 and later.",
                "ao2": "Accessible output 2 engine, for older Windows.",
            }.get(value, f"Engine {value}.")
        if key == "theme":
            o.theme_mode = value
            self.theme.set_override(value)
            self.records.save()
            return {
                "auto": "Theme auto. Dark mode and high contrast follow the operating system.",
                "dark": "Theme dark.",
                "light": "Theme light.",
            }.get(value, f"Theme {value}.")
        if key == "speech output":
            o.speech_output = value
            self.speech.configure(output=value, rebuild=True)
            voices = [v for v in self._speech_voices() if v]
            if o.speech_voice and o.speech_voice not in voices:
                o.speech_voice = ""
            self.records.save()
            return f"Speech output {value}."
        if key == "speech voice":
            o.speech_voice = value
            self.speech.configure(voice=value)
            self.records.save()
            return f"Voice {value or 'engine default'}."
        if key == "speech rate":
            o.speech_rate = int(value)
            self.speech.configure(rate=int(value))
            self.records.save()
            return f"Speech rate {int(value)} percent."
        if key == "speech volume":
            o.speech_volume = int(value)
            self.speech.configure(volume=int(value))
            self.records.save()
            return f"Speech volume {int(value)} percent."
        if key == "speech pitch":
            o.speech_pitch = int(value)
            self.speech.configure(pitch=int(value))
            self.records.save()
            return f"Speech pitch {int(value)} percent."
        if key == "music volume":
            o.music_volume = int(value)
            self.audio.set_music_volume(o.music_volume)
            self.records.save()
            return f"Music volume {int(value)} percent."
        if key == "sound volume":
            o.sound_volume = int(value)
            self.audio.sound_volume = o.sound_volume
            self.audio.play("diamond", 0.0, 1.0)
            self.records.save()
            return f"Sound effects volume {int(value)} percent."
        return ""

    @staticmethod
    def _choice_label(key: str, value) -> str:
        """Spoken/rendered text for one entry of a multi-choice list."""
        if key in ("speech rate", "speech volume", "speech pitch",
                   "music volume", "sound volume"):
            return f"{value} percent"
        if key == "speech voice":
            return value or "engine default"
        if key == "speech engine":
            return {"prism": "Prism", "ao2": "accessible output 2"}.get(value, str(value))
        if key == "speech":
            return SPEECH_MODE_NAMES.get(value, str(value))
        if key == "speed units":
            return {"mph": "miles per hour", "kmh": "kilometres per hour"}.get(value, str(value))
        if key == "theme":
            return {"auto": "auto", "dark": "dark", "light": "light"}.get(value, str(value))
        return str(value)

    def option_adjust(self, direction: int) -> None:
        """Left/right on a multi-choice row: step through its choice list.

        Each step confirms the new value with its position (as in 'four of
        five') instead of Enter-cycling blind through the values.
        """
        key = self._option_rows()[self.option_index][0]
        choices = self._option_choices(key)
        if len(choices) < 2:
            self.speech.say("No other choices available.", urgent=True)
            return
        attr = {"speech": "speech", "voice": "voice", "speed units": "speed_units",
                "speech engine": "speech_engine",
                "speech output": "speech_output", "speech voice": "speech_voice",
                "speech rate": "speech_rate", "speech volume": "speech_volume",
                "speech pitch": "speech_pitch", "music volume": "music_volume",
                "sound volume": "sound_volume", "theme": "theme_mode"}[key]
        try:
            idx = choices.index(getattr(self.options, attr))
        except ValueError:
            # stale/off-ladder value (e.g. volume 57 on a 5-step ladder):
            # snap to the nearest step rather than jumping to the top
            if choices and all(isinstance(c, int) for c in choices):
                idx = min(range(len(choices)),
                          key=lambda i: abs(choices[i] - (getattr(self.options, attr) or 0)))
            else:
                idx = 0
        value = choices[(idx + direction) % len(choices)]
        confirm = self._option_apply(key, value)
        pos = choices.index(value) + 1
        # an engine/output change rebuilds the engine: re-probe so the list
        # shows the new engine's outputs/voices, not the previous engine's
        if key in ("speech engine", "speech output"):
            choices = self._option_choices(key)
            try:
                pos = choices.index(getattr(self.options, attr)) + 1
            except ValueError:
                pos = 1
        self.speech.shut_up(drop_pending=True)
        self.speech.say(f"{confirm} {pos} of {len(choices)}.", urgent=False)
        self._chooser_lines = [key] + [
            ("* " if i == pos - 1 else "  ") + self._choice_label(key, v)
            for i, v in enumerate(choices)
        ]

    def menu_quit(self) -> None:
        self.speech.say("Thanks for playing!", urgent=True)
        self.records.save()
        self.quit()

    # --------------------------------------------------------------- stages
    def stage_move(self, delta: int) -> None:
        self.stage_index = (self.stage_index + delta) % len(self.tracks)
        self._announce_stage()

    def stage_select(self) -> None:
        if (self.stage_index + 1) > self.records.unlocked_stage:
            self.speech.say("This stage is locked. Finish the previous stage first.", urgent=True)
            self.speech.beep([(220, 140, "square")])
            return
        self.start_stage(self.stage_index)

    def start_stage(self, index: int) -> None:
        track = self.tracks[index]
        self.play = PlayState(self, track, index + 1)
        self.screen = "game"
        # a fresh attempt gets fresh sound: without this, a restart from
        # the pause menu would merely unpause the suspended theme, and
        # leftover bed suspension would mute the new ambience
        self.audio.stop_music()
        self.audio.stop_ambience()
        self.dialog = None  # entering a stage always clears any dialog
        if self.use_3d and getattr(self, "_renderer3d_cls", None):
            try:
                self.renderer = self._renderer3d_cls(self._surface, track)
            except Exception:
                # GL not available: drop back to the plain 2D window
                self.renderer = None
                self.use_3d = False
                try:
                    import pygame

                    self._surface = pygame.display.set_mode(WINDOW_SIZE)
                except Exception:
                    pass
        self.records.last_stage = index + 1
        self.records.save()
        self.speech.say(f"{STAGE_NAMES[index]}, {len(track.items)} items on the slide. Get ready!", urgent=True)
        self.audio.start_ambience()

    def finish_stage(self, play: PlayState) -> None:
        self.audio.stop_ambience()
        rank = self.records.add_score(self.records.player_name, play.score, play.stage_number)
        unlocked_msg = ""
        if play.man.finished:
            if play.stage_number >= self.records.unlocked_stage and self.records.unlocked_stage < 9:
                self.records.unlocked_stage = play.stage_number + 1
                unlocked_msg = f" Stage {play.stage_number + 1} unlocked!"
        self.records.save()
        rank_msg = f" New high score, rank {rank}!" if rank else ""
        self.screen = "gameover"
        self.last_stage_result = "win" if play.man.finished else "fall"
        self.gameover_index = 0
        # Modal results dialog: nothing else speaks until the player
        # acknowledges it with Enter or a click.
        self.show_dialog(self.results_text() + unlocked_msg + rank_msg,
                         result="win" if play.man.finished else "fall")

    def show_dialog(self, text: str, result: str = "") -> None:
        """Open the modal results/death dialog (Enter or click dismisses)."""
        self.dialog = {
            "text": text,
            "result": result,
        }
        self.speech.shut_up(drop_pending=True)
        self.speech.say(text, urgent=False)
        self.speech.say("Press Enter or click to continue.", urgent=False)

    def _dialog_lines(self) -> List[str]:
        """Wrap the modal dialog text for the visual mirror."""
        words = (self.dialog["text"] if self.dialog else "").split()
        lines: List[str] = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > 60:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
        return lines

    def dismiss_dialog(self) -> None:
        """Close the modal dialog (Enter or click)."""
        self.dialog = None
        if self.screen == "gameover":
            self._announce_gameover()
        else:
            # mid-stage death dialog: respawn with the remaining lives;
            # the music policy brings the theme back with the stage
            self.speech.say("Continuing.", urgent=False)
            self.audio.start_ambience()

    def _announce_gameover(self) -> None:
        opts = self._gameover_options()
        self.gameover_index %= len(opts)
        label = opts[self.gameover_index][0]
        self.speech.say(f"{label}, {self.gameover_index + 1} of {len(opts)}.", urgent=False)

    def _gameover_options(self) -> List[tuple]:
        """Gameover menu entries; 'next stage' first after a win (if any left)."""
        play = self.play
        won = self.last_stage_result == "win" and play is not None and play.man.finished
        opts: List[tuple] = []
        if won and play is not None and play.stage_number < 9:
            opts.append(("next stage", self._gameover_next_stage))
        opts.append(("replay stage", self._gameover_replay))
        opts.append(("stage select", self.menu_play))
        opts.append(("main menu", self._gameover_main_menu))
        return opts

    def _gameover_next_stage(self) -> None:
        nxt = self.play.stage_number if self.play else 1
        if nxt < 9:
            self.start_stage(nxt)  # index: stage n+1 = index n
        else:
            self.menu_play()

    def _gameover_replay(self) -> None:
        self.start_stage(self.play.stage_number - 1 if self.play else 0)

    def _gameover_main_menu(self) -> None:
        self.audio.stop_ambience()
        self.show_main_menu()

    def results_text(self) -> str:
        """Full spoken results dialog for the finished stage."""
        play = self.play
        if play is None:
            return "Run over."
        if play.man.finished:
            intro = f"Stage {play.stage_number} complete!"
        else:
            intro = f"Stage {play.stage_number} over."
        return (
            f"{intro} Time {play.elapsed:.1f} seconds, "
            f"{play.rings_got} of {play.rings_total} diamonds, "
            f"{play.lives} {'life' if play.lives == 1 else 'lives'} remaining, "
            f"final score {play.score}."
        )

    def gameover_move(self, delta: int) -> None:
        self.gameover_index = (self.gameover_index + delta) % len(self._gameover_options())
        self.speech.shut_up(drop_pending=True)
        self._announce_gameover()

    def gameover_select(self) -> None:
        if self.dialog is not None:
            self.dismiss_dialog()
            return
        opts = self._gameover_options()
        self.gameover_index %= len(opts)
        opts[self.gameover_index][1]()

    # ---------------------------------------------------------------- pause
    def enter_pause(self, play: PlayState) -> None:
        self.screen = "pause"
        self.pause_index = 0
        self.speech.say("Paused. Resume, restart, or main menu.", urgent=True)
        # the whole soundscape hushes: music suspends via the policy, the
        # water and wind beds suspend here, and both continue in place on
        # resume (an explicit set_splash(0, 0) would only mute - the beds
        # would keep chewing mixer time and restart semantics get muddy)
        self.audio.pause_ambience()

    def _game_report_key(self, key: int) -> bool:
        """Answer P/C/I/L/R/E (the in-game report keys) outside the race."""
        if self.play is None:
            return False
        map_ = {
            112: self.play.report_progress,
            104: self.play.report_progress,  # H, the original's key
            99: self.play.report_score,
            105: self.play.report_items,
            108: self.play.report_lives,
            114: self.play.report_radar,
            101: self.play.report_scan,
        }
        fn = map_.get(key)
        if fn is None:
            return False
        fn()
        return True

    def pause_move(self, delta: int) -> None:
        self.pause_index = (self.pause_index + delta) % 3
        labels = ["resume", "restart stage", "main menu"]
        self.speech.shut_up(drop_pending=True)
        self.speech.say(labels[self.pause_index], urgent=False)

    def pause_select(self) -> None:
        if self.pause_index == 0:
            self.screen = "game"
            self.audio.resume_ambience()
            self.speech.say("Resumed.", urgent=True)
        elif self.pause_index == 1:
            if self.play:
                self.start_stage(self.play.stage_number - 1)
        else:
            self.audio.stop_ambience()
            self.show_main_menu()

    # ---------------------------------------------------------------- audio
    def _volume_hotkey(self, key: int, mods: int = 0) -> bool:
        """Volume hotkeys.

        PgUp/PgDn = music volume, Shift+PgUp/PgDn = sound effects volume;
        Home/End = music mute/full, Shift+Home/End = effects mute/full.
        """
        shift = bool(mods & 3)  # KMOD_SHIFT
        if key in (1073741899, 1073741902):    # K_PAGEUP / K_PAGEDOWN
            delta = 10 if key == 1073741899 else -10
            self.adjust_sound_volume(delta) if shift else self.adjust_music_volume(delta)
            return True
        if key in (1073741898, 1073741901):    # K_HOME / K_END
            up = key == 1073741898             # Home=full, End=mute; Shift swaps channel
            delta = 100 if up else -100
            self.adjust_sound_volume(delta) if shift else self.adjust_music_volume(delta)
            return True
        return False

    def adjust_music_volume(self, delta: int) -> None:
        o = self.options
        before = o.music_volume
        o.music_volume = max(0, min(100, o.music_volume + delta))
        self.audio.set_music_volume(o.music_volume)
        self.records.save()
        if o.music_volume <= 0 and before > 0:
            self._music_policy()  # mute takes effect at once
            self.speech.say("Music muted. Page up or Home brings it back.", urgent=True)
        elif delta > 0 and not o.music and o.music_volume > 0:
            o.music = True
            self._music_policy()  # the theme only ever plays in a live stage
            self.speech.say(f"Music on, volume {o.music_volume} percent.", urgent=True)
        elif o.music and o.music_volume <= 0:
            pass  # handled above
        else:
            self.speech.say(f"Music volume {o.music_volume} percent.", urgent=True)

    def adjust_sound_volume(self, delta: int) -> None:
        o = self.options
        o.sound_volume = max(0, min(100, o.sound_volume + delta))
        self.audio.sound_volume = o.sound_volume
        self.records.save()
        if o.sound_volume <= 0:
            o.sound = False
            self.audio.enabled = False
            self.speech.say("Sound effects muted. Shift page up or shift Home brings them back.", urgent=True)
        elif delta > 0 and not o.sound and o.sound_volume > 0:
            o.sound = True
            self.audio.enabled = True
            self.audio.play("diamond", 0.0, 0.9)
            self.speech.say(f"Sound effects on, volume {o.sound_volume} percent.", urgent=True)
        else:
            if o.sound_volume > 0 and o.sound:
                self.audio.play("diamond", 0.0, 0.9)
            self.speech.say(f"Sound effects volume {o.sound_volume} percent.", urgent=True)

    def _apply_speech_mode(self, mode: str) -> None:
        """Store and activate a speech mode: 'auto', 'on' or 'off'."""
        from .speech import screen_reader_running

        self.options.speech = mode
        self.speech.mode_pref = mode
        if mode == "auto":
            running = screen_reader_running()
            # re-evaluate the deferred-engine state from scratch
            self.speech._defer_engine = not running
            self.speech._force_build = False
            self.speech.enabled = running
        else:
            # an explicit choice always owns the engine
            self.speech._defer_engine = False
            self.speech._force_build = True
            self.speech.enabled = mode == "on"
        # wake the worker: build the engine, or drop it for auto-off
        self.speech.configure(rebuild=True)
        # the 3D view is independent of the speech mode and stays as it is

    def cycle_audio(self) -> None:
        """Cycle the speech mode: auto - on - off - auto."""
        order = ["auto", "on", "off"]
        mode = self.options.speech if self.options.speech in order else "auto"
        nxt = order[(order.index(mode) + 1) % len(order)]
        self._apply_speech_mode(nxt)
        msg = {
            "auto": "Speech auto. A running screen reader gets speech; "
                    "otherwise the game stays silent. The 3D view is unaffected.",
            "on": "Speech on, sound effects on.",
            "off": "Speech off, sound effects on.",
        }.get(nxt, "Speech mode " + nxt + ".")
        self.speech.say(msg, urgent=True)
        self.records.save()

    def _toggle_3d(self) -> None:
        """Turn the 3D view on or off (V key).

        Toggling on recreates the window with an OpenGL surface (the 3D
        renderer needs one); the run loop rebuilds the per-stage renderer
        when ``self.renderer`` is None while the stage is still live.
        """
        if getattr(self, "_surface", None) is None or self.headless:
            self.speech.say("The 3D view needs a window.", urgent=True)
            return
        self.use_3d = not self.use_3d
        try:
            import pygame

            flags = pygame.OPENGL | pygame.DOUBLEBUF if self.use_3d else 0
            self._surface = pygame.display.set_mode(WINDOW_SIZE, flags)
        except Exception:
            self.use_3d = not self.use_3d
            self.speech.say("The 3D view is not available here.", urgent=True)
            return
        self.renderer = None
        msg = "3D view on." if self.use_3d else "3D view off."
        if not self.speech.enabled:
            msg += " Shift V toggles speech."
        self.speech.say(msg, urgent=True)

    def handle_keydown(self, key: int, unicode: str = "", mods: int = 0) -> None:
        self._keys.add(key)
        # V toggles the 3D view.  Shift+V also cycles the speech mode when
        # the window is 3D-first (sighted auto), because a mouseless sighted
        # player has no in-game menu navigation otherwise.
        if key == 118:  # K_V
            if unicode == "V":
                self._apply_speech_mode("off" if self.speech.enabled else "on")
                self.records.save()
                self.speech.say("Speech off. Press shift V to bring it back."
                                if not self.speech.enabled else "Speech on.",
                                urgent=True)
            else:
                self._toggle_3d()
            return
        if self._volume_hotkey(key, mods):
            return
        if self.screen in ("main", "stages", "options"):
            if key in (273, 274, 1073741906, 1073741905):  # up/down
                delta = -1 if key in (273, 1073741906) else 1
                if self.screen == "main":
                    self.menu_move(delta)
                elif self.screen == "stages":
                    self.stage_move(delta)
                else:
                    rows = len(self._option_rows())
                    self.option_index = (self.option_index + delta) % rows
                    self._chooser_lines = []
                    self._announce_option()
                return
            if key in (1073741903, 1073741904) and self.screen == "options":
                # left/right: step through a multi-choice option's list;
                # on single-choice rows they fall back to row navigation
                direction = 1 if key == 1073741903 else -1
                if self._option_rows()[self.option_index][0] in self.MULTI_CHOICE:
                    self.option_adjust(direction)
                else:
                    self.handle_keydown(1073741905 if key == 1073741903 else 1073741906)
                return
            if key in (13, 10, 32, 1073741910):  # enter/space/select
                if self.screen == "main":
                    self.menu_select()
                elif self.screen == "stages":
                    self.stage_select()
                else:
                    self.option_toggle()
                return
            if key == 27:  # escape: back
                if self.screen == "main":
                    self.menu_quit()
                else:
                    self.show_main_menu()
                return
        if self.screen == "help":
            if key in (13, 10, 32, 27):
                self.show_main_menu()
            elif key == 114:  # R repeats
                self.speech.say(HELP_TEXT + " " + self._controls_text(), urgent=True)
            return
        if self.screen == "scores":
            if key in (13, 10, 32, 27):
                self.show_main_menu()
            return
        if self.screen == "nameentry":
            self._nameentry_key(key, unicode)
            return
        if self.dialog is not None:
            # While a dialog is up, everything but its dismiss keys is eaten.
            if key in (13, 10, 32):
                self.dismiss_dialog()
            return
        if self.screen == "gameover":
            if key in (273, 1073741906):
                self.gameover_move(-1)
            elif key in (274, 1073741905):
                self.gameover_move(1)
            elif key in (13, 10, 32):
                self.gameover_select()
            elif key == 114:  # R: repeat the results dialog
                self.speech.shut_up()
                self.speech.say(self.dialog["text"] if self.dialog is not None else self.results_text(), urgent=False)
            elif key == 27:
                self.show_main_menu()
            return
        if self.screen == "pause":
            if self.play is not None and self._game_report_key(key):
                return  # report keys answer from the pause screen too
            if key in (273, 1073741906):
                self.pause_move(-1)
            elif key in (274, 1073741905):
                self.pause_move(1)
            elif key in (13, 10, 32):
                self.pause_select()
            elif key == 27:
                # escape resumes directly: same sound restoration as the
                # menu's Resume (the music policy unpauses the theme on
                # its next tick, but the ambience beds need this explicit
                # resume or they stay suspended until a stage restart)
                self.screen = "game"
                self.audio.resume_ambience()
                self.speech.say("Resumed.", urgent=True)
            return
        if self.screen == "tutorial" and self.tutorial is not None:
            if key == 114:  # R repeats the instruction
                self.tutorial.handle_event(_KeyProxy(key))
            elif key in (104, 112, 116, 27):  # H, P, T, escape
                self.tutorial.handle_event(_KeyProxy(key))
            elif key == 99:  # C: score
                self.tutorial.app.speech.say("Score 0. The tutorial does not score.", urgent=True)
            elif key == 105:  # I: diamonds
                self.tutorial.app.speech.say(
                    "0 of 3 diamonds. The practice items are the only ones here.", urgent=True)
            elif key == 108:  # L: lives
                self.tutorial.app.speech.say(
                    "3 lives in a real stage. The tutorial never takes lives.", urgent=True)
            return
        if self.screen == "game" and self.play is not None:
            self.play.handle_event(_KeyProxy(key))

    def handle_keyup(self, key: int) -> None:
        self._keys.discard(key)

    # ---------------------------------------------------------------- frame
    def _music_policy(self) -> None:
        """Keep the music consistent with the app state, every frame.

        Rule: the theme plays *only* while a stage is actually in progress
        (countdown or racing), and never on menus, dialogs, the results
        screen, or after the stage ends.  Pausing the stage - or muting the
        music volume mid-race - suspends the song in place, and restoring
        it continues exactly where it left off; dying stops it outright
        (the original's behavior, applied in PlayState.apply_fall) and the
        policy brings it back on respawn.
        """
        in_live_stage = (
            self.screen in ("game", "pause")
            and self.play is not None
            and self.dialog is None
            # a death is being sequenced: the cry plays, THEN the dialog
            # appears - the theme must stay out of both
            and getattr(self.play, "_dialog_text", None) is None
            and self.play.phase in ("countdown", "racing")
        )
        want = (
            self.options.music
            and self.options.music_volume > 0
            and self.screen == "game"
            and in_live_stage
        )
        if want:
            self.audio.play_music()   # idempotent; continues a suspended song
        elif in_live_stage:
            # Stage is live but the music is not wanted right now (paused,
            # or volume muted): suspend the song in place so restoring it
            # continues instead of restarting.  Nothing else may stop the
            # channel here - that is the whole point.
            self.audio.pause_music()
        else:
            self.audio.stop_music()

    def update(self, dt: float) -> None:
        self._music_policy()
        if self.dialog is not None:
            # A modal dialog freezes everything, including gameplay: the
            # stage does not resume until it is dismissed.
            return
        if (self.use_3d and self.renderer is None and self.screen == "game"
                and getattr(self, "play", None) and getattr(self, "_renderer3d_cls", None)
                and getattr(self, "_surface", None) is not None):
            # V recreated the window with a GL surface mid-race: rebuild
            # the per-stage renderer on the next frame.
            try:
                self.renderer = self._renderer3d_cls(self._surface, self.play.track)
            except Exception:
                self.renderer = None
        if self.screen == "tutorial" and self.tutorial is not None:
            steer, brake = read_steering(_KeyState(self._keys))
            self.tutorial.update(dt, steer, brake)
            return
        if self.screen == "game" and self.play is not None:
            if self.play.phase == "finished":
                return
            if self.play.phase == "countdown":
                self.play.update(dt, 0.0, False)
                return
            steer, brake = self.play.steering_input(_KeyState(self._keys))
            if self.headless:
                steer, brake = self._headless_steer(steer, brake)
            self.play.update(dt, steer, brake)
            if self.play.man.finished:
                self.finish_stage(self.play)

    def _headless_steer(self, steer: float, brake: bool):
        """Simple demo autopilot for self-tests: steer toward next item."""
        from .game import angle_diff_signed

        play = self.play
        nxt = play._next_item()
        if nxt is None:
            return steer, brake
        diff = angle_diff_signed(nxt.angle, play.man.angle)
        # gentle clamp: the demo pilot never rides past the fall angle
        return (max(-0.85, min(0.85, diff * 3.0))), brake

    # ----------------------------------------------------------------- draw
    def draw(self, surface) -> None:
        """Minimal visual mirror of the audio UI (sighted co-op players)."""
        import pygame

        surface.fill(self.theme.bg)
        font = None
        try:
            font = pygame.font.Font(None, 30)
        except Exception:
            return
        lines: List[str] = []
        if self.screen == "main":
            lines = ["Waterslide Fusion - the 2009 classic, accessible edition", ""]
            lines += [("> " if i == self.menu_index else "  ") + m.label for i, m in enumerate(self.menu_items)]
        elif self.screen == "stages":
            lines = ["Choose your stage", ""]
            for i, name in enumerate(STAGE_NAMES):
                lock = "" if (i + 1) <= self.records.unlocked_stage else " (locked)"
                cur = "> " if i == self.stage_index else "  "
                lines.append(cur + name + lock)
        elif self.screen == "nameentry":
            lines = ["Player name", "", "Type a name, enter to accept, escape cancels", "",
                     "".join(self._name_buffer) or "(no name yet)"]
        elif self.screen == "options":
            lines = ["Options", ""]
            for i, (_label, text) in enumerate(self._option_rows()):
                lines.append(("> " if i == self.option_index else "  ") + text)
            if self._chooser_lines:
                lines += [""] + self._chooser_lines
        elif self.screen == "game" and self.play:
            p = self.play
            lines = [
                STAGE_NAMES[p.stage_number - 1],
                f"score {p.score}   lives {p.lives}",
                f"progress {int(p.man.progress() * 100)}%   speed {p.speed_display()[0]} {'mph' if getattr(self.options, 'speed_units', 'mph') == 'mph' else 'km/h'}",
                f"diamonds {p.rings_got}/{p.rings_total}",
            ]
        elif self.screen == "tutorial" and self.tutorial:
            lines = ["Waterslide Fusion - tutorial", self.tutorial.status_line()]
        elif self.screen == "gameover":
            if self.dialog is not None:
                lines = [("STAGE COMPLETE!" if self.last_stage_result == "win" else "SLIDE OVER"), ""]
                lines += self._dialog_lines()
                lines += ["", "Enter or click to continue"]
            else:
                lines = [self.last_stage_result == "win" and "STAGE COMPLETE!" or "SLIDE OVER"]
                lines += [("> " if i == self.gameover_index else "  ") + t
                          for i, t in enumerate([label for label, _ in self._gameover_options()])]
        y = 40
        for line in lines:
            try:
                surf = font.render(str(line), True, self.theme.fg)
                surface.blit(surf, (40, y))
            except Exception:
                pass
            y += 34

    # ----------------------------------------------------------------- loop
    def run(self) -> None:
        import pygame

        self.start()
        clock = pygame.time.Clock()
        try:
            self._run_loop(pygame, clock)
        finally:
            self.records.save()
            if not self.headless:
                try:
                    pygame.quit()
                except Exception:
                    pass

    def _run_loop(self, pygame, clock) -> None:
        while self.running:
            dt = min(clock.tick(60) / 1000.0, 0.05)
            try:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                    elif event.type == pygame.KEYDOWN:
                        self.handle_keydown(
                            event.key, getattr(event, "unicode", ""),
                            getattr(event, "mod", 0),
                        )
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        # A click dismisses the modal dialog (and nothing
                        # else - the game is keyboard driven).
                        if self.dialog is not None:
                            self.dismiss_dialog()
                    elif event.type == pygame.KEYUP:
                        self.handle_keyup(event.key)
                # A quit handler may have torn the display down (or pygame
                # itself may have lost the window): bail out first.
                if not self.running or not pygame.get_init():
                    break
                if self.theme.poll():
                    # the OS theme or high-contrast scheme flipped: the 3D
                    # fog follows automatically next frame; menus repaint
                    self.draw(self._surface)
                self.update(dt)
                if getattr(self, "_surface", None) is not None:
                    if self.renderer is not None and self.screen == "game" and self.play:
                        lines = [
                            (STAGE_NAMES[self.play.stage_number - 1], 40, 30),
                            (f"score {self.play.score}   lives {self.play.lives}   "
                             f"diamonds {self.play.rings_got}/{self.play.rings_total}", 40, 62),
                            (f"progress {int(self.play.man.progress() * 100)}%   "
                             f"speed {self.play.speed_display()[0]} {'mph' if getattr(self.options, 'speed_units', 'mph') == 'mph' else 'km/h'}", 40, 94),
                        ]
                        self.renderer.draw(self.play, lines)
                    else:
                        self.draw(self._surface)
                    pygame.display.flip()
            except pygame.error as exc:
                # Display went away mid-frame (window closed, device lost,
                # video system shut down): exit the loop cleanly instead of
                # crashing the game.
                print(f"display lost, exiting: {exc}", file=sys.stderr)
                self.running = False
        self.records.save()

    # ------------------------------------------------------------------ cli
    @classmethod
    def main(cls, argv: Optional[List[str]] = None) -> int:
        argv = list(sys.argv[1:] if argv is None else argv)
        headless = "--selftest" in argv or "--headless" in argv
        music = True if "--music" in argv else (False if "--no-music" in argv else None)
        app = cls(headless=headless, music=music)
        if "--selftest" in argv:
            return self_test(app)
        app.run()
        return 0


class _KeyProxy:
    """Minimal pygame-like event for headless/testing use."""

    def __init__(self, key: int) -> None:
        self.key = key


class _KeyState:
    def __init__(self, keys: set) -> None:
        self.keys = keys

    def __getitem__(self, k: int) -> bool:
        return k in self.keys


def on_off(v: bool) -> str:
    return "on" if v else "off"


# ---------------------------------------------------------------- self-test
def self_test(app: App) -> int:
    """Headless smoke test: load assets, play a scripted segment."""
    failures: List[str] = []
    try:
        app.start()
        assert app.tracks, "no tracks loaded"
        assert len(app.tracks) == 9, f"expected 9 tracks, got {len(app.tracks)}"
        for i, t in enumerate(app.tracks, 1):
            assert t.total_length > 0
            assert t.items, f"track {i} has no items"
        # short simulated stage
        app.start_stage(0)
        play = app.play
        assert play is not None
        dt = 1 / 60
        for step in range(int(60 * 45)):  # up to 45 s of sim
            app.update(dt)
            if app.screen != "game":
                break
            if app.play is None or app.play.phase == "finished":
                break
        final = play.score
        app.speech.say(f"Self test complete. Score {final}.", urgent=True)
        time.sleep(0.1)
        speech_probe = app.speech.probe()
        print(f"SELFTEST OK tracks={len(app.tracks)} items={len(play.items)} score={final} "
              f"phase={play.phase} audio_mode={app.audio.mode} speech={speech_probe.get('engine')} "
              f"engine={speech_probe.get('name')}")
        build_errors = speech_probe.get("build_errors") or []
        if speech_probe.get("error"):
            build_errors.append(speech_probe["error"])
        if build_errors:
            print("  speech engine errors: " + "; ".join(build_errors))
        # Engine switch: the rebuilt engine must report its own outputs and
        # voices.  A stale probe would keep showing the previous engine's
        # lists - exactly the bug this guards against.
        if not getattr(app.speech, "_defer_engine", False):
            original_engine = app.speech.engine_pref or "prism"
            for alt in ("ao2", "prism"):
                if alt == original_engine:
                    continue
                app.speech.configure(engine=alt, rebuild=True)
                info, switched = {}, False
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    info = app.speech.probe(timeout=1.0)
                    if info.get("engine") == alt:
                        switched = True
                        break
                    time.sleep(0.1)
                if switched:
                    print(f"  engine switch OK: {alt} reports "
                          f"{len(info.get('outputs') or [])} outputs, "
                          f"{len(info.get('voices') or [])} voices")
                else:
                    failures.append(
                        f"engine switch to {alt} did not take effect "
                        f"(probe still reports {info.get('engine')!r})")
            app.speech.configure(engine=original_engine, rebuild=True)
            time.sleep(0.3)
        return 0
    except AssertionError as exc:
        failures.append(str(exc))
        from .paths import assets_root
        from .track import find_level_dir
        level_dir = find_level_dir()
        print("SELFTEST FAIL:", "; ".join(failures))
        print(f"  assets_root={assets_root()} level_dir={level_dir} "
              f"frozen={getattr(sys, 'frozen', False)} "
              f"meipass={getattr(sys, '_MEIPASS', '')} "
              f"cwd={os.getcwd()}")
        try:
            print("  level dir contents:", os.listdir(level_dir)[:12])
        except Exception as list_err:
            print("  level dir unreadable:", list_err)
        return 1
    finally:
        app.quit()
