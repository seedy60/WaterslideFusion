"""Opt-in 3D view over the original track data (for sighted co-op players).

The game itself remains audio-first; this renderer adds a windowed 3D
picture of the *original* spline geometry.  Enable with ``--3d``; if a GL
context cannot be created the game silently stays in 2D.

The mesh is built straight from :class:`waterslide.track.Track`: ring
cross-sections swept along the spline using each handle's roll, striped
with the track's original ``color0``/``color1``, plus item markers and a
finish ring.  Text (menus, HUD) is drawn as textured quads so the whole
UI works in one GL window.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from .track import Track, TrackItem, build_frame

RING_SEGMENTS = 24      # cross-section resolution
SAMPLES = 900           # rings along the whole track
ITEM_SEGMENTS = 8


# ------------------------------------------------------------------ meshes
def build_track_mesh(track: Track) -> Tuple[np.ndarray, np.ndarray]:
    """Return (vertices, indices) for the whole tube.

    Vertices: x, y, z, r, g, b.  Shading is baked per-vertex from the ring
    angle so the tube reads as 3D without a lighting pass.
    """
    positions: List[Tuple[float, float, float]] = []
    frames: List[Tuple[Tuple[float, float, float], ...]] = []
    radii: List[float] = []
    for i in range(SAMPLES):
        d = track.total_length * i / (SAMPLES - 1)
        seg, u = track.param_from_distance(d)
        pos = track.pos_at(seg, u)
        fwd = track.tangent_at(seg, u)
        roll = track.roll_at(seg, u)
        right, up = build_frame(fwd, roll)
        positions.append(pos)
        frames.append((fwd, right, up))
        radii.append(track.tube_radius(seg))

    c0 = [v / 255.0 for v in track.colors[0][:3]]
    c1 = [v / 255.0 for v in track.colors[1][:3]]

    verts = np.zeros((SAMPLES * (RING_SEGMENTS + 1), 6), dtype=np.float32)
    v = 0
    for i in range(SAMPLES):
        pos = positions[i]
        _fwd, right, up = frames[i]
        r = radii[i]
        stripe = ((i // 5) % 2) == 0
        base = c0 if stripe else c1
        for k in range(RING_SEGMENTS + 1):
            ang = math.tau * k / RING_SEGMENTS
            ca, sa = math.cos(ang), math.sin(ang)
            x = pos[0] + (ca * right[0] + sa * up[0]) * r
            y = pos[1] + (ca * right[1] + sa * up[1]) * r
            z = pos[2] + (ca * right[2] + sa * up[2]) * r
            # fake tube shading: brightest near the top, darker at the bottom
            shade = 0.55 + 0.45 * max(0.0, -math.cos(ang))
            verts[v] = (x, y, z, base[0] * shade, base[1] * shade, base[2] * shade)
            v += 1

    row = RING_SEGMENTS + 1
    idx = np.zeros(((SAMPLES - 1) * RING_SEGMENTS * 6), dtype=np.uint32)
    t = 0
    for i in range(SAMPLES - 1):
        for k in range(RING_SEGMENTS):
            a = i * row + k
            b = a + 1
            c = a + row
            d2 = c + 1
            idx[t:t + 6] = (a, c, b, b, c, d2)
            t += 6
    return verts, idx


def build_ring_marker(track: Track, distance: float, color: Tuple[float, float, float],
                      scale: float = 1.05) -> Tuple[np.ndarray, np.ndarray]:
    """A bright ring at one arc-length position (start gate / finish line)."""
    seg, u = track.param_from_distance(distance)
    pos = track.pos_at(seg, u)
    fwd = track.tangent_at(seg, u)
    from .track import build_frame

    right, up = build_frame(fwd, track.roll_at(seg, u))
    r = track.tube_radius(seg) * scale
    verts = np.zeros(((RING_SEGMENTS + 1), 6), dtype=np.float32)
    for k in range(RING_SEGMENTS + 1):
        ang = math.tau * k / RING_SEGMENTS
        ca, sa = math.cos(ang), math.sin(ang)
        verts[k] = (
            pos[0] + (ca * right[0] + sa * up[0]) * r,
            pos[1] + (ca * right[1] + sa * up[1]) * r,
            pos[2] + (ca * right[2] + sa * up[2]) * r,
            color[0], color[1], color[2],
        )
    idx = np.zeros((RING_SEGMENTS - 2) * 3, dtype=np.uint32)
    t = 0
    for k in range(1, RING_SEGMENTS - 1):
        idx[t:t + 3] = (0, k, k + 1)
        t += 3
    return verts, idx[:t]


# Item colours, Okabe-Ito colourblind-safe values: the universal design
# palette stays distinguishable under deuteranopia and protanopia (the
# common red/green confusions) because hues were chosen on blue-yellow
# and light-dark axes.  Members never sit adjacent in those spaces:
# good/blue sky, vermillion/orange, yellow, bluish green, reddish
# purple, and sky blue for the collectible diamonds.
ITEM_PALETTE = {
    "ring": (0.00, 0.45, 0.70),   # blue: the diamonds you collect
    "heart": (0.00, 0.62, 0.45),  # bluish green: extra life, far from crab red
    "star": (0.94, 0.89, 0.26),   # yellow: god mode, brightest marker
    "boost": (0.34, 0.71, 0.91),  # sky blue: speed pad
    "crab": (0.90, 0.63, 0.00),   # orange: the hazard, dark enough to warn
    "duck": (0.49, 0.18, 0.56),   # reddish purple: the squash bonus
}


# ------------------------------------------------------------ marker shapes
# Every item class also has its own GEOMETRY, so markers stay identifiable
# with no colour vision at all: colour, silhouette and vertex count differ
# per class (diamond 6v, star 11v, spiky crab 18v, health cross 26v,
# pyramid 5v, slab 8v but elongated).  Shapes are built in the tube's
# local frame so stars and crabs face the rider and the boost pad lies
# flat on the slide.


def _add(c, d):
    return (c[0] + d[0], c[1] + d[1], c[2] + d[2])


def _combine(right, ar, up, au, fwd, af):
    return tuple(right[k] * ar + up[k] * au + fwd[k] * af for k in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _shape_diamond(c, s, right, up, fwd):
    """Octahedron: the diamond, unchanged from the original marker."""
    pts = [_add(c, _combine(right, s, up, 0, fwd, 0)),
           _add(c, _combine(right, -s, up, 0, fwd, 0)),
           _add(c, _combine(right, 0, up, s, fwd, 0)),
           _add(c, _combine(right, 0, up, -s, fwd, 0)),
           _add(c, _combine(right, 0, up, 0, fwd, s)),
           _add(c, _combine(right, 0, up, 0, fwd, -s))]
    tris = ((0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4),
            (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5))
    return pts, tris


def _shape_star(c, s, right, up, fwd):
    """Five-point star in the cross-section plane, point up: the god mode
    pickup, unmistakably a star from the rider's view."""
    pts = [tuple(c)]
    for k in range(10):
        ang = math.pi / 2 + math.tau * k / 10
        rad = s if k % 2 == 0 else s * 0.42
        pts.append(_add(c, _combine(right, math.cos(ang) * rad,
                                    up, math.sin(ang) * rad, fwd, 0.0)))
    tris = tuple((0, 1 + k, 1 + (k + 1) % 10) for k in range(10))
    return pts, tris


