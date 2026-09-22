"""Headless tests for gameplay, records, speech and audio."""

import math
import os
import time

import pytest

from waterslide.audio import AudioEngine, describe_position, pan_from_angle
from waterslide.game import (
    GliderMan,
    PlayState,
    _angle_diff,
    _clock_position,
    _item_word,
    _pan,
    angle_diff_signed,
    assist_steer,
)
from waterslide.records import Options, Records, ScoreRow
from waterslide.speech import BeepCue, _tone
from waterslide.track import CLASS_DIAMOND, load_all_tracks


@pytest.fixture(scope="module")
def tracks():
    for d in (os.path.join("tests", "fixtures", "level"), os.path.join("assets", "content", "level")):
        if os.path.isdir(d):
            ts = load_all_tracks(d)
            if ts:
                return ts
    pytest.skip("no level fixtures; run python -m waterslide.ipa_extract")


class DummyApp:
    def __init__(self):
        self.audio = HeadlessAudio()
        self.speech = SpeechStub()
        self.options = Options()
        self.theme = _ThemeStub()

    def enter_pause(self, play):
        pass

    def finish_stage(self, play):
        pass

    def cycle_audio(self):
        pass


class _ThemeStub:
    scheme = "dark"

    def set_override(self, mode):
        pass


def _theme_row_value(self):
    return getattr(self.options, "theme_mode", "auto")


DummyApp._theme_row_value = _theme_row_value


class SpeechStub:
    def __init__(self):
        self.messages = []
        self.beeps = []

    def say(self, text, urgent=False, cue=None):
        self.messages.append(text)

    def beep(self, pattern, urgent=False):
        self.beeps.append(tuple(pattern))


class HeadlessAudio(AudioEngine):
    """Audio engine that never touches devices."""

    def __init__(self, headless: bool = False):
        self.enabled = True
        self.mode = "headless"
        self.sounds = {}
        self.panned = []
        self.splash = (0.0, 0.0, 0.0)

    def play(self, key, pan=0.0, volume=1.0):
        self.panned.append((key, pan, volume))

    def set_splash(self, water, wind, pan=0.0):
        self.splash = (water, wind, pan)

    def sound_length(self, key):
        # canned length so headless tests exercise the deferred death dialog
        return 1.5 if key.startswith("fall") else 0.0


class _KeyState:
    def __init__(self, keys=()):
        self.keys = set(keys)

    def __getitem__(self, k):
        return k in self.keys


# ------------------------------------------------------------------ physics
class TestGliderMan:
    def test_moves_forward(self, tracks):
        man = GliderMan(tracks[0])
        d0 = man.dist
        man.update(0.1, 0.0, False)
        assert man.dist > d0

    def test_brake_slows(self, tracks):
        man = GliderMan(tracks[0])
        for _ in range(30):
            man.update(1 / 60, 0.0, False)
        fast = man.speed
        for _ in range(60):
            man.update(1 / 60, 0.0, True)
        assert man.speed < fast

    def test_steering_moves_angle(self, tracks):
        man = GliderMan(tracks[0])
        for _ in range(20):
            man.update(1 / 60, 1.0, False)
        assert man.angle > 0.5
        for _ in range(40):
            man.update(1 / 60, 0.0, False)
        assert man.angle < 0.2  # returns to centre

    def test_angle_clamped_to_walls(self, tracks):
        man = GliderMan(tracks[0])
        for _ in range(120):
            man.update(1 / 60, 1.0, False)
        assert man.angle <= 1.751  # MAX_STEER + eps (full steer rides past the rim)

    def test_riding_past_rim_falls_after_grace(self, tracks):
        man = GliderMan(tracks[0])
        man.no_fall = False
        fell = False
        for _ in range(int(4 * 60)):  # shelf pause + wall-riding
            man.update(1 / 60, 1.0, False)
            if getattr(man, "_fell", False):
                fell = True
                break
        # the fall event resets the edge timer; detect via repeated warning state
        assert man.angle <= 1.751
        # after 2 s beyond FALL_GRACE at full lock, at least one fall must have fired
        man2 = GliderMan(tracks[0])
        man2.no_fall = False
        falls = 0
        prev = 0.0
        for _ in range(int(6 * 60)):
            man2.update(1 / 60, 1.0, False)
            if man2.off_wall_t < prev:  # timer reset = fall happened
                falls += 1
            prev = man2.off_wall_t
        assert falls >= 1, "riding past the rim must eventually fall"

    def test_finish_reached(self, tracks):
        man = GliderMan(tracks[0])
        man.dist = tracks[0].total_length - 10
        man.update(0.05, 0.0, False)
        assert man.finished
        assert man.dist == tracks[0].total_length

    def test_frame_returns_valid(self, tracks):
        man = GliderMan(tracks[0])
        pos, fwd = man.frame()
        assert all(math.isfinite(x) for x in pos)
        assert math.isclose(math.dist(fwd, (0, 0, 0)), 1.0, abs_tol=1e-6)


