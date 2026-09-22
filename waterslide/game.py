"""Gameplay: the port of ``GliderMan`` and the play state.

The original player class ``GliderMan`` slid along a ``PRSpline`` track,
steered across the tube wall, collected diamonds/hearts/stars/boosts and
avoided crabs and ducks (which slow you down rather than kill you - "Watch
out for obstacles that'll slow you down", from the original instructions).

This port keeps that model:

* ``dist``  arc-length position along the spline (0 .. total_length)
* ``angle`` lateral position on the tube wall in radians
            (0 = bottom centre, negative = left wall, positive = right)
* ``speed`` units/second, with passive gravity acceleration, braking,
            boost pads and obstacle slowdown.

Items are collected when the player passes their arc-length position
within an angular window; every event is reported with a stereo pan so a
blind player can hear the *layout* of the slide.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .track import (
    CLASS_BOOST,
    CLASS_CRAB,
    CLASS_DIAMOND,
    CLASS_DUCK,
    CLASS_HEART,
    CLASS_STAR,
    Track,
    TrackItem,
)
from .audio import sfx_key

# ---------------------------------------------------------------- physics
MIN_SPEED = 500.0
MAX_SPEED = 1600.0
PASSIVE_ACCEL = 150.0
SLIDE_GRAVITY = 1.4       # slope -> cruise-target shift (engine altitude / dist)
SLOPE_LOOKAHEAD = 60.0
CRUISE_BIAS = 0.60        # fraction of the speed band kept on flat ground
NATURAL_DECEL = 500.0     # momentum: coasting down-speed is gentle
MAX_ACCEL = 1200.0
BRAKE_DECEL = 1800.0
BOOST_SPEED = 2600.0
BOOST_TIME = 3.0
DUCK_SLOWDOWN = 0.55
CRAB_SLOWDOWN = 0.62
MAX_STEER = 1.75          # radians, ~100 deg: full steer rides past the rim
STEER_RATE = 2.4          # rad/s toward target
STEER_RETURN = 3.0        # rad/s back to centre
FALL_ANGLE = 1.66         # radians (~95 deg): the rim of the slide
FALL_GRACE = 0.9          # seconds of riding the edge before you drop
GOD_TIME = 8.0

# ---- collectibility geometry ---------------------------------------------
# The cross-section is a clock face (0 = 12 o'clock / bottom of the tube,
# +90 deg = 3 o'clock / right wall, -90 = 9 o'clock / left wall).  The
# angles items are placed at, after folding, are multiples of 45 degrees
# (0, +/-45, +/-90): the cardinals plus the diagonal clock positions.
COLLECT_WINDOW = 0.5        # rad: items are collected within this cone
STEER_MARGIN = 0.25         # rad of angle control you always keep for timing
DIAGONAL = math.pi / 4      # 2, 4, 8 and 10 o'clock sit at 45 degrees
# With full steer held, the man used to settle at MAX_STEER (1.75 rad),
# reachable only by the cardinals.  A held steer now settles on a shelf
# at 1.18 rad, chosen so it sits inside the 0.5 rad collection window of
# BOTH families: the 45-degree diagonals (2, 4, 8 and 10 o'clock, at
# 0.785 rad) and the 90-degree walls (3 and 9 o'clock, at 1.571 rad).
# One steady line therefore takes both - no precise release timing - and
# the shelf is a pause, not a wall: keep holding past HOLD_CREEP_DELAY
# and the target resumes toward the rim, so deliberately riding off the
# edge still ends in a fall.
HOLD_SHELF = DIAGONAL + 0.395              # 1.18 rad: the both-families park
HOLD_CREEP_DELAY = 2.5                     # s of hold before the rim resumes
# Distances (game units; ~500-1600 units/s) for steering guidance: far
# enough to always see the next diamond in a chain (they sit roughly
# 1900 units apart on the shipped stages).
ASSIST_AHEAD = 8000.0        # how far ahead the assist looks for targets
GUARD_RADIUS = 260.0        # obstacle guard zone radius around a hazard
GUARD_WINDOW = 0.6          # angular guard window around a hazard's angle

SCORE_DIAMOND = 100
SCORE_HEART = 50
SCORE_STAR = 100
SCORE_BOOST = 50
SCORE_SQUASH = 10
SCORE_OBSTACLE = -150
FINISH_BONUS_PER_LIFE = 500


@dataclass
class GameEvent:
    """One gameplay event to be rendered as sound/speech by the app."""

    kind: str
    pan: float = 0.0
    angle: float = 0.0
    text: str = ""


class GliderMan:
    """Player physics, ported from the original GliderMan behaviour."""

    def __init__(self, track: Track) -> None:
        self.track = track
        self.dist = 0.0
        self.speed = MIN_SPEED
        self.angle = 0.0
        self.braking = False
        # gameplay timers count down in game-time so they behave the same
        # in real play and in accelerated simulations
        self.boost_timer = 0.0
        self.boost_charges = 0
        self.god_timer = 0.0
        self.stall_timer = 0.0
        self.off_wall_t = 0.0
        self._edge_warned = False
        self.no_fall = False    # tutorial practice: falling disabled
        self.finished = False
        self.finish_slowdown = 1.0

    # ------------------------------------------------------------------ api
    @property
    def boost_active(self) -> bool:
        return self.boost_timer > 0.0

    @property
    def god_active(self) -> bool:
        return self.god_timer > 0.0

    def progress(self) -> float:
        return min(1.0, self.dist / max(1.0, self.track.total_length))

    def frame(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        pos, fwd, right, up = self.track.frame_at_distance(self.dist)
        # lateral offset from centre
        r = self.track.tube_radius(self.track.param_from_distance(self.dist)[0])
        px = pos[0] + right[0] * math.sin(self.angle) * r * 0.75
        py = pos[1] + right[1] * math.sin(self.angle) * r * 0.75
        pz = pos[2] + right[2] * math.sin(self.angle) * r * 0.75
        return (px, py, pz), fwd

    def activate_boost(self) -> bool:
        if self.boost_charges > 0:
            self.boost_charges -= 1
            self.boost_timer = BOOST_TIME
            return True
        return False

    def give_god(self) -> None:
        self.god_timer = GOD_TIME

    def update(self, dt: float, steer: float, brake: bool) -> List[GameEvent]:
        events: List[GameEvent] = []
        was_braking = self.braking
        self.braking = brake
        if brake and not was_braking:
            # Brake.wav shipped with the original game but was never wired:
            # emit a transition event so the engine layer can play it once
            # per brake application instead of retriggering every frame.
            events.append(GameEvent(kind="brake"))
        # tick gameplay timers (game-time based)
        for attr in ("boost_timer", "god_timer", "stall_timer"):
            v = getattr(self, attr)
            if v > 0.0:
                new = max(0.0, v - dt)
                setattr(self, attr, new)
                if attr == "god_timer" and new == 0.0:
                    # the star just wore off: the wall is deadly again
                    events.append(GameEvent(kind="god_expired"))

        # steering -----------------------------------------------------
        target = steer * MAX_STEER
        # Hold-shelf: a held steer settles on the diagonal set-point so
        # 45-degree items (2, 4, 8 and 10 o'clock) are collectible with
        # hold-then-release timing; holding past HOLD_CREEP_DELAY resumes
        # toward the rim so riding the wall to a fall still works.
        if steer == 0.0 or steer * getattr(self, "_last_steer", 0.0) < 0.0:
            self._hold_t = 0.0
        else:
            self._hold_t = getattr(self, "_hold_t", 0.0) + dt
        self._last_steer = steer
        if abs(target) > HOLD_SHELF and getattr(self, "_hold_t", 0.0) < HOLD_CREEP_DELAY:
            target = math.copysign(min(abs(target), HOLD_SHELF), target)
        rate = STEER_RATE if abs(target) > abs(self.angle) and (target * self.angle >= 0 or target == 0) else STEER_RETURN
        if target > self.angle:
            self.angle = min(target, self.angle + rate * dt)
        elif target < self.angle:
            self.angle = max(target, self.angle - rate * dt)

        # falling off the edge -------------------------------------------
        # Like the original: ride too far up the wall and, after a short
        # grace, you tumble out of the slide.  A warning fires on entry so
        # audio players can react before the grace runs out.
        # God mode carries the original's promise: nothing can hurt you,
        # including the edge.  Riding the wall is safe (and silent) until
        # the star expires; the warning then re-arms on the next frame.
        if not self.no_fall and not self.god_active and abs(self.angle) > FALL_ANGLE:
            self.off_wall_t += dt
            if not self._edge_warned:
                self._edge_warned = True
                events.append(GameEvent(kind="fall_warning"))
            if self.off_wall_t >= FALL_GRACE:
                self.off_wall_t = 0.0
                self._edge_warned = False
                events.append(GameEvent(kind="fall"))
        else:
            self.off_wall_t = 0.0
            self._edge_warned = False

        # speed --------------------------------------------------------
        # Slope from the engine's own altitude column (col 1) modulates a
        # cruise target; speed eases toward it so momentum carries through
        # climbs, like a real (water-assisted) slide.
        h_now = self.track.height_at_distance(self.dist)
        h_ahead = self.track.height_at_distance(self.dist + SLOPE_LOOKAHEAD)
        slope = (h_now - h_ahead) / SLOPE_LOOKAHEAD
        if self.boost_active:
            self.speed = BOOST_SPEED
        elif brake:
            self.speed = max(MIN_SPEED * 0.55, self.speed - BRAKE_DECEL * dt)
        else:
            band = MAX_SPEED - MIN_SPEED
            frac = max(0.12, min(1.0, CRUISE_BIAS + slope * SLIDE_GRAVITY))
            target = MIN_SPEED + band * frac
            if target > self.speed:
                self.speed = min(target, self.speed + MAX_ACCEL * dt)
            else:
                self.speed = max(target, self.speed - NATURAL_DECEL * dt)
            if self.speed < MIN_SPEED:
                self.speed = MIN_SPEED
        if self.stall_timer > 0.0:
            self.speed = MIN_SPEED * 0.7

        # progress -----------------------------------------------------
        if not self.finished:
            self.dist += self.speed * dt * self.finish_slowdown
            if self.dist >= self.track.total_length:
                self.dist = self.track.total_length
                self.finished = True
        return events


class PlayState:
    """Runs one stage: countdown, racing, finish."""

    def __init__(self, app, track: Track, stage_number: int) -> None:
        self.app = app
        self.track = track
        self.stage_number = stage_number
        self.man = GliderMan(track)
        self.score = 0
        self.lives = 3
        self.gender = getattr(getattr(app, "options", None), "voice", "male") or "male"
        # Fresh copies, NOT references: collection/hit flags are mutated
        # during play, and a shallow list copy still shares the item
        # objects with the Track - replaying a stage in one session would
        # inherit the previous run's collected and hit items.
        self.items = [_copy_item(it) for it in track.items]
        self.elapsed = 0.0
        self.phase = "countdown"     # countdown -> racing -> finished
        self.countdown_t = 3.4
        self.last_tick = 4
        self.status_timer = 0.0
        self.status_interval = 8.0
        self.rings_total = sum(1 for i in self.items if i.cls == CLASS_DIAMOND)
        self.rings_got = 0
        self._loops_announced: set = set()
        self._loop_passed: set = set()  # clusters whose pass-through was called out
        self._loop_entered: set = set()  # clusters whose entry scream fired
        self._loop_windows: List[Tuple[float, float]] = []  # silenced spans
        self.near_miss_window = 0.9
        self.start_time = 0.0
        self._dialog_t = 0.0          # pending death-dialog delay (cry length)
        self._dialog_text: Optional[str] = None

    # ---------------------------------------------------------------- input
    def handle_event(self, event) -> None:
        key = getattr(event, "key", None)
        if self._dialog_text is not None:
            return  # dialog pending: nothing may act before it exists
        if self.phase == "finished":
            if key in (13, 10, 32):  # enter/space
                self.app.finish_stage(self)
            return
        if key in (27,):  # escape
            self.app.enter_pause(self)
        elif key in (112, 104):  # P or H: progress and speed
            self.report_progress()
        elif key == 99:  # C: score
            self.report_score()
        elif key == 105:  # I: diamonds
            self.report_items()
        elif key == 108:  # L: lives
            self.report_lives()
        elif key == 114:  # R: radar
            self.report_radar()
        elif key == 101:  # E: progress scan
            self.report_scan()
        elif key == 32:  # space: boost
            if self.man.boost_charges > 0 and self.man.activate_boost():
                self.app.audio.play("chevron", 0.0, 0.9)
                self.app.speech.say("Boost!", urgent=True)
        elif key in (109,):  # M: audio mode
            self.app.cycle_audio()

    # -------------------------------------------------------------- reporting
    def speed_display(self) -> Tuple[int, str]:
        """Speed in the player's chosen units: (value, spoken unit)."""
        if getattr(self.app.options, "speed_units", "mph") == "kmh":
            return int(self.man.speed / 10), "kilometres per hour"
        # internal units: speed/10 is km/h; convert to miles per hour
        return int(self.man.speed / 16.0934), "miles per hour"

    def report_progress(self) -> None:
        """P (or H): stage, progress along the slide, and speed."""
        pct = int(self.man.progress() * 100)
        speed, unit = self.speed_display()
        self.app.speech.say(
            f"Stage {self.stage_number}, {pct} percent, {speed} {unit}.",
            urgent=True,
        )

    def report_score(self) -> None:
        """C: the current score."""
        self.app.speech.say(f"Score {self.score}.", urgent=True)

    def report_items(self) -> None:
        """I: diamonds collected so far."""
        self.app.speech.say(
            f"{self.rings_got} of {self.rings_total} diamonds.", urgent=True
        )

    def report_lives(self) -> None:
        """L: lives remaining."""
        if self.lives > 0:
            self.app.speech.say(
                f"{self.lives} {'life' if self.lives == 1 else 'lives'} left.",
                urgent=True,
            )
        else:
            self.app.speech.say("No lives left.", urgent=True)

    def report_radar(self) -> None:
        """Announce the next few items ahead with clock-face positions."""
        d = self.man.dist
        upcoming = [it for it in self.items if not it.collected and not it.hit and it.t * self.track.total_length > d]
        upcoming.sort(key=lambda it: it.t * self.track.total_length)
        if not upcoming:
            self.app.speech.say("Nothing ahead - the finish is close!", urgent=True)
            return
        parts: List[str] = []
        for it in upcoming[:3]:
            gap = int((it.t * self.track.total_length - d) / 10)
            what = _item_word(it.cls)
            clock = _clock_position(it.angle)
            parts.append(f"{what}, {clock}, in {gap} metres")
        self.app.speech.say(", ".join(parts) + ".", urgent=True)

    def report_scan(self) -> None:
        """One-line overview of what the next 15 seconds of slide holds."""
        d = self.man.dist
        horizon = d + self.man.speed * 15
        cnt = {"diamond": 0, "crab": 0, "duck": 0, "boost": 0}
        for it in self.items:
            x = it.t * self.track.total_length
            if it.collected or it.hit or not (d < x <= horizon):
                continue
            if it.cls in cnt:
                cnt[it.cls] += 1
        bits = []
        if cnt["diamond"]:
            bits.append(f"{cnt['diamond']} diamonds")
        if cnt["crab"]:
            bits.append(f"{cnt['crab']} crab{'s' if cnt['crab'] != 1 else ''}")
        if cnt["duck"]:
            bits.append(f"{cnt['duck']} duck{'s' if cnt['duck'] != 1 else ''}")
        if cnt["boost"]:
            bits.append("a boost")
        text = "; ".join(bits) if bits else "clear water"
        self.app.speech.say(f"Next 15 seconds: {text}.", urgent=True)

    # ---------------------------------------------------------------- update
    def update(self, dt: float, steer: float, brake: bool) -> None:
        # pending death dialog: surface it once the fall cry has finished
        if self._dialog_text is not None:
            self._dialog_t -= dt
            if self._dialog_t <= 0.0:
                self.app.show_dialog(self._dialog_text)
                self._dialog_text = None
            return
        if self.phase == "countdown":
            self.countdown_t -= dt
            tick = int(self.countdown_t) + 1
            if tick < self.last_tick and tick >= 1:
                self.last_tick = tick
                self.app.audio.play("countdown", 0.0, 0.8)
                self.app.speech.say(str(tick), urgent=True, cue="countdown")
            if self.countdown_t <= 0:
                self.phase = "racing"
                self.start_time = time.monotonic()
                self.app.audio.play("start", 0.0, 0.9)
                self.app.speech.say("Go!", urgent=True, cue="go")
            return

        if self.phase == "racing":
            self.elapsed += dt
            assist = getattr(self.app.options, "assist_pan_ahead", 0.12)
            guidance = self._steer_guidance()
            if guidance[0] is not None and assist > 0.0 and abs(steer) > 0.1:
                # never fight a deliberate steer: the assist helps you
                # reach what you are already reaching for, it does not
                # take the wheel (a held line must stay a held line)
                auto_dir = max(-1.0, min(1.0, angle_diff_signed(guidance[0], self.man.angle) * 3.0))
                if auto_dir * steer >= 0.0:
                    steer = assist_steer(steer, self.man.angle, guidance[0], assist)
            events = self.man.update(dt, steer, brake)
            for ev in events:
                if ev.kind == "fall":
                    self.apply_fall()
                elif ev.kind == "fall_warning":
                    self.app.speech.beep([(300, 90), (240, 120)], urgent=True)
                    self.app.speech.say(
                        "Careful - you are riding the wall! Ease off or you will fall.",
                        urgent=True,
                    )
                elif ev.kind == "god_expired":
                    # descending contour: protection gone, edge is deadly
                    self.app.speech.beep([(660, 50), (440, 50), (330, 90)], urgent=True)
                    self.app.speech.say("God mode over.", urgent=True)
                elif ev.kind == "brake":
                    # the original's brake sample, once per application
                    self.app.audio.play("brake", 0.0, 0.8)
            self._check_items()
            self._emit_ambience()
            self.status_timer += dt
            if self.status_timer >= self.status_interval:
                self.status_timer = 0.0
                self._auto_status()
            if self.man.finished:
                self.phase = "finished"
                self._on_finished()

    def _auto_status(self) -> None:
        # periodic info: waits its turn instead of chopping other speech;
        # suppressed inside loop clusters so the loop callout owns the air
        d = self.man.dist
        for lo, hi in self._loop_windows:
            if lo <= d <= hi:
                return
        nxt = self._next_item()
        if nxt is not None:
            gap = int((nxt.t * self.track.total_length - self.man.dist) / 10)
            # clock face relative to where you are pointing now, the same
            # wording the radar and the tutorial teach
            self.app.speech.say(
                f"{_item_word(nxt.cls)}, {_clock_position(nxt.angle - self.man.angle)}, {gap} metres.",
                urgent=False,
            )
        else:
            self.app.speech.say("Final stretch!", urgent=False)

    def _next_item(self) -> Optional[TrackItem]:
        d = self.man.dist
        upcoming = [it for it in self.items if not it.collected and not it.hit and it.t * self.track.total_length > d]
        upcoming.sort(key=lambda it: it.t * self.track.total_length)
        return upcoming[0] if upcoming else None

    def _steer_guidance(self) -> Optional[Tuple[float, str]]:
        """Where steering help should aim, and why.

        The accessibility assist must never drag the rider into an
        obstacle, so hazards win a guard zone: near a crab or duck the
        guidance vanishes entirely (steer is the player's own), and once
        the hazard is passed, collectibles ahead are targeted again.
        """
        d = self.man.dist
        total = self.track.total_length
        # nearest obstacle within the guard window, ahead of the rider
        haz = None
        for it in self.items:
            if it.hit or it.cls not in (CLASS_CRAB, CLASS_DUCK):
                continue
            x = it.t * total
            if x <= d or x - d > GUARD_RADIUS:
                continue
            if abs(angle_diff_signed(it.angle, self.man.angle)) < GUARD_WINDOW:
                if haz is None or x < haz[0]:
                    haz = (x, it)
        if haz is not None:
            return None, "obstacle"
        # otherwise: nearest collectible within look-ahead distance
        best = None
        for it in self.items:
            if it.collected or it.cls in (CLASS_CRAB, CLASS_DUCK):
                continue
            x = it.t * total
            if x <= d or x - d > ASSIST_AHEAD:
                continue
            if best is None or x < best[0]:
                best = (x, it)
        if best is None:
            return None, "none"
        return best[1].angle, "item"

    # ---------------------------------------------------------------- items
    def _check_items(self) -> None:
        d = self.man.dist
        total = self.track.total_length
        for it in self.items:
            if it.collected or it.hit:
                continue
            x = it.t * total
            if x > d:
                continue
            diff = _angle_diff(it.angle, self.man.angle)
            self._check_loops(d)
            if it.cls in (CLASS_CRAB, CLASS_DUCK):
                if diff < 0.55:
                    it.hit = True
                    self._on_obstacle(it)
                elif diff < self.near_miss_window:
                    it.hit = True
                    self.app.audio.play(it.cls, _pan(it.angle), 0.25)
                    self.app.speech.beep([(160, 50, "square")])
            else:
                if diff < 0.5:
                    it.collected = True
                    self._on_collect(it)

    def _on_collect(self, it: TrackItem) -> None:
        pan = _pan(it.angle)
        if it.cls == CLASS_DIAMOND:
            self.score += SCORE_DIAMOND
            self.rings_got += 1
            self.app.audio.play("diamond", pan, 0.9)
            self.app.speech.beep([(880, 50), (1174, 50), (1568, 70)])
        elif it.cls == CLASS_HEART:
            self.score += SCORE_HEART
            self.lives = min(5, self.lives + 1)
            self.app.audio.play("heart", pan, 1.0)
            self.app.speech.say("Extra life!", urgent=True)
            self.app.speech.beep([(1046, 60), (1318, 60), (1568, 90)], urgent=True)
        elif it.cls == CLASS_STAR:
            self.score += SCORE_STAR
            self.man.give_god()
            self.app.audio.play("godmode", pan, 1.0)
            self.app.speech.say(
                f"God mode! Obstacles and falls cannot hurt you for {int(GOD_TIME)} seconds.",
                urgent=True,
            )
        elif it.cls == CLASS_BOOST:
            self.score += SCORE_BOOST
            self.man.boost_charges += 1
            self.app.audio.play("chevron", pan, 0.9)
            self.app.speech.say("Boost charged - press space.", urgent=True)

    def _on_obstacle(self, it: TrackItem) -> None:
        pan = _pan(it.angle)
        if self.man.god_active:
            self.score += SCORE_SQUASH
            self.app.audio.play("godhit", pan, 1.0)
            self.app.speech.beep([(200, 60, "square"), (140, 80, "square")], urgent=True)
            return
        self.score += SCORE_OBSTACLE if getattr(self.app.options, "speedrun_mode", True) else 0
        factor = DUCK_SLOWDOWN if it.cls == CLASS_DUCK else CRAB_SLOWDOWN
        if not getattr(self.app.options, "speedrun_mode", True):
            # practice mode: obstacles barely slow you
            factor = 1.0 - (1.0 - factor) * 0.4
        self.man.speed *= factor
        self.man.stall_timer = 0.4
        variant = {"crab": "crab_hit", "duck": "duck"}.get(it.cls, it.cls)
        self.app.audio.play(sfx_key(variant, self.gender) if variant != "duck" else "duck",
                            pan, 1.0)
        self.app.speech.beep([(180, 120, "square")], urgent=True)
        self.app.speech.say(f"{_item_word(it.cls)}! Slowed down.", urgent=True)

    # -------------------------------------------------------------- loops
    def _loop_clusters(self) -> List[Tuple[int, int]]:
        """Loop-the-loop clusters as (start_seg, end_seg) handle ranges.

        The roll columns describe the tube's banking frame on nearly every
        handle; a real loop is a run of consecutive handles rolled to a
        full inversion (about 180 degrees).  Runs are merged so one loop
        gets one callout.
        """
        clusters: List[Tuple[int, int]] = []
        start = None
        for i, h in enumerate(self.track.handles):
            full = max(abs(h.roll_in), abs(h.roll_out)) >= 179.0
            if full and start is None:
                start = i
            elif not full and start is not None:
                clusters.append((start, i - 1))
                start = None
        if start is not None:
            clusters.append((start, len(self.track.handles) - 1))
        return clusters

    def _check_loops(self, dist: float) -> None:
        """Call out each loop cluster: one approach line, one pass-through."""
        for start, end in self._loop_clusters():
            entry = self.track._cum_len[start]
            exit_d = self.track._cum_len[min(end + 2, len(self.track.handles) - 1)]
            if start not in self._loops_announced:
                gap = entry - dist
                if 0.0 < gap <= 3000.0:
                    self._loops_announced.add(start)
                    metres = max(50, int(round(gap / 10 / 50.0) * 50))
                    self._loop_windows.append((entry, exit_d))
                    self._announce_loop(metres)
            if start not in self._loop_entered and entry <= dist < exit_d:
                # the payoff: the original loop scream as you go over
                self._loop_entered.add(start)
                self.app.audio.play(sfx_key("loop", self.gender), 0.0, 0.9)
            elif start not in self._loop_passed and dist > exit_d:
                self._loop_passed.add(start)  # exit: nothing spoken, the scream said it

    def _announce_loop(self, metres: int) -> None:
        """Call out a loop-the-loop ahead, with the original scream variant."""
        # Non-urgent: appends after current speech instead of interrupting
        # it, and the auto-status silence window keeps the air clear after.
        self.app.speech.say(
            f"Loop the loop in {metres} metres. Hold your line and enjoy the ride.",
            urgent=False,
        )
        self.app.speech.beep([(330, 90), (440, 90), (587, 140)], urgent=True)

    # ------------------------------------------------------------- ambience
    def _emit_ambience(self) -> None:
        sp = (self.man.speed - MIN_SPEED) / max(1.0, (MAX_SPEED - MIN_SPEED))
        self.app.audio.set_splash(min(1.0, 0.35 + sp * 0.9), max(0.0, sp - 0.55) * 2.2, _pan(self.man.angle))

    # -------------------------------------------------------------- finish
    def _on_finished(self) -> None:
        self.app.audio.play("start", 0.0, 0.8)
        self.app.speech.beep([(659, 60), (880, 60), (1046, 60), (1318, 60), (1568, 120)], urgent=True)
        self.app.speech.say("Finished!", urgent=True, cue="finish")
        time_bonus = max(0, 2000 - int(self.elapsed * 10))
        life_bonus = self.lives * FINISH_BONUS_PER_LIFE
        self.score += time_bonus + life_bonus
        self.app.speech.say(
            f"Time {self.elapsed:.1f} seconds, time bonus {time_bonus}, life bonus {life_bonus}. "
            f"Final score {self.score}.",
            urgent=True,
        )
        self.app.audio.set_splash(0.15, 0.0)

    # -------------------------------------------------------------- damage
    def apply_fall(self) -> None:
        """Fell off the slide: lose a life, respawn on the spot (or die)."""
        if self.man.god_active:
            # defense in depth: the man never emits a fall while the star
            # is live, but if one ever arrives, god mode absorbs it
            return
        self.lives -= 1
        variant = "fall_male" if self.gender == "male" else "fall"
        self.app.audio.play(variant, 0.0, 1.0)
        # Authentic to the original: the music stops when you die and only
        # returns once you respawn with your remaining lives.  Ambience
        # goes with it, so the death dialog sits in silence.
        self.app.audio.stop_music()
        self.app.audio.stop_ambience()
        # respawn centred with a slow recovery beat
        self.man.angle = 0.0
        self.man.speed = MIN_SPEED * 0.75
        self.man.stall_timer = 1.0
        if self.lives <= 0:
            self.app.finish_stage(self)
            return
        # Modal death dialog: gameplay waits until Enter or a click.  If
        # sound effects are on, the dialog waits for the fall cry to
        # finish first - speech must not fight the cry for attention.
        delay = self.app.audio.sound_length(variant) \
            if getattr(self.app.options, "sound", True) else 0.0
        self._dialog_t = max(0.0, delay)
        self._dialog_text = (
            f"You fell out of the slide! {self.lives} {'life' if self.lives == 1 else 'lives'} "
            "left. Press Enter or click to continue."
        )

    # ----------------------------------------------------------------- view
    def steering_input(self, keys) -> Tuple[float, bool]:
        return read_steering(keys)