def _shape_crab(c, s, right, up, fwd):
    """A spiky X: small core plus four tetra spikes on the cross-section
    diagonals.  Spikes say hazard from any angle."""
    pts, tris = _shape_diamond(c, s * 0.30, right, up, fwd)
    for ang in (math.pi / 4, 3 * math.pi / 4, 5 * math.pi / 4, 7 * math.pi / 4):
        d = _combine(right, math.cos(ang), up, math.sin(ang), fwd, 0.0)
        perp = _cross(d, fwd)
        apex = _add(c, _combine(right, math.cos(ang) * s,
                                up, math.sin(ang) * s, fwd, 0.0))
        bc = _add(c, _combine(right, math.cos(ang) * s * 0.30,
                              up, math.sin(ang) * s * 0.30, fwd, 0.0))
        base = len(pts)
        for j in range(3):
            a2 = math.tau * j / 3
            pts.append(_add(bc, _combine(fwd, math.cos(a2) * s * 0.20,
                                         perp, math.sin(a2) * s * 0.20,
                                         right, 0.0)))
        tris = tris + ((base, base + 1, base + 2),
                       (base, base + 1, base + 3),
                       (base + 1, base + 2, base + 3),
                       (base + 2, base, base + 3))
    return pts, tris


def _box(c, er, eu, ef, right, up, fwd):
    """Axis-aligned box in the tube frame; extents per local axis."""
    pts = []
    for a in (-1.0, 1.0):
        for b in (-1.0, 1.0):
            for cc in (-1.0, 1.0):
                pts.append(_add(c, _combine(right, a * er, up, b * eu, fwd, cc * ef)))
    # corner index bit layout: 4=a, 2=b, 1=c (a/b/c in {-1,+1})
    tris = ((0, 1, 3), (0, 3, 2), (4, 7, 5), (4, 6, 7),
            (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
            (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3))
    return pts, tris


def _shape_heart(c, s, right, up, fwd):
    """Health cross: a plus sign in the cross-section plane, extruded.
    Reads as 'extra life' from any angle and shares no geometry with the
    other markers."""
    a, b = s * 0.75, s * 0.30          # half-length, half-thickness
    depth = s * 0.30                    # extrusion along the slide
    outline = [(b, b), (b, a), (-b, a), (-b, b), (-a, b), (-a, -b),
               (-b, -b), (-b, -a), (b, -a), (b, -b), (a, -b), (a, b)]
    pts = []
    for layer in (-depth, depth):
        for ru in outline:
            pts.append(_add(c, _combine(right, ru[0], up, ru[1], fwd, layer)))
    pts.append(_add(c, _combine(right, 0, up, 0, fwd, -depth)))
    pts.append(_add(c, _combine(right, 0, up, 0, fwd, depth)))
    n = len(outline)
    tris = []
    for k in range(n):                                    # two caps (fans)
        k2 = (k + 1) % n
        tris += ((2 * n, 2 * k, 2 * k2), (2 * n + 1, 2 * k2 + 1, 2 * k + 1))
    for k in range(n):                                    # side walls
        k2 = (k + 1) % n
        tris += ((2 * k, 2 * k2, 2 * k2 + 1), (2 * k, 2 * k2 + 1, 2 * k + 1))
    return pts, tuple(tris)


def _shape_duck(c, s, right, up, fwd):
    """Squat pyramid, apex up: the duck."""
    bc = _add(c, _combine(right, 0, up, -s * 0.35, fwd, 0.0))
    pts = [_add(bc, _combine(right, -s * 0.55, up, -s * 0.55, fwd, 0.0)),
           _add(bc, _combine(right, s * 0.55, up, -s * 0.55, fwd, 0.0)),
           _add(bc, _combine(right, s * 0.55, up, s * 0.55, fwd, 0.0)),
           _add(bc, _combine(right, -s * 0.55, up, s * 0.55, fwd, 0.0)),
           _add(c, _combine(right, 0, up, s, fwd, 0.0))]
    tris = ((0, 1, 2), (0, 2, 3),
            (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4))
    return pts, tris


def _shape_boost(c, s, right, up, fwd):
    """Flat wide slab lying on the slide: the boost pad."""
    return _box(c, s, s * 0.22, s * 1.15, right, up, fwd)


ITEM_SHAPES = {
    "ring": (_shape_diamond, 26.0),
    "star": (_shape_star, 34.0),
    "crab": (_shape_crab, 30.0),
    "heart": (_shape_heart, 26.0),
    "duck": (_shape_duck, 30.0),
    "boost": (_shape_boost, 28.0),
}


def build_item_markers(track: Track, items: List[TrackItem]) -> Tuple[np.ndarray, np.ndarray]:
    """A distinct 3D marker per item class, colour-coded and shaped."""
    palette = ITEM_PALETTE
    verts_list: List[np.ndarray] = []
    idx_list: List[np.ndarray] = []
    total = track.total_length
    for it in items:
        if it.collected or it.hit:
            continue
        d = it.t * total
        seg, u = track.param_from_distance(d)
        pos = track.pos_at(seg, u)
        fwd = track.tangent_at(seg, u)
        right, up = build_frame(fwd, track.roll_at(seg, u))
        r = track.tube_radius(seg)
        ang = it.angle
        center = tuple(
            pos[k] + (math.cos(ang) * right[k] + math.sin(ang) * up[k]) * r * 0.7
            for k in range(3)
        )
        col = palette.get(it.cls, (1.0, 1.0, 1.0))
        builder, s = ITEM_SHAPES.get(it.cls, (_shape_diamond, 26.0))
        pts, tris = builder(center, s, right, up, fwd)
        base = len(verts_list)
        for p in pts:
            verts_list.append((p[0], p[1], p[2], *col))
        for tri in tris:
            idx_list.append((base + tri[0], base + tri[1], base + tri[2]))
    if not verts_list:
        return np.zeros((0, 6), dtype=np.float32), np.zeros((0,), dtype=np.uint32)
    return (np.array(verts_list, dtype=np.float32),
            np.array(idx_list, dtype=np.uint32).ravel())


# ------------------------------------------------------------------- math
def perspective(fovy: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fovy) / 2.0)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def look_at(eye, center, up) -> np.ndarray:
    fwd = np.array(center, dtype=np.float32) - np.array(eye, dtype=np.float32)
    ln = np.linalg.norm(fwd)
    if ln < 1e-6:
        fwd = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    else:
        fwd = fwd / ln
    side = np.cross(fwd, np.array(up, dtype=np.float32))
    if np.linalg.norm(side) < 1e-6:
        side = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    else:
        side = side / np.linalg.norm(side)
    up2 = np.cross(side, fwd)
    m = np.eye(4, dtype=np.float32)
    m[0, :3], m[1, :3], m[2, :3] = side, up2, -fwd
    m[0, 3] = -np.dot(side, eye)
    m[1, 3] = -np.dot(up2, eye)
    m[2, 3] = np.dot(fwd, eye)
    return m