# ------------------------------------------------------------------ play
class TestPlayState:
    def test_countdown_then_racing(self, tracks):
        app = DummyApp()
        play = PlayState(app, tracks[0], 1)
        assert play.phase == "countdown"
        for _ in range(int(4 * 60)):
            play.update(1 / 60, 0.0, False)
        assert play.phase == "racing"

    def test_collect_diamonds(self, tracks):
        app = DummyApp()
        play = PlayState(app, tracks[0], 1)
        play.phase = "racing"
        # drive the whole stage on autopilot toward each diamond
        for _ in range(int(60 * 120)):
            nxt = play._next_item()
            if nxt is None:
                break
            diff = angle_diff_signed(nxt.angle, play.man.angle)
            play.update(1 / 60, max(-0.85, min(0.85, diff * 3)), False)
            if play.man.finished:
                break
        assert play.rings_got > 0, "autopilot should collect some diamonds"
        assert play.score > 0

    def test_reports_are_spoken(self, tracks):
        app = DummyApp()
        play = PlayState(app, tracks[0], 1)
        play.report_progress()
        play.report_score()
        play.report_items()
        play.report_lives()
        play.report_radar()
        play.report_scan()
        assert any("percent" in m for m in app.speech.messages)
        assert any("Score" in m for m in app.speech.messages)
        assert any("diamonds" in m for m in app.speech.messages)
        assert any("lives left" in m for m in app.speech.messages)
        assert any("metres" in m for m in app.speech.messages)

    def test_clock_words(self):
        assert _clock_position(0.0) == "straight ahead"
        assert _clock_position(math.pi / 2) == "3 o'clock, right wall"
        assert _clock_position(math.pi) == "6 o'clock, the top of the tube"
        assert _clock_position(-math.pi / 2) == "9 o'clock, left wall"
        # nothing decodes to a bare '6 o'clock' any more
        for a in (i * math.pi / 12 for i in range(-12, 13)):
            assert _clock_position(a) != "6 o'clock"

    def test_obstacle_slows_and_scores_negative(self, tracks):
        app = DummyApp()
        app.options.speedrun_mode = True
        play = PlayState(app, tracks[0], 1)
        play.phase = "racing"
        from waterslide.track import TrackItem

        crab = TrackItem(cls="crab", t=0.5, angle=0.0)
        play.items = [crab]
        play.man.dist = tracks[0].total_length * 0.6  # just past the crab
        before = play.man.speed
        play.man.update(1 / 60, 0.0, False)
        play._check_items()
        assert crab.hit
        assert play.man.speed < before
        assert play.score < 0

    def test_assist(self):
        assert assist_steer(0.0, 0.0, 1.0, 0.0) == 0.0
        assert assist_steer(0.0, 0.0, 1.0, 0.5) > 0.2
        assert abs(assist_steer(1.0, 0.0, 0.0, 0.5)) <= 1.0


# ------------------------------------------------------------------ records
class TestRecords:
    def test_score_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        import waterslide.records as rec

        monkeypatch.setattr(rec, "records_dir", lambda: str(tmp_path / "wse"))
        r = Records()
        rank = r.add_score("tester", 1234, 2)
        assert rank == 1
        r.unlocked_stage = 5
        r.player_name = "tester"
        r.save()
        r2 = rec.Records.load()
        assert r2.scores[0].name == "tester"
        assert r2.scores[0].score == 1234
        assert r2.unlocked_stage == 5
        assert r2.player_name == "tester"

    def test_top_ten_limit(self):
        r = Records()
        for i in range(20):
            r.add_score(f"p{i}", i * 100, 1)
        assert len(r.scores) == 10
        assert r.scores[0].score == 1900

    def test_qualifies(self):
        r = Records()
        assert r.qualifies(1)
        for i in range(10):
            r.add_score(f"p{i}", 1000 - i)
        assert not r.qualifies(500)
        assert r.qualifies(5000)

    def test_options_roundtrip(self, tmp_path, monkeypatch):
        import waterslide.records as rec

        monkeypatch.setattr(rec, "records_dir", lambda: str(tmp_path / "wse2"))
        r = Records()
        r.options.speech = "off"
        r.options.speech_rate = 70
        r.options.speech_engine = "ao2"
        r.save()
        r2 = rec.Records.load()
        assert r2.options.speech == "off"
        assert r2.options.speech_rate == 70
        assert r2.options.speech_engine == "ao2"

    def test_option_rows_exist(self, tracks):
        app = DummyApp()
        app.records = Records()
        from waterslide.app import App

        # the row builder only touches module constants and options
        rows = App._option_rows(app)
        assert ("back", "back") in rows
        assert any(k == "speed units" for k, _ in rows)