# ------------------------------------------------------------------ helpers
def read_steering(keys) -> Tuple[float, bool]:
    """Translate a pygame-style key state into (steer, brake)."""
    def kd(*codes: int) -> bool:
        return any(keys[c] for c in codes)

    left = kd(1073741904, 276)   # K_LEFT
    right = kd(1073741903, 275)  # K_RIGHT
    a = kd(97)
    d = kd(100)
    brake = kd(1073741905, 274, 115, 83)  # down / S
    steer = 0.0
    if left or a:
        steer -= 1.0
    if right or d:
        steer += 1.0
    return steer, brake


def _copy_item(it: TrackItem) -> TrackItem:
    """A mutable per-stage copy of a track item definition."""
    return TrackItem(cls=it.cls, t=it.t, angle=it.angle,
                     collected=it.collected, hit=it.hit, alive=it.alive)


def _angle_diff(a: float, b: float) -> float:
    return abs(((a - b + math.pi) % math.tau) - math.pi)


def angle_diff_signed(a: float, b: float) -> float:
    """Signed shortest difference a - b in radians."""
    return ((a - b + math.pi) % math.tau) - math.pi


def assist_steer(current_steer: float, player_angle: float, target_angle: float,
                 strength: float) -> float:
    """Blend player steering toward a target angle (accessibility assist)."""
    if strength <= 0.0:
        return current_steer
    diff = angle_diff_signed(target_angle, player_angle)
    auto = max(-1.0, min(1.0, diff * 3.0))
    blend = min(0.85, strength * 4.0)
    return current_steer * (1.0 - blend) + auto * blend