# ---------------------------------------------------------------- renderer
VERTEX_SRC = """
#version 130
in vec3 in_position;
in vec3 in_color;
uniform mat4 mvp;
out vec3 v_color;
out float v_dist;
uniform vec3 cam_pos;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
    v_color = in_color;
    v_dist = length(in_position - cam_pos);
}
"""

FRAGMENT_SRC = """
#version 130
in vec3 v_color;
in float v_dist;
out vec4 out_color;
uniform vec3 fog_color;
uniform float fog_end;
void main() {
    float fog = clamp(v_dist / max(fog_end, 1.0), 0.0, 0.85);
    out_color = vec4(mix(v_color, fog_color, fog), 1.0);
}
"""

TEXT_VERTEX_SRC = """
#version 130
in vec2 in_pos;
in vec2 in_uv;
uniform vec2 screen;
out vec2 v_uv;
void main() {
    vec2 p = vec2(in_pos.x / screen.x * 2.0 - 1.0, 1.0 - in_pos.y / screen.y * 2.0);
    gl_Position = vec4(p, 0.0, 1.0);
    v_uv = in_uv;
}
"""

TEXT_FRAGMENT_SRC = """
#version 130
in vec2 v_uv;
out vec4 out_color;
uniform sampler2D tex;
void main() {
    out_color = texture(tex, v_uv);
}
"""


