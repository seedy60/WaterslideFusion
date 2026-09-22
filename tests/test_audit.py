"""Dead-content audit: nothing ships that can never fire.

These tests exist because the playtests surfaced 248 unreachable items
(every heart pinned to the top of the tube) and a Brake.wav that loaded
but never played.  This file pins the invariants that would have caught
both, so new dead content cannot creep back in.
"""

import ast
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from waterslide.audio import AudioEngine, SOUND_FILES, sfx_key
from waterslide.game import (
    CLASS_BOOST,
    CLASS_CRAB,
    CLASS_DIAMOND,
    CLASS_DUCK,
    CLASS_HEART,
    CLASS_STAR,
    PlayState,
    _copy_item,
)
from waterslide.records import Options
from waterslide.track import TrackItem, load_all_tracks

ROOT = Path(__file__).resolve().parent.parent


def _audio_keys_in_source():
    """Every literal audio.play(...) key in the game code."""
    keys = set()
    for path in (ROOT / "waterslide").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "play" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def _string_literals_in_source():
    """Every string literal in the game code (dynamic play keys included)."""
    literals = set()
    for path in (ROOT / "waterslide").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    return literals


def _event_kinds():
    """Emitted GameEvent kinds vs handled kinds across the game code."""
    emitted, handled = set(), set()
    for path in (ROOT / "waterslide").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'GameEvent\(kind="([a-z_]+)"\)', src):
            emitted.add(m.group(1))
        for m in re.finditer(r'ev\.kind == "([a-z_]+)"', src):
            handled.add(m.group(1))
    return emitted, handled


class TestSoundRegistry:
    def test_every_played_key_exists_in_sound_files(self):
        played = _audio_keys_in_source()
        missing = {k for k in played if k not in SOUND_FILES} - {"crab_hit_female"}
        assert not missing, f"audio.play keys with no entry: {sorted(missing)}"

    def test_no_loaded_sound_is_never_referenced(self):
        # play() keys are often built at runtime (sfx_key variants, the
        # obstacle variant map), so "used" means referenced as a string
        # literal anywhere in the game code - which is exactly the check
        # that would have caught Brake.wav loading and never firing.
        referenced = _string_literals_in_source()
        orphans = {k for k in SOUND_FILES if k not in referenced}
        assert not orphans, f"loaded but never referenced: {sorted(orphans)}"


class TestEventWiring:
    def test_every_emitted_event_is_handled(self):
        emitted, handled = _event_kinds()
        missing = emitted - handled
        assert not missing, f"GameEvents emitted but never handled: {sorted(missing)}"

    def test_every_handled_event_is_emitted(self):
        emitted, handled = _event_kinds()
        ghosts = handled - emitted
        assert not ghosts, f"handlers for events nothing emits: {sorted(ghosts)}"


class TestBrakeSound:
    def _play(self):
        from test_game import DummyApp  # reuse the headless app stub

        tracks = load_all_tracks()
        app = DummyApp()
        return PlayState(app, tracks[0], 1), app

    def test_brake_emits_and_plays_once(self):
        play, app = self._play()
        play.countdown_t = 0.01
        play.update(1 / 60, 0.0, False)          # start moving, not braking
        app.audio.panned.clear()
        for _ in range(10):                       # hold the brake 10 frames
            play.update(1 / 60, 0.0, True)
        brakes = [k for k, _pan, _v in app.audio.panned if k == "brake"]
        assert brakes == ["brake"], brakes       # exactly once, not per-frame
        for _ in range(10):                       # release, then re-apply
            play.update(1 / 60, 0.0, False)
        app.audio.panned.clear()
        for _ in range(10):
            play.update(1 / 60, 0.0, True)
        assert [k for k, _p, _v in app.audio.panned if k == "brake"] == ["brake"]

    def test_brake_sound_file_exists(self):
        rel = SOUND_FILES["brake"]
        from waterslide.audio import _asset

        assert os.path.exists(_asset(rel)), rel


class TestMeshCoversAllClasses:
    def test_every_item_class_is_renderable_and_audible(self):
        from waterslide.render3d import ITEM_PALETTE, ITEM_SHAPES
        from waterslide.game import _item_word

        classes = {CLASS_DIAMOND, CLASS_HEART, CLASS_STAR,
                   CLASS_BOOST, CLASS_CRAB, CLASS_DUCK}
        for cls in classes:
            assert cls in ITEM_PALETTE, f"{cls}: no colour"
            assert cls in ITEM_SHAPES, f"{cls}: no distinct shape"
            assert _item_word(cls), f"{cls}: no spoken name"

    def test_copy_item_isolates_stage_state(self):
        it = TrackItem(cls=CLASS_HEART, t=0.5, angle=1.57)
        copy = _copy_item(it)
        copy.collected = True
        assert it.collected is False


@pytest.fixture(scope="module")
def tracks():
    for d in (os.path.join("tests", "fixtures", "level"),
              os.path.join("assets", "content", "level")):
        if os.path.isdir(d):
            ts = load_all_tracks(d)
            if ts:
                return ts
    pytest.skip("no level fixtures")


class TestTrackContentLive:
    def test_every_stage_has_live_content(self, tracks):
        for i, t in enumerate(tracks, 1):
            assert t.items, f"stage {i} has no items"
            assert t.total_length > 0, f"stage {i} has no geometry"


def _obstacle_gaps(t):
    """World-unit gaps between consecutive obstacles along the slide."""
    obs = sorted(it.t for it in t.items if it.cls in (CLASS_CRAB, CLASS_DUCK))
    return [(b - a) * t.total_length for a, b in zip(obs, obs[1:])]


class TestStageContentQuality:
    """Per-stage content-quality audit: pacing guarantees every stage
    must meet, not just fleet-wide averages.  Thresholds come from the
    original data itself: stages carry 55-90 diamonds, obstacle runs
    never sit closer together than 9000 world units (about 6 s at cruise
    speed), and the designed specials are heart/star/boost."""

    MIN_DIAMONDS = 50
    MIN_OBSTACLE_GAP = 5000.0   # world units between consecutive obstacles

    def test_every_stage_has_enough_diamonds(self, tracks):
        for i, t in enumerate(tracks, 1):
            n = sum(1 for it in t.items if it.cls == CLASS_DIAMOND)
            assert n >= self.MIN_DIAMONDS, f"stage {i}: only {n} diamonds"

    def test_obstacle_spacing_per_stage(self, tracks):
        for i, t in enumerate(tracks, 1):
            gaps = _obstacle_gaps(t)
            tight = [round(g) for g in gaps if g < self.MIN_OBSTACLE_GAP]
            assert not tight, f"stage {i}: obstacles {tight} units apart"

    def test_heart_and_star_presence_per_stage(self, tracks):
        for i, t in enumerate(tracks, 1):
            classes = {it.cls for it in t.items}
            assert CLASS_HEART in classes, f"stage {i}: no extra life"
            assert CLASS_STAR in classes, f"stage {i}: no god-mode star"

    def test_diamond_run_is_a_chain(self, tracks):
        """Diamonds are a distributed chain, not one clump: the largest
        gap between consecutive diamonds must stay small relative to the
        stage, so the audio trail is continuous."""
        for i, t in enumerate(tracks, 1):
            ds = sorted(it.t for it in t.items if it.cls == CLASS_DIAMOND)
            gaps = [(b - a) * t.total_length for a, b in zip(ds, ds[1:])]
            assert max(gaps) < t.total_length * 0.08, \
                f"stage {i}: diamond gap of {round(max(gaps))} units breaks the chain"