def _pan(angle: float) -> float:
    """Clock angle (radians, tube frame) -> stereo pan in [-1, 1].

    Item and obstacle sounds pan by the item's ABSOLUTE clock position,
    not the bearing from the rider: the spoken callouts say "3 o'clock,
    right wall", so the chime must come from the right wall too.  (A
    relative bearing flips sign whenever the rider sweeps past the
    item's angle mid-glide - a right-wall diamond grabbed while moving
    left chimed from the left, the exact bug this killed.)
    """
    return math.sin(max(-1.4, min(1.4, angle)))


def _clock_position(angle: float) -> str:
    """Position on the clock painted inside the tube's cross-section.

    12 is the bottom of the tube (the line you ride), 3 the right wall,
    9 the left wall and 6 the top of the tube - nothing is ever 'behind'
    you in a slide.  The four cardinal points get plain-language tags so
    a fresh player never has to decode the number.
    """
    deg = math.degrees(angle) % 360.0
    if deg <= 20.0 or deg >= 340.0:
        return "straight ahead"
    if 160.0 <= deg <= 200.0:
        return "6 o'clock, the top of the tube"
    if 70.0 <= deg <= 110.0:
        return "3 o'clock, right wall"
    if 250.0 <= deg <= 290.0:
        return "9 o'clock, left wall"
    hour = int(round(deg / 30.0)) % 12
    if hour == 0:
        hour = 12
    return f"{hour} o'clock"


def _item_word(cls: str) -> str:
    return {
        CLASS_DIAMOND: "diamond",
        CLASS_HEART: "extra life",
        CLASS_STAR: "god mode star",
        CLASS_BOOST: "boost pad",
        CLASS_CRAB: "crab",
        CLASS_DUCK: "evil duck",
    }.get(cls, cls)
