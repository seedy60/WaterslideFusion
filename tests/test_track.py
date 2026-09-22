"""Tests for the .prt track parser and spline math (original levels)."""

import math
import os

import pytest

from waterslide.track import (
    CLASS_BOOST,
    CLASS_CRAB,
    CLASS_DIAMOND,
    CLASS_DUCK,
    CLASS_HEART,
    CLASS_STAR,
    Track,
    build_frame,
    hermite_point,
    load_all_tracks,
)

LEVEL_DIRS = [
    os.path.join("tests", "fixtures", "level"),
    os.path.join("assets", "content", "level"),
]


def _level_dir():
    for d in LEVEL_DIRS:
        if os.path.isdir(d):
            return d
    return None


@pytest.fixture(scope="module")
def tracks():
    d = _level_dir()
    if d is None:
        pytest.skip("no level fixtures; run python -m waterslide.ipa_extract")
    ts = load_all_tracks(d)
    assert ts, "no tracks parsed"
    return ts


class TestParser:
    def test_nine_stages(self, tracks):
        assert len(tracks) == 9

    def test_meta(self, tracks):
        t = tracks[0]
        assert t.declared_length > 0
        assert t.colors[0] != t.colors[1]
        assert len(t.colors[0]) == 4

    def test_handles_run_start_to_finish(self, tracks):
        # Verified against the original data: the engine altitude column
        # (col 1) starts ~110k and ends ~10-25k in file order on every
        # stage.  Small uphill bumps are legitimate terrain, so we check
        # the dominant downward trend rather than strict monotonicity.
        for t in tracks:
            heights = [h.height for h in t.handles]
            assert heights[0] > heights[-1] * 2, "slide must start far above the finish"
            drops = sum(1 for a, b in zip(heights, heights[1:]) if b < a)
            assert drops >= len(heights) * 0.8, "altitude should trend downward"

    def test_profiles_range(self, tracks):
        for t in tracks:
            for h in t.handles:
                assert 0 <= h.profile < max(1, t.num_profiles)

    def test_items_placed(self, tracks):
        for i, t in enumerate(tracks, 1):
            assert t.items, f"stage {i} has no items"
            classes = {it.cls for it in t.items}
            assert CLASS_DIAMOND in classes
            # hearts and stars exist on all original stages
            assert CLASS_HEART in classes or CLASS_STAR in classes

    def test_items_sorted_and_inside(self, tracks):
        for t in tracks:
            ts = [it.t for it in t.items]
            assert ts == sorted(ts)
            for x in ts:
                assert 0.0 <= x <= 1.0

    def test_deco_meshes(self, tracks):
        t = tracks[0]
        assert "city_level" in t.deco
        assert "skybox" in t.deco


class TestSpline:
    def test_hermite_endpoints(self):
        p = hermite_point((0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 0, 0), 0.0)
        assert p == (0, 0, 0)
        p = hermite_point((0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 0, 0), 1.0)
        assert p == (10, 0, 0)

    def test_hermite_midpoint_bias(self):
        # mid point should lean along the tangent
        p = hermite_point((0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 0, 0), 0.5)
        assert 3.0 < p[0] < 7.0

    def test_length_consistency(self, tracks):
        for t in tracks:
            assert t.total_length > 0
            # arc length is the same regardless of sampling direction
            seg, u = t.param_from_distance(t.total_length)
            assert seg == t.num_segments - 1 or seg == t.num_segments
            seg0, u0 = t.param_from_distance(0.0)
            assert seg0 == 0 and u0 == 0.0

    def test_param_roundtrip(self, tracks):
        t = tracks[0]
        for d in (0.0, 100.0, t.total_length / 2, t.total_length - 1, t.total_length):
            seg, u = t.param_from_distance(d)
            back = t.distance_from_param(seg, u)
            assert math.isclose(back, min(d, t.total_length), rel_tol=0.02, abs_tol=1.0)

    def test_frame_orthonormal(self, tracks):
        t = tracks[0]
        for d in (0.0, t.total_length * 0.25, t.total_length * 0.5, t.total_length * 0.9):
            pos, fwd, right, up = t.frame_at_distance(d)
            assert math.isclose(math.dist(fwd, (0, 0, 0)), 1.0, abs_tol=1e-6)
            dot_rr = sum(right[k] * up[k] for k in range(3))
            dot_rf = sum(right[k] * fwd[k] for k in range(3))
            assert abs(dot_rr) < 1e-6, "right/up must be orthogonal"
            assert abs(dot_rf) < 1e-6, "right/fwd must be orthogonal"

    def test_rolls_reach_loop_angles(self, tracks):
        # the original data encodes loop-the-loops with 180-degree rolls
        found = False
        for t in tracks:
            for h in t.handles:
                if h.roll_in == 180 or h.roll_out == 180:
                    found = True
                    break
            if found:
                break
        assert found, "expected at least one 180-degree roll (loop-the-loop)"

    def test_nearest_param_finds_track_point(self, tracks):
        t = tracks[0]
        for d in (500.0, t.total_length * 0.4, t.total_length * 0.8):
            pos, *_ = t.frame_at_distance(d)
            seg, u, dist = t.nearest_param(pos)
            # tolerance: chord sampling sag between spline samples
            assert dist < 60.0, f"nearest_param should find the spline point at {d}"


class TestItems:
    def test_item_angle_ranges(self, tracks):
        for t in tracks:
            for it in t.items:
                assert -math.pi - 1e-9 <= it.angle <= math.pi + 1e-9

    def test_obstacles_present_on_later_stages(self, tracks):
        classes = set()
        for t in tracks[1:]:
            classes |= {it.cls for it in t.items}
        assert classes & {CLASS_CRAB, CLASS_DUCK}

    def test_specials(self, tracks):
        all_classes = set()
        for t in tracks:
            all_classes |= {it.cls for it in t.items}
        assert CLASS_BOOST in all_classes
        assert CLASS_HEART in all_classes
        assert CLASS_STAR in all_classes

    def test_all_items_reachable(self, tracks):
        """Regression: every heart used to be pinned to angle pi (the top
        of the tube) and the deterministic angle pattern put a slice of
        diamonds and obstacles past the walls - 248 items no player could
        ever touch, because steering tops out at ~100 degrees and the fall
        rim sits at 95.  Every item must sit inside the reach arc."""
        from waterslide.game import MAX_STEER

        contact = 0.55  # the widest collection window in game.py
        for t in tracks:
            for it in t.items:
                assert abs(it.angle) <= MAX_STEER + contact + 1e-9, \
                    (it.cls, it.angle)

    def test_hearts_are_pickable_wall_items(self, tracks):
        # placed hearts sit on a wall (alternating per stage); the
        # content-quality floor may add one where the original data had
        # none (stage 6), at a nearby mid-slide position
        for t in tracks:
            for it in t.items:
                if it.cls == CLASS_HEART:
                    assert abs(abs(it.angle) - math.pi / 2) < 1e-9, it.angle
                    assert 0.3 <= it.t <= 0.6, it.t
