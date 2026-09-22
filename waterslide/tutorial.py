"""Interactive audio tutorial for new blind players.

A guided stage that teaches, with spoken coaching and the game's real
sounds: steering (the water pans with you), collecting diamonds (and the
clock-face positioning system), avoiding crabs, braking, the boost pad,
and the P/C/I/L report keys.  Each phase waits for the player to actually
perform the action, and R repeats the current instruction.

The player's slide position is frozen; only steering is live, so nobody
can fail the tutorial - completing it just takes a couple of minutes.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

from .game import GliderMan, MAX_STEER
from .track import CLASS_BOOST, CLASS_CRAB, CLASS_DIAMOND, Track, TrackItem


class TutorialStep:
    def __init__(self, key: str, instruction: str, done_check, timeout: float = 0.0) -> None:
        self.key = key
        self.instruction = instruction
        self.done_check = done_check    # (tutorial, steer, brake) -> bool
        self.timeout = timeout          # 0 = no timeout (wait forever)


class Tutorial:
    """Scripted coaching session at a fixed point on stage 1."""

    def __init__(self, app, track: Track) -> None:
        self.app = app
        self.track = track
        self.man = GliderMan(track)
        self.man.speed = 0.0
        self.man.no_fall = True  # nobody fails the tutorial by steering
        self.dist0 = min(3000.0, track.total_length * 0.5)
        self.man.dist = self.dist0
        self.phase = "briefing"       # briefing -> running -> done
        self.brief_t = 1.0
        self.step_index = 0
        self.wait_t = 0.0
        self.finished = False
        self._practice_items: List[TrackItem] = []
        self._last_coach = 0.0
        self._steps = self._build_steps()

    # ------------------------------------------------------------------ steps
    def _build_steps(self) -> List[TutorialStep]:
        def steer_left(t, steer, brake):
            return steer < -0.5

        def steer_right(t, steer, brake):
            return steer > 0.5

        def centred(t, steer, brake):
            return abs(t.man.angle) < 0.15 and abs(steer) < 0.1

        def hit_practice_diamond(t, steer, brake):
            return any(it.collected for it in t._practice_items if it.cls == CLASS_DIAMOND)

        def hit_diagonal_diamond(t, steer, brake):
            return any(it.collected for it in t._practice_items if it.cls == CLASS_DIAMOND)

        def dodged_crab(t, steer, brake):
            # update() accumulates t._crab_safe_t while the player holds
            # the side away from the crab; almost a second of that is a
            # genuine dodge.
            return getattr(t, "_crab_safe_t", 0.0) >= 0.9

        def braked(t, steer, brake):
            return brake

        def grabbed_boost(t, steer, brake):
            return any(it.collected for it in t._practice_items if it.cls == CLASS_BOOST)

        return [
            TutorialStep(
                "steer_left",
                "First, steering. Only the left and right arrows steer - the up arrow "
                "does nothing. The water sound moves with you: it pans left when you "
                "slide left. Hold the left arrow now.",
                steer_left,
            ),
            TutorialStep(
                "steer_right",
                "Great. Now hold the right arrow.",
                steer_right,
            ),
            TutorialStep(
                "centred",
                "Perfect. Let go and the water centres you again. Wait for centre.",
                centred,
                timeout=12.0,
            ),
            TutorialStep(
                "diamond",
                "Now, diamonds. I've placed one at 3 o'clock, hard on your right wall: "
                "its chime plays panned to the right. Hold the right arrow to grab it. "
                "Press R to hear this again any time.",
                hit_practice_diamond,
                timeout=45.0,
            ),
            TutorialStep(
                "diagonal",
                "Diagonals now. The clock face has half-past positions too: "
                "2 o'clock is halfway up the right wall, 10 the same on the "
                "left. Here's the trick: hold the wall key and your line "
                "parks just outside both the walls and the diagonals, so a "
                "steady hold takes them both. I've put one at 2 o'clock - "
                "hold the right arrow and ride it through. Release after "
                "you hear the chime to come back to centre. Press R to "
                "hear this again.",
                hit_diagonal_diamond,
                timeout=60.0,
            ),
            TutorialStep(
                "crab",
                "Excellent! Next: crabs. They slow you down, and you'll hear their "
                "scuttle on the side they sit. I've placed one at 9 o'clock on your "
                "left: hold the right arrow to stay on the safe side until I say "
                "you're clear.",
                dodged_crab,
                timeout=45.0,
            ),
            TutorialStep(
                "brake",
                "Well done. Braking: hold the down arrow or S to slow down when it "
                "gets hairy.",
                braked,
                timeout=20.0,
            ),
            TutorialStep(
                "boost",
                "Nice. Boost pads charge your boost: press space in a race to fire "
                "it. I've placed one straight ahead, at 12 o'clock. Steer to centre "
                "and let it come to you.",
                grabbed_boost,
                timeout=45.0,
            ),
            TutorialStep(
                "reports",
                "Last skill: asking the game questions. P speaks your progress "
                "and speed, C your score, I your diamonds, L your lives, R gives "
                "a radar of the next items with clock-face positions and metres, "
                "E scans the next fifteen seconds. Press P now.",
                None,  # handled by handle_event marking the step done
                timeout=60.0,
            ),
        ]

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.phase = "running"
        self.wait_t = 0.0
        self.app.audio.start_ambience()
        self.app.speech.say(
            "Welcome to the Waterslide Fusion tutorial. We're standing on the slide, "
            "not moving, so take your time. Two things to remember: only the left "
            "and right arrows steer, and press R any time to repeat an instruction.",
            urgent=True,
        )
        self._announce_step()

    def _announce_step(self) -> None:
        if self.step_index < len(self._steps):
            self.app.speech.say(self._steps[self.step_index].instruction, urgent=True)
        self._last_coach = time.monotonic()

    def _spawn_practice_items(self) -> None:
        """Place one diamond, one crab and one boost just ahead for the drills."""
        items = []
        for cls, angle, dist in ((CLASS_DIAMOND, math.pi / 2, 220.0),
                                 (CLASS_DIAMOND, math.pi / 4, 260.0),
                                 (CLASS_CRAB, -math.pi / 2, 220.0),
                                 (CLASS_BOOST, 0.0, 220.0)):
            items.append(TrackItem(cls=cls, t=self.dist0 + dist, angle=angle))
        self._practice_items = items

    # ----------------------------------------------------------------- events
    def handle_event(self, event) -> None:
        key = getattr(event, "key", None)
        if key == 114 and self.phase == "running":  # R: repeat instruction
            self._announce_step()
        elif key in (273, 1073741906) and self.phase == "running":  # up: not a steer key
            self.app.speech.say(
                "The up arrow doesn't steer. Only the left and right arrows steer; "
                "the down arrow brakes.",
                urgent=True,
            )
        elif key in (104, 112) and self.phase == "running":  # H/P
            step = self._steps[self.step_index]
            if step.key == "reports":
                self._complete_step()
            self.app.speech.say(
                "Tutorial mode. Steering is live, the slide is not moving.",
                urgent=True,
            )
        elif key == 27:  # escape: leave tutorial
            self._finish(quit_to_menu=True)

    # ----------------------------------------------------------------- update
    def update(self, dt: float, steer: float, brake: bool) -> None:
        if self.phase == "briefing":
            self.brief_t -= dt
            if self.brief_t <= 0:
                self.start()
            return
        if self.phase != "running":
            return

        self.man.update(dt, steer, brake)
        # freeze position on the slide
        self.man.dist = self.dist0
        self.man.speed = 0.0
        sp = abs(self.man.angle) / MAX_STEER
        self.app.audio.set_splash(0.5 + sp * 0.4, 0.0, math.sin(self.man.angle))

        # lazily spawn the practice items on the diamond step
        if self._steps[self.step_index].key == "diamond" and not self._practice_items:
            self._spawn_practice_items()

        # simulate practice items: collect/dodge them like PlayState does
        for it in self._practice_items:
            if it.collected or it.hit:
                continue
            if self._steps[self.step_index].key not in ("diamond", "diagonal", "crab", "boost", "reports"):
                continue
            diff = abs(((it.angle - self.man.angle + math.pi) % math.tau) - math.pi)
            # the wall drill and the diagonal drill each claim one diamond
            if it.cls == CLASS_DIAMOND:
                want = "diamond" if abs(abs(it.angle) - math.pi / 2) < 0.1 else "diagonal"
                if self._steps[self.step_index].key != want:
                    continue
            if it.cls in (CLASS_CRAB,):
                if diff < 0.55:
                    it.hit = True
                    pan = math.sin(max(-1.4, min(1.4, it.angle)))
                    self.app.audio.play("crab", pan, 0.8)
                    self.app.speech.beep([(180, 120, "square")], urgent=True)
            else:
                if diff < 0.5:
                    it.collected = True
                    pan = math.sin(max(-1.4, min(1.4, it.angle)))
                    sound = "diamond" if it.cls == CLASS_DIAMOND else "chevron"
                    cue = [(880, 50), (1174, 50), (1568, 70)] if it.cls == CLASS_DIAMOND else None
                    self.app.audio.play(sound, pan, 0.9)
                    if cue:
                        self.app.speech.beep(cue, urgent=True)

        # crab drill: accumulate safe-side time, reset when touching it.
        # The crab sits at -pi/2 (9 o'clock, left); holding right of centre
        # counts as clear, and bumping the crab restarts the drill.
        step = self._steps[self.step_index]
        crab = next((it for it in self._practice_items if it.cls == CLASS_CRAB), None)
        if step.key == "crab" and crab is not None:
            diff = ((crab.angle - self.man.angle + math.pi) % math.tau) - math.pi
            if crab.hit:
                # steered into it: re-coach and reset the drill
                if not getattr(self, "_crab_coached", False):
                    self._crab_coached = True
                    self._crab_safe_t = 0.0
                    self.app.speech.say(
                        "That's the crab - hear how it slows you down? "
                        "Steer right, away from its side, and hold it there.",
                        urgent=True,
                    )
            elif self.man.angle > 0.5:  # clearly holding the right side
                self._crab_safe_t = getattr(self, "_crab_safe_t", 0.0) + dt
            else:
                self._crab_safe_t = 0.0

        if step.done_check is not None and step.done_check(self, steer, brake):
            self._complete_step()
            return

        # timeouts re-coach rather than fail
        if step.timeout:
            self.wait_t += dt
            if self.wait_t >= step.timeout:
                self.wait_t = 0.0
                hint = {
                    "diamond": "Listen again: the diamond is at 3 o'clock, on your right. "
                               "Hold the right arrow.",
                    "diagonal": "Listen again: the diagonal diamond is at 2 o'clock. "
                                "Hold the right arrow - your line parks right beside "
                                "the diagonals - and ride it through.",
                    "crab": "The crab is at 9 o'clock, on your left. Hold the "
                               "right arrow to stay clear of its side.",
                    "boost": "The boost pad is straight ahead at 12 o'clock. Steer to "
                               "centre and hold.",
                }.get(step.key)
                if hint:
                    self.app.speech.say(hint, urgent=True)
        # periodic re-coach for actions
        if time.monotonic() - self._last_coach > 25.0:
            self._announce_step()

    def _complete_step(self) -> None:
        self.wait_t = 0.0
        self._crab_safe_t = 0.0
        self._crab_coached = False
        praises = ["Good.", "That's it.", "Nice work.", "Got it."]
        self.app.speech.say(praises[self.step_index % len(praises)], urgent=True)
        self.step_index += 1
        if self.step_index >= len(self._steps):
            self._finish()
        else:
            self._announce_step()

    def _finish(self, quit_to_menu: bool = False) -> None:
        self.phase = "done"
        self.finished = True
        self.app.audio.stop_ambience()
        self.app.audio.set_splash(0, 0)
        if quit_to_menu:
            self.app.speech.say("Tutorial closed.", urgent=True)
            self.app.show_main_menu()
        else:
            self.app.speech.say(
                "Tutorial complete! You're ready for the slide. "
                "Remember: P for progress, C for score, I for diamonds, L for lives, R for radar, E to scan. Have fun!",
                urgent=True,
            )
            self.app.tutorial_done()

    # ------------------------------------------------------------------- view
    def status_line(self) -> str:
        if self.step_index < len(self._steps):
            return f"tutorial: {self._steps[self.step_index].key}"
        return "tutorial: done"