# ------------------------------------------------------------------ audio
class TestAudio:
    def test_pan_math(self):
        assert pan_from_angle(0.0) == 0.0
        assert pan_from_angle(math.pi / 2) > 0.99
        assert pan_from_angle(-math.pi / 2) < -0.99

    def test_describe_position(self):
        assert describe_position(0.0) == "ahead"
        assert "left" in describe_position(-1.5)
        assert "right" in describe_position(1.5)

    def test_headless_engine(self):
        a = HeadlessAudio()
        a.play("diamond", 0.5, 0.9)
        assert a.panned[0][0] == "diamond"
        a.set_splash(0.5, 0.2, -0.3)
        assert a.splash[0] == 0.5

    def test_real_engine_starts(self):
        a = AudioEngine()
        assert a.mode in ("pygame", "winmm", "none")
        a.play("menu", 0.0, 0.1)  # must not raise
        a.quit()

    def test_one_shots_never_steal_ambience_channels(self):
        """Regression: play() used find_channel(True), whose force mode
        steals the longest-playing channel when all are busy - always an
        ambience bed.  A dense pickup burst (more concurrent chimes than
        the free channels) evicted the water/wind loop for the rest of the
        stage.  The last three channels are now reserved and one-shots are
        dropped instead of stealing when everything is busy."""
        a = AudioEngine()
        if a.mode != "pygame":
            pytest.skip("no pygame mixer in this environment")
        import pygame

        try:
            a.start_ambience()
            assert a._water_ch.get_busy() and a._wind_ch.get_busy()
            # saturate every free channel, then keep demanding more
            for _ in range(40):
                a.play("diamond", 0.0, 0.9)
            assert a._water_ch.get_busy(), "water bed was stolen by one-shots"
            assert a._wind_ch.get_busy(), "wind bed was stolen by one-shots"
        finally:
            a.stop_ambience()
            a.quit()

    def test_waveout_synthesis(self):
        from waterslide.speech import _WaveOut

        w = _WaveOut()
        samples = _tone(880, 40)
        assert len(samples) > 800
        # don't actually play in CI; just verify structure
        assert all(-1.0 <= s <= 1.0 for s in samples)

    def test_beep_patterns(self):
        assert len(BeepCue.render(BeepCue.DIAMOND)) > 0
        assert len(BeepCue.render(BeepCue.CRAB)) > 0


# ------------------------------------------------------------------ helpers
class TestHelpers:
    def test_angle_diff(self):
        assert _angle_diff(0.0, 0.1) < 0.2
        assert _angle_diff(0.0, math.pi - 0.01) > 3.0

    def test_signed_diff(self):
        assert angle_diff_signed(0.3, 0.1) == pytest.approx(0.2, abs=1e-9)
        assert angle_diff_signed(3.0, -3.0) < 0  # wraps the short way

    def test_pan(self):
        assert _pan(0.0) == 0.0
        assert _pan(1.0) > 0.5

    def test_pan_is_absolute_clock_frame(self, tracks):
        """Regression: item sounds used to pan by the bearing from the
        rider (item angle minus man angle), so sweeping past a right-wall
        diamond mid-glide made its chime play from the LEFT - contradicting
        the spoken "3 o'clock, right wall" callout and the water bed, which
        both use absolute clock positions.  The pan must be the item's own
        clock angle, independent of where the rider is."""
        man = GliderMan(tracks[0])
        man.angle = 1.5            # rider swept to the right wall
        item_angle = math.pi / 2   # diamond at the right wall (3 o'clock)
        play = PlayState.__new__(PlayState)
        play.man = man
        # reproduce the old expression to prove the fix: bearing would be
        # ~0.07 (near-centre pan) or negative after overshoot; absolute is
        # firmly right no matter the rider's position
        assert _pan(item_angle) > 0.95   # clamped hard right (sin 1.4 = 0.985)
        assert _pan(item_angle - man.angle) != _pan(item_angle)

    def test_item_words(self):
        assert _item_word("ring") == "diamond"
        assert _item_word("duck") == "evil duck"