class TextGL:
    """Bitmap text in GL: pygame.font surfaces uploaded as textures."""

    def __init__(self, ctx, size_px: int = 26) -> None:
        import pygame

        self._pygame = pygame
        self.ctx = ctx
        self.font = pygame.font.Font(None, size_px)
        self.prog = ctx.program(vertex_shader=TEXT_VERTEX_SRC, fragment_shader=TEXT_FRAGMENT_SRC)
        self._cache = {}

    def _texture(self, text: str, color):
        key = (text, tuple(color))
        if key in self._cache:
            return self._cache[key]
        surf = self.font.render(text, True, tuple(color))
        tw, th = surf.get_size()
        tex = self.ctx.texture((tw or 1, th or 1), 4)
        import pygame

        raw = pygame.image.tostring(surf, "RGBA", True)
        tex.write(raw)
        tex.filter = (self.ctx.LINEAR, self.ctx.LINEAR)
        self._cache[key] = (tex, tw, th)
        if len(self._cache) > 300:
            old = self._cache.pop(next(iter(self._cache)))
            old[0].release()
        return self._cache[key]

    def draw_list(self, surface, lines_with_pos, color=(230, 242, 255)) -> None:
        """lines_with_pos: iterable of (text, x, y)."""
        import numpy as np

        sw, sh = surface.get_size()
        self.prog["screen"].value = (float(sw), float(sh))
        self.prog["tex"].value = 0
        for text, x, y in lines_with_pos:
            tex, tw, th = self._texture(text, color)
            quad = np.array(
                [x, y, 0, 0, x + tw, y, 1, 0, x + tw, y + th, 1, 1, x, y + th, 0, 1],
                dtype=np.float32,
            )
            vbo = self.ctx.buffer(quad.tobytes())
            vao = self.ctx.vertex_array(
                self.prog, [(vbo, "2f 2f", "in_pos", "in_uv")]
            )
            tex.use(0)
            vao.render(self.ctx.TRIANGLE_FAN)
            vao.release()
            vbo.release()


