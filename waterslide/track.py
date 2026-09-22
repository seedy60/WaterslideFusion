"""Parser for the original ``.prt`` track files and the track spline.

``.prt`` layout (plaintext, CRLF, semicolon-terminated records):

* ``[meta]``   ``name,<text>`` / ``color0,r,g,b,a`` / ``color1,...`` /
  ``length,<int>`` (approximate arc length of the slide in world units).
* ``[profile]`` one or more cross-section rings, each a list of quads
  (``q,x0,y0,x1,y1;``) and texture quads (``T,...``).  Only the number of
  sections matters for gameplay: every spline handle stores an index into
  this list, selecting which cross-section (tube diameter) it uses.
* ``[handles]`` comma-separated integer rows of 15 columns each:

      col 0  sort/decoration key (unused for gameplay)
      col 1  engine altitude; in file order it drops from ~110k to ~10k on
             every stage, proving file order runs start (top) -> finish
      col 2-4   handle position (x, y, z)
      col 5-7   "in"  handle tangent (x, y, z)
      col 8-10  "out" handle tangent (x, y, z)
      col 11    unknown flags (1 = start area, 4 = normal)
      col 12    entry roll in degrees
      col 13    exit roll in degrees
      col 14    cross-section (``[profile]`` ring) index

  The original engine builds a cubic Hermite spline from these; we keep the
  same convention and reuse col 1 for slope-based gravity so the physics
  matches the original altitude profile regardless of axis conventions.
* ``[mbacs]``  decoration mesh names (city_level, ring, heart, skybox...).
* ``[thing]``  placed items: ``C,<class>;`` plus optional ``t/r/s`` vector
  rows and ``B,<int>``.  Rings (collectable diamonds in-game) carry no
  coordinates: the engine spaces them evenly along the spline, which
  ``Track.items`` reproduces.

The spline is evaluated with a cubic Hermite interpolation using the
original in/out tangents and a tension factor, matching
``PRSpline::getPosOnSpline`` / ``getMatrixonSpline`` behaviour closely
enough for authentic gameplay feel.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]
TAU = math.tau

# Item classes found in the original files, mapped to in-game meaning
# (names follow the original sound effects: Pick-Up-Diamond.wav etc).
CLASS_DIAMOND = "ring"
CLASS_HEART = "heart"
CLASS_STAR = "star"
CLASS_BOOST = "boost"
CLASS_CRAB = "crab"
CLASS_DUCK = "duck"
CLASS_DECO = "deco"

GOOD_CLASSES = frozenset({CLASS_DIAMOND, CLASS_HEART, CLASS_STAR, CLASS_BOOST})
BAD_CLASSES = frozenset({CLASS_CRAB, CLASS_DUCK})


# ------------------------------------------------------------------ helpers
def _wrap_angle(a: float) -> float:
    """Wrap an angle into [-pi, pi]."""
    return (a + math.pi) % TAU - math.pi


def _vec(s: str) -> Vec3:
    a, b, c = (s.split(",") + ["0", "0"])[:3]
    return (float(a), float(b), float(c))


@dataclass
class Handle:
    """One spline control row from ``[handles]``."""

    key: int            # col 0
    height: int         # col 1
    pos: Vec3           # col 2-4
    tin: Vec3           # col 5-7
    tout: Vec3          # col 8-10
    flags: int          # col 11
    roll_in: float      # col 12, degrees
    roll_out: float     # col 13, degrees
    profile: int        # col 14

    @property
    def is_loop(self) -> bool:
        return self.roll_in >= 90.0 or self.roll_out >= 90.0


@dataclass
class TrackItem:
    """A collectable or obstacle placed along the slide."""

    cls: str
    t: float            # 0..1 parametric position on the spline
    angle: float        # radians around the tube cross-section
    collected: bool = False
    hit: bool = False
    alive: bool = True


@dataclass
class Track:
    name: str
    colors: Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]
    declared_length: int
    handles: List[Handle]
    num_profiles: int
    deco: List[str]
    things: List[Dict[str, str]]
    items: List[TrackItem] = field(default_factory=list)

    # ------------------------------------------------------------------ parse
    @classmethod
    def from_text(cls, text: str, index: int = 1) -> "Track":
        parts = re.split(r"\[(\w+)\]", text)
        sections: Dict[str, List[str]] = {}
        for i in range(1, len(parts) - 1, 2):
            sections.setdefault(parts[i], []).append(parts[i + 1])

        meta: Dict[str, str] = {}
        for row in sections.get("meta", [""])[0].split(";"):
            row = row.strip()
            if "," in row:
                k, v = row.split(",", 1)
                meta[k.strip()] = v.strip()

        colors: List[Tuple[int, int, int, int]] = []
        for key in ("color0", "color1"):
            vals = [int(x) for x in meta.get(key, "160,200,255,0").split(",")]
            while len(vals) < 4:
                vals.append(0)
            colors.append(tuple(vals[:4]))  # type: ignore[arg-type]

        handles: List[Handle] = []
        for raw in re.split(r";", sections.get("handles", [""])[0]):
            row = raw.strip()
            if not row or not re.fullmatch(r"-?\d+(,-?\d+)*", row):
                continue
            c = [int(x) for x in row.split(",")]
            c += [0] * (15 - len(c))
            handles.append(
                Handle(
                    key=c[0],
                    height=c[1],
                    pos=(float(c[2]), float(c[3]), float(c[4])),
                    tin=(float(c[5]), float(c[6]), float(c[7])),
                    tout=(float(c[8]), float(c[9]), float(c[10])),
                    flags=c[11],
                    roll_in=float(c[12]),
                    roll_out=float(c[13]),
                    profile=c[14] if c[14] < len(sections.get("profile", [])) else 0,
                )
            )
        # File order is start (top of slide) -> finish: the per-handle
        # altitude column (col 1) drops from ~110k to ~10k in file order on
        # every original stage.  Keep order; col1 drives slope physics.

        deco = [r.strip() for r in sections.get("mbacs", [""])[0].split(";") if r.strip()]
        things: List[Dict[str, str]] = []
        for block in sections.get("thing", []):
            entries: Dict[str, str] = {}
            for row in block.split(";"):
                row = row.strip()
                if "," in row:
                    k, v = row.split(",", 1)
                    entries[k.strip()] = v.strip()
            if entries:
                things.append(entries)

        num_profiles = len(sections.get("profile", []))
        track = cls(
            name=meta.get("name", f"Stage {index}"),
            colors=(colors[0], colors[1]),  # type: ignore[arg-type]
            declared_length=int(meta.get("length", "0")),
            handles=handles,
            num_profiles=num_profiles,
            deco=deco,
            things=things,
        )
        track._apply_world_scale()
        track._build_items(index)
        track._precompute()
        return track

    def _apply_world_scale(self, target_length: float = 135000.0) -> None:
        """Normalize stage length so played durations land in the 1.5-2.5 min
        range while preserving each stage's unique shape.

        The raw splines vary wildly in winding (polyline vs altitude span
        ranges 1.9x-3.5x across the nine stages); the original engine's
        stages played at broadly similar durations, so we scale each track
        to a common arc length and let slope/speed profiles differentiate
        the feel."""
        # raw (unscaled) polyline length
        total = 0.0
        n = len(self.handles) - 1
        for i in range(n):
            total += self._estimate_segment_length(i)
        scale = target_length / total if total > 0 else 1.0
        for h in self.handles:
            h.pos = tuple(c * scale for c in h.pos)  # type: ignore[assignment]
            h.tin = tuple(c * scale for c in h.tin)  # type: ignore[assignment]
            h.tout = tuple(c * scale for c in h.tout)  # type: ignore[assignment]
            h.height *= scale

    @classmethod
    def from_file(cls, path: str, index: int = 1) -> "Track":
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            return cls.from_text(fh.read(), index)

    # ----------------------------------------------------------------- items
    def _build_items(self, index: int) -> None:
        """Place items along the spline.

        The original files only give the *count* of each class (one
        ``[thing]`` block per item).  Rings/diamonds are distributed evenly
        over the middle 90% of the slide; special items (heart/star/boost)
        and obstacles (crab/duck) are placed at evenly spaced distinctive
        positions, as in the original engine which scatters them along the
        spline.  Angles around the tube follow a deterministic pattern so
        the level layout is stable between runs (and learnable!).

        Every angle is folded into the lower half of the tube (|angle| <=
        90 degrees): steering tops out at about 100 degrees and the fall
        rim sits at 95, so anything higher - every heart used to be pinned
        to exactly 180 degrees, the top of the tube - was dead content no
        player could ever reach.
        """
        counts: Dict[str, int] = {}
        for thing in self.things:
            cls = thing.get("C", "")
            if cls in (CLASS_DECO, ""):
                continue
            counts[cls] = counts.get(cls, 0) + 1

        items: List[TrackItem] = []

        def spread(cls: str, n: int, lo: float, hi: float, phase: float = 0.0) -> None:
            if n <= 0:
                return
            span = (hi - lo) / n
            for k in range(n):
                t = lo + span * (k + 0.5)
                # deterministic pseudo-angle: cycles through the tube walls
        # Angles are only *generated* in multiples of 45 degrees; the fold
        # below maps every multiple of 45 onto a reachable multiple of 45
        # (pi -> 0, +/-3pi/4 -> -/+pi/4), so items keep a learnable layout
        # that is also collectible: see COLLECTIVITY in game.py.
                angle = ((k + phase) % 4) * (math.pi / 2) + (math.pi / 4 if (k // 4) % 2 else 0.0)
                # fold into the reachable half: wrap first (the pattern can
                # reach 7pi/4), then reflect anything past the walls back
                # down (pi -> 0, +/-3pi/4 -> -/+pi/4).  Without this the
                # heart (pinned to pi) and a slice of diamonds and
                # obstacles simply could never be touched.
                angle = _wrap_angle(angle)
                if angle > math.pi / 2:
                    angle = math.pi - angle
                elif angle < -math.pi / 2:
                    angle = -math.pi - angle
                items.append(TrackItem(cls=cls, t=t, angle=_wrap_angle(angle)))

        # diamonds: dense chain through the middle of the stage
        spread(CLASS_DIAMOND, counts.get(CLASS_DIAMOND, 0), 0.05, 0.95, phase=0.0)
        # obstacles interleaved between diamonds
        spread(CLASS_CRAB, counts.get(CLASS_CRAB, 0), 0.10, 0.90, phase=0.5)
        spread(CLASS_DUCK, counts.get(CLASS_DUCK, 0), 0.10, 0.90, phase=0.75)
        # specials
        spread(CLASS_BOOST, counts.get(CLASS_BOOST, 0), 0.08, 0.80, phase=0.25)
        n_heart = counts.get(CLASS_HEART, 0)
        for k in range(n_heart):
            # mid-slide, alternating wall by stage: reachable (angle pi was
            # the top of the tube - no rider could ever get there)
            wall = math.pi / 2 if index % 2 else -math.pi / 2
            items.append(TrackItem(CLASS_HEART, 0.5, wall))
        n_star = counts.get(CLASS_STAR, 0)
        if n_star:
            items.append(TrackItem(CLASS_STAR, 0.72, 0.0))

        # content-quality floor: the engine only places what the original
        # data declares, but a stage without an extra life or without a
        # god-mode star is missing designed pacing (stage 6 shipped with
        # no heart, stage 1 with no star) - add the missing special at a
        # deterministic, reachable position.
        if not any(it.cls == CLASS_HEART for it in items):
            wall = math.pi / 2 if (index + 1) % 2 else -math.pi / 2
            items.append(TrackItem(CLASS_HEART, 0.38, wall))
        if not any(it.cls == CLASS_STAR for it in items):
            items.append(TrackItem(CLASS_STAR, 0.62, 0.0))

        items.sort(key=lambda it: it.t)
        self.items = items

    # --------------------------------------------------------------- geometry
    def _precompute(self) -> None:
        self._seg_len: List[float] = []
        self._cum_len: List[float] = [0.0]
        n = len(self.handles) - 1
        for i in range(n):
            self._seg_len.append(self._estimate_segment_length(i))
            self._cum_len.append(self._cum_len[-1] + self._seg_len[-1])
        self.total_length = self._cum_len[-1]

    def _estimate_segment_length(self, i: int, samples: int = 8) -> float:
        prev = self._pos_on_segment(i, 0.0)
        total = 0.0
        for k in range(1, samples + 1):
            p = self._pos_on_segment(i, k / samples)
            total += math.dist(prev, p)
            prev = p
        return total

    @property
    def num_segments(self) -> int:
        return len(self.handles) - 1

    def tube_radius(self, seg: int) -> float:
        """World-space tube radius at a segment (from profile ring extents)."""
        # Original rings span roughly +/-2048 (full circle) down to +/-480
        # for the narrowest used profile.
        p = self.handles[max(0, min(seg, len(self.handles) - 1))].profile
        return 240.0 - 30.0 * min(p, 4)

    # ------------------------------------------------------------- splines
    def _pos_on_segment(self, i: int, u: float) -> Vec3:
        h0 = self.handles[i]
        h1 = self.handles[i + 1]
        return hermite_point(h0.pos, h0.tout, h1.pos, h1.tin, u)

    def pos_at(self, seg: int, u: float) -> Vec3:
        i = max(0, min(seg, self.num_segments - 1))
        u = min(max(u, 0.0), 1.0)
        return self._pos_on_segment(i, u)

    def tangent_at(self, seg: int, u: float) -> Vec3:
        i = max(0, min(seg, self.num_segments - 1))
        u = min(max(u, 0.0), 1.0)
        h0 = self.handles[i]
        h1 = self.handles[i + 1]
        d = hermite_tangent(h0.pos, h0.tout, h1.pos, h1.tin, u)
        n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) or 1.0
        return (d[0] / n, d[1] / n, d[2] / n)

    def roll_at(self, seg: int, u: float) -> float:
        """Roll angle in radians, interpolating handle entry/exit rolls."""
        i = max(0, min(seg, self.num_segments - 1))
        u = min(max(u, 0.0), 1.0)
        h0 = self.handles[i]
        h1 = self.handles[i + 1]
        a0 = math.radians(h0.roll_out if i > 0 else h0.roll_in)
        a1 = math.radians(h1.roll_in)
        # shortest-path interpolation; 180 vs 0 gives the loop roll
        d = (a1 - a0 + math.pi) % TAU - math.pi
        return a0 + d * u

    def profile_at(self, seg: int) -> int:
        return self.handles[max(0, min(seg, len(self.handles) - 1))].profile

    def height_at_distance(self, dist: float) -> float:
        """Engine altitude (col 1) at an arc-length distance (linear interp)."""
        seg, u = self.param_from_distance(dist)
        h0 = self.handles[seg].height
        h1 = self.handles[min(seg + 1, len(self.handles) - 1)].height
        return h0 + (h1 - h0) * u

    def param_from_distance(self, dist: float) -> Tuple[int, float]:
        """Map arc-length distance to (segment, u)."""
        target = min(max(dist, 0.0), self.total_length)
        lo, hi = 0, self.num_segments - 1
        # cumulative lengths are small (<= a few hundred entries): linear scan ok
        for i in range(hi + 1):
            if self._cum_len[i + 1] >= target or i == hi:
                seg = i
                break
        else:  # pragma: no cover
            seg = hi
        seg_len = self._seg_len[seg] or 1e-6
        u = (target - self._cum_len[seg]) / seg_len
        return seg, min(max(u, 0.0), 1.0)

    def distance_from_param(self, seg: int, u: float) -> float:
        return self._cum_len[seg] + self._seg_len[seg] * u

    def pos_at_distance(self, dist: float) -> Vec3:
        seg, u = self.param_from_distance(dist)
        return self.pos_at(seg, u)

    def frame_at_distance(self, dist: float) -> Tuple[Vec3, Vec3, Vec3, Vec3]:
        """Return (position, forward, right, up) at an arc-length distance."""
        seg, u = self.param_from_distance(dist)
        pos = self.pos_at(seg, u)
        fwd = self.tangent_at(seg, u)
        roll = self.roll_at(seg, u)
        right, up = build_frame(fwd, roll)
        return pos, fwd, right, up

    def nearest_param(self, p: Vec3, seg_hint: Optional[int] = None) -> Tuple[int, float, float]:
        """Nearest (segment, u, distance) on the spline to point ``p``."""
        best = (0, 0.0, float("inf"))
        rng = range(self.num_segments)
        if seg_hint is not None:
            lo = max(0, seg_hint - 2)
            hi = min(self.num_segments - 1, seg_hint + 2)
            rng = range(lo, hi + 1)
        for i in rng:
            prev = self._pos_on_segment(i, 0.0)
            for k in range(1, 9):
                q = self._pos_on_segment(i, k / 8)
                # point-segment distance
                dx, dy, dz = q[0] - prev[0], q[1] - prev[1], q[2] - prev[2]
                wx, wy, wz = p[0] - prev[0], p[1] - prev[1], p[2] - prev[2]
                ll = dx * dx + dy * dy + dz * dz or 1e-9
                s = min(max((wx * dx + wy * dy + wz * dz) / ll, 0.0), 1.0)
                ex, ey, ez = prev[0] + dx * s, prev[1] + dy * s, prev[2] + dz * s
                d = math.dist(p, (ex, ey, ez))
                if d < best[2]:
                    uu = (k - 1 + s) / 8
                    best = (i, uu, d)
                prev = q
        return best

    # -------------------------------------------------------------- helpers
    def length_for_stage(self) -> float:
        return self.total_length


# --------------------------------------------------------------------- math
def hermite_point(p0: Vec3, m0: Vec3, p1: Vec3, m1: Vec3, u: float) -> Vec3:
    """Cubic Hermite point with the original 0.5x tangent scaling."""
    u2, u3 = u * u, u * u * u
    h00 = 2 * u3 - 3 * u2 + 1
    h10 = u3 - 2 * u2 + u
    h01 = -2 * u3 + 3 * u2
    h11 = u3 - u2
    return tuple(  # type: ignore[return-value]
        h00 * p0[k] + h10 * 0.5 * m0[k] + h01 * p1[k] + h11 * 0.5 * m1[k] for k in range(3)
    )


def hermite_tangent(p0: Vec3, m0: Vec3, p1: Vec3, m1: Vec3, u: float) -> Vec3:
    u2 = u * u
    d00 = 6 * u2 - 6 * u
    d10 = 3 * u2 - 4 * u + 1
    d01 = -6 * u2 + 6 * u
    d11 = 3 * u2 - 2 * u
    return tuple(  # type: ignore[return-value]
        d00 * p0[k] + d10 * 0.5 * m0[k] + d01 * p1[k] + d11 * 0.5 * m1[k] for k in range(3)
    )


def build_frame(fwd: Vec3, roll: float) -> Tuple[Vec3, Vec3]:
    """Build a stable (right, up) frame around ``fwd`` with roll applied."""
    world_up = (0.0, 1.0, 0.0)
    r0 = cross(fwd, world_up)
    ln = math.sqrt(r0[0] ** 2 + r0[1] ** 2 + r0[2] ** 2)
    if ln < 1e-6:  # straight up/down: fall back to a horizontal axis
        r0 = (1.0, 0.0, 0.0)
    else:
        r0 = (r0[0] / ln, r0[1] / ln, r0[2] / ln)
    up0 = cross(r0, fwd)
    cosr, sinr = math.cos(roll), math.sin(roll)
    right = tuple(r0[k] * cosr + up0[k] * sinr for k in range(3))
    up = tuple(up0[k] * cosr - r0[k] * sinr for k in range(3))
    return right, up  # type: ignore[return-value]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


# ------------------------------------------------------------------ loading
def find_level_dir() -> str:
    """Locate the extracted level directory ('assets/content/level')."""
    from .paths import assets_root

    return os.path.join(assets_root(), "content", "level")


def load_all_tracks(level_dir: Optional[str] = None) -> List[Track]:
    d = level_dir or find_level_dir()
    tracks: List[Track] = []
    for i in range(1, 10):
        p = os.path.join(d, f"track_0{i}.prt")
        if os.path.exists(p):
            tracks.append(Track.from_file(p, i))
    return tracks