class Renderer3D:
    """Owns the GL context objects for one window."""

    def __init__(self, surface, track: Track) -> None:
        import moderngl

        self.ctx = moderngl.create_context()
        self.surface = surface
        self.track = track
        self.prog = self.ctx.program(vertex_shader=VERTEX_SRC, fragment_shader=FRAGMENT_SRC)
        verts, idx = build_track_mesh(track)
        self.track_vbo = self.ctx.buffer(verts.tobytes())
        self.track_ibo = self.ctx.buffer(idx.tobytes())
        self.track_vao = self.ctx.vertex_array(
            self.prog, [(self.track_vbo, "3f 3f", "in_position", "in_color")],
            index_buffer=self.track_ibo,
        )
        start_ring = build_ring_marker(track, 0.0, (0.2, 1.0, 0.4))
        finish_ring = build_ring_marker(track, track.total_length, (1.0, 0.85, 0.2))
        self.marker_vaos = []
        for v, i in (start_ring, finish_ring):
            vbo = self.ctx.buffer(v.tobytes())
            ibo = self.ctx.buffer(i.tobytes())
            vao = self.ctx.vertex_array(
                self.prog, [(vbo, "3f 3f", "in_position", "in_color")], index_buffer=ibo
            )
            self.marker_vaos.append((vao, vbo, ibo))
        self.item_vao = None
        self.item_vbo = None
        self.item_ibo = None
        self.text = TextGL(self.ctx)
        self._cam_pos = (0.0, 0.0, 0.0)
        from .theme import get_theme

        self.theme = get_theme()
        self._fog_scheme = None
        self.fog_color = self._theme_fog()

    def _theme_fog(self):
        """The palette's fog colour, re-read whenever the OS scheme flips."""
        if self._fog_scheme != self.theme.scheme:
            self._fog_scheme = self.theme.scheme
            self.fog_color = tuple(self.theme.fog3)
        return self.fog_color

    # ---------------------------------------------------------------- frame
    def draw(self, play, lines_with_pos) -> None:
        w, h = self.surface.get_size()
        self.ctx.viewport = (0, 0, w, h)
        # re-resolve the palette first: if the OS theme or high-contrast
        # scheme flipped since the last frame, the clear and the fog both
        # use the new colour immediately (no one-frame lag).
        self._theme_fog()
        # moderngl only clears the depth buffer when asked explicitly;
        # without this, stale depths from earlier camera positions block
        # new geometry and the frame degrades into a blank fog colour.
        self.ctx.clear(*self.fog_color, depth=1.0)
        self.ctx.enable(self.ctx.DEPTH_TEST)

        # Chase camera riding the tube frame 240 units behind the man.
        # Eye, view direction and up all come from ONE frame at the same
        # distance: the tube frame's up is perpendicular to its tangent by
        # construction, so look_at can never degenerate (a target that
        # follows the man's heading produces NaN view matrices on loops
        # and steep drops, and NaN discards every vertex - a blank frame).
        back_d = max(0.0, play.man.dist - 240.0)
        seg, u = self.track.param_from_distance(back_d)
        radius = self.track.tube_radius(seg)
        cfwd = self.track.tangent_at(seg, u)
        _cright, cup = build_frame(cfwd, self.track.roll_at(seg, u))
        cpos = self.track.pos_at(seg, u)
        eye = tuple(cpos[k] + cup[k] * (radius * 0.30) for k in range(3))
        target = tuple(cpos[k] + cup[k] * (radius * 0.30) + cfwd[k] * 1000.0
                       for k in range(3))
        up = cup
        self._cam_pos = eye
        view = look_at(eye, target, up)
        proj = perspective(70.0, w / max(1, h), 20.0, 22000.0)
        mvp = (proj @ view).astype(np.float32)

        # GL consumes column-major data; numpy built the matrix row-major.
        # Uploading without the transpose scrambles the rotation and the
        # geometry only lands on-screen by coincidence (symmetric tube
        # sections), vanishing into the fog colour everywhere else.
        self.prog["mvp"].write(mvp.T.tobytes())
        self.prog["cam_pos"].value = eye
        self.prog["fog_color"].value = self.fog_color
        self.prog["fog_end"].value = 9000.0

        self.track_vao.render()
        for vao, _vbo, _ibo in self.marker_vaos:
            vao.render()

        # live items
        verts, idx = build_item_markers(self.track, play.items)
        if len(idx):
            if self.item_vbo is not None:
                self.item_vbo.release()
                self.item_ibo.release()
                self.item_vao.release()
            self.item_vbo = self.ctx.buffer(verts.tobytes())
            self.item_ibo = self.ctx.buffer(idx.tobytes())
            self.item_vao = self.ctx.vertex_array(
                self.prog, [(self.item_vbo, "3f 3f", "in_position", "in_color")],
                index_buffer=self.item_ibo,
            )
            self.item_vao.render()

        # HUD text (unblended quads; font surfaces carry their own bg)
        self.ctx.disable(self.ctx.DEPTH_TEST)
        self.text.draw_list(self.surface, lines_with_pos, color=self.theme.hud_fg)

    def release(self) -> None:
        for attr in ("track_vao", "track_vbo", "track_ibo", "item_vao", "item_vbo", "item_ibo"):
            try:
                getattr(self, attr).release()
            except Exception:
                pass
        for vao, vbo, ibo in self.marker_vaos:
            for obj in (vao, vbo, ibo):
                try:
                    obj.release()
                except Exception:
                    pass
        self.ctx.release()
