"""Tests for the OS-aware theme: dark mode follows the OS, high contrast
stands down, the palette re-resolves while the game runs, the user can
override the OS in options, and the 3D item markers are colourblind-safe
(measured with Machado 2009 CVD simulation)."""

import math
import os
import time

import numpy as np
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from waterslide import theme as theme_mod
from waterslide.records import Records
from waterslide.render3d import ITEM_PALETTE, build_item_markers
from waterslide.theme import HC, DARK, LIGHT, Theme, detect


@pytest.fixture()
def os_probe(monkeypatch):
    """Replace the registry probe with a controllable stub.

    Returns a setter: set_os(high_contrast, app_theme) where each side is
    None (flag unreadable) or its value.
    """
    state = {"hc": None, "theme": None}

    def fake_read():
        return state["hc"], state["theme"]

    def set_os(hc=None, theme=None):
        state["hc"] = hc
        state["theme"] = theme

    monkeypatch.setattr(theme_mod, "_read_windows", fake_read)
    return set_os


class TestDetectionPrecedence:
    def test_dark_when_os_says_dark(self, os_probe):
        os_probe(theme="dark")
        assert detect() == "dark"

    def test_light_when_os_says_light(self, os_probe):
        os_probe(theme="light")
        assert detect() == "light"

    def test_dark_when_nothing_readable(self, os_probe):
        os_probe()
        assert detect() == "dark"

    def test_high_contrast_beats_dark(self, os_probe):
        os_probe(hc="hc", theme="dark")
        assert detect() == "hc"

    def test_high_contrast_beats_light(self, os_probe):
        os_probe(hc="hc", theme="light")
        assert detect() == "hc"

    def test_unreadable_theme_flag_defaults_dark(self, os_probe):
        # HighContrast readable but off, AppsUseLightTheme missing
        os_probe(hc="not-hc", theme=None)
        assert detect() == "dark"


class TestPalettes:
    def test_palettes_are_maximum_contrast(self):
        for pal in (DARK, LIGHT, HC):
            bg, fg = pal["fg"], pal["bg"]
            lum = sum((a - b) ** 2 for a, b in zip(bg, fg)) ** 0.5
            assert lum > 200, pal["name"]

    def test_high_contrast_stands_down(self):
        assert HC["fg"] == HC["dim"] == HC["hud_fg"] == (255, 255, 255)
        assert tuple(HC["fog3"]) == (0.0, 0.0, 0.0)

    def test_light_is_actually_light(self):
        assert sum(LIGHT["bg"]) > 600
        assert sum(LIGHT["fg"]) < 150

    def test_dark_is_actually_dark(self):
        assert sum(DARK["bg"]) < 100


class TestDynamicResolution:
    def test_poll_reports_scheme_change(self, os_probe):
        os_probe(theme="dark")
        t = Theme(watch=False)
        assert t.scheme == "dark"
        assert t.poll() is False
        os_probe(theme="light")  # the user flips the OS theme
        assert t.poll() is True  # app must repaint
        assert t.scheme == "light"
        assert t.poll() is False  # one-shot transition

    def test_poll_picks_up_high_contrast_engaging(self, os_probe):
        os_probe(theme="dark")
        t = Theme(watch=False)
        os_probe(hc="hc", theme="dark")
        assert t.poll() is True
        assert t.scheme == "hc"
        assert t.palette is HC

    def test_pinned_scheme_never_re_resolves(self, os_probe):
        t = Theme(scheme="light", watch=False)
        os_probe(theme="dark")
        assert t.poll() is False
        assert t.scheme == "light"

    def test_watcher_thread_resolves_changes(self, os_probe):
        os_probe(theme="dark")
        t = Theme(watch=True)  # 1 Hz watcher
        try:
            os_probe(theme="light")
            deadline = time.time() + 3.0
            while time.time() < deadline and t.scheme != "light":
                time.sleep(0.05)
            assert t.scheme == "light"
            assert t.poll() is True  # the transition is reported once
        finally:
            t.stop()

    def test_stop_detaches_to_poll_on_demand(self, os_probe):
        os_probe(theme="dark")
        t = Theme(watch=True)
        t.stop()
        os_probe(theme="light")
        assert t.poll() is True  # no watcher: poll re-resolves itself
        assert t.scheme == "light"

    def test_describe(self, os_probe):
        os_probe(hc="hc")
        assert Theme(watch=False).describe() == "high contrast mode"


class TestAppWiring:
    def _app(self):
        from waterslide.app import App

        a = App(headless=True)
        a.start()
        return a

    def test_app_has_a_theme(self):
        app = self._app()
        try:
            assert app.theme.scheme in ("dark", "light", "hc")
        finally:
            app.quit()

    def test_draw_uses_palette(self):
        app = self._app()
        try:
            app.screen = "main"
            app.theme = Theme(scheme="light", watch=False)
            app.theme.bg = (1, 2, 3)
            app.theme.fg = (4, 5, 6)
            surface = FakeSurface()
            app.draw(surface)
            assert surface.fills and surface.fills[-1] == (1, 2, 3)
        finally:
            app.quit()

    def test_theme_poll_triggers_repaint(self):
        app = self._app()
        try:
            app.theme = Theme(scheme="dark", watch=False)
            assert app.theme.poll() is False
            app.theme.refresh = lambda: True  # simulate "OS theme flipped"
            assert app.theme.poll() is True
        finally:
            app.quit()


class TestRendererWiring:
    def test_fog_and_hud_follow_the_theme(self, monkeypatch):
        from waterslide.app import App

        os_state = {"hc": None, "theme": "dark"}
        monkeypatch.setattr(
            theme_mod, "_read_windows", lambda: (os_state["hc"], os_state["theme"])
        )
        # one shared theme, like the real process-wide accessor: the app
        # and the renderer must observe the SAME scheme object.  app.py
        # binds get_theme with a from-import, so patch it there too.
        shared = Theme(watch=False)
        monkeypatch.setattr(theme_mod, "get_theme", lambda: shared)
        monkeypatch.setattr("waterslide.app.get_theme", lambda: shared)
        drawn = {}

        class FakeTextGL:
            def __init__(self, ctx, size_px=26):
                pass

            def draw_list(self, surface, lines, color=(230, 242, 255)):
                drawn["hud"] = tuple(color)

        class FakeRenderer:
            def __init__(self, surface, track):
                self.theme = theme_mod.get_theme()
                self._fog_scheme = None
                self.fog_color = None
                self.text = FakeTextGL(None)
                self._theme_fog()  # like the real renderer

            def _theme_fog(self):
                if self._fog_scheme != self.theme.scheme:
                    self._fog_scheme = self.theme.scheme
                    self.fog_color = tuple(self.theme.fog3)
                return self.fog_color

            def draw(self, play, lines):
                self._theme_fog()  # like the real renderer: before the clear
                drawn["fog"] = tuple(self.fog_color)
                self.text.draw_list(None, lines, color=self.theme.hud_fg)

        app = App(headless=True)
        try:
            app.start()
            # headless start() has no window: give the renderer branch a
            # stand-in surface so it can run
            app._surface = object()
            app._renderer3d_cls = FakeRenderer
            app.start_stage(0)
            assert app.renderer is not None
            app.renderer.draw(app.play, [])
            assert drawn["fog"] == tuple(DARK["fog3"])
            assert drawn["hud"] == tuple(DARK["hud_fg"])

            # the user flips the OS theme mid-race: the watcher (or the
            # run loop's poll-driven refresh) re-resolves, next draw follows
            os_state["theme"] = "light"
            app.theme.refresh()
            app.renderer.draw(app.play, [])
            assert drawn["fog"] == tuple(LIGHT["fog3"])
            assert drawn["hud"] == tuple(LIGHT["hud_fg"])

            # high contrast engages: stand down to flat black/white
            os_state["hc"] = "hc"
            app.theme.refresh()
            app.renderer.draw(app.play, [])
            assert drawn["fog"] == (0.0, 0.0, 0.0)
            assert drawn["hud"] == (255, 255, 255)
        finally:
            app.quit()


# ------------------------------------------------------- colourblind safe
def _srgb_to_linear(rgb):
    arr = np.asarray(rgb, dtype=np.float64)
    return np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)


# Machado, Oliveira & Fernandes 2009, severity 1.0 (linear-RGB space)
_MACHADO = {
    "deuteranopia": np.array([
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ]),
    "protanopia": np.array([
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ]),
}


def _simulated(rgb, kind):
    lin = _srgb_to_linear(np.array(rgb, dtype=np.float64) / 255.0)
    return np.clip(_MACHADO[kind] @ lin, 0.0, 1.0)


def _pair_distance(a, b, kind):
    return float(np.linalg.norm(_simulated(a, kind) - _simulated(b, kind)))


def _rgb01(rgb):
    return tuple(int(round(c * 255)) for c in rgb)


class TestColorblindPalette:
    def test_every_class_has_a_color(self):
        for cls in ("ring", "heart", "star", "boost", "crab", "duck"):
            assert cls in ITEM_PALETTE, cls

    def test_colors_are_valid_srgb(self):
        for cls, col in ITEM_PALETTE.items():
            assert all(0.0 <= c <= 1.0 for c in col), cls

    def test_diamond_vs_crab_under_deuteranopia(self):
        # the critical gameplay pair: collectible vs hazard
        d = _pair_distance(_rgb01(ITEM_PALETTE["ring"]), _rgb01(ITEM_PALETTE["crab"]),
                           "deuteranopia")
        assert d > 0.25, f"diamond/crab too close under deuteranopia: {d:.3f}"

    def test_diamond_vs_crab_under_protanopia(self):
        d = _pair_distance(_rgb01(ITEM_PALETTE["ring"]), _rgb01(ITEM_PALETTE["crab"]),
                           "protanopia")
        assert d > 0.25, f"diamond/crab too close under protanopia: {d:.3f}"

    @pytest.mark.parametrize("kind", ["deuteranopia", "protanopia"])
    def test_all_pairs_distinguishable(self, kind):
        keys = sorted(ITEM_PALETTE)
        weakest = (1.0, None, None)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                d = _pair_distance(_rgb01(ITEM_PALETTE[a]), _rgb01(ITEM_PALETTE[b]), kind)
                if d < weakest[0]:
                    weakest = (d, a, b)
        d, a, b = weakest
        assert d > 0.12, f"{a}/{b} nearly identical under {kind}: {d:.3f}"

    def test_hazard_is_darker_than_treats(self):
        # a non-colour cue: the crab must not be the brightest marker
        def lum(c):
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        assert lum(ITEM_PALETTE["crab"]) < lum(ITEM_PALETTE["star"])

    def test_markers_use_the_safe_palette(self):
        from waterslide.track import TrackItem

        class FakeTrack:
            total_length = 100.0

            def param_from_distance(self, d):
                return (0, d / 100.0)

            def pos_at(self, seg, u):
                return (u * 100.0, 0.0, 0.0)

            def tangent_at(self, seg, u):
                return (1.0, 0.0, 0.0)

            def tube_radius(self, seg):
                return 10.0

            def roll_at(self, seg, u):
                return 0.0

        items = [TrackItem(cls=c, t=0.5 * i / 5, angle=0.0)
                 for i, c in enumerate(("ring", "crab", "star", "heart", "duck"))]
        verts, idx = build_item_markers(FakeTrack(), items)
        assert len(idx) > 0
        # stride 6: x, y, z, r, g, b - every vertex carries its item colour
        # (float32 precision: compare rounded python floats)
        colors = {tuple(round(float(x), 2) for x in v[3:]) for v in verts}
        expected = {tuple(round(float(c), 2) for c in ITEM_PALETTE[cls])
                    for cls in ("ring", "crab", "star", "heart", "duck")}
        assert colors == expected, (colors, expected)


# -------------------------------------------------- distinct marker shapes
def _shape_verts(cls):
    """Build one marker of the class and return its unique vertices."""
    from waterslide.track import TrackItem

    class FakeTrack:
        total_length = 100.0

        def param_from_distance(self, d):
            return (0, d / 100.0)

        def pos_at(self, seg, u):
            return (u * 100.0, 0.0, 0.0)

        def tangent_at(self, seg, u):
            return (1.0, 0.0, 0.0)

        def tube_radius(self, seg):
            return 10.0

        def roll_at(self, seg, u):
            return 0.0

    item = TrackItem(cls=cls, t=0.5, angle=0.0)
    verts, _ = build_item_markers(FakeTrack(), [item])
    return {tuple(round(float(x), 3) for x in v[:3]) for v in verts}


class TestDistinctShapes:
    def test_every_class_has_a_shape(self):
        from waterslide.render3d import ITEM_SHAPES

        for cls in ("ring", "heart", "star", "boost", "crab", "duck"):
            assert cls in ITEM_SHAPES, cls

    def test_shapes_are_distinct_solid_geometry(self):
        from waterslide.render3d import ITEM_SHAPES

        nverts = {}
        for cls, (builder, s) in ITEM_SHAPES.items():
            pts, tris = builder((0.0, 0.0, 0.0), s, (1.0, 0.0, 0.0),
                                (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            assert len(set(pts)) == len(pts), f"{cls}: duplicate vertices"
            assert len(tris) >= 4, f"{cls}: not a solid"
            nverts[cls] = len(pts)
        assert len(set(nverts.values())) == len(nverts), nverts

    @pytest.mark.parametrize("cls", ["ring", "heart", "star", "boost", "crab", "duck"])
    def test_marker_carries_class_geometry(self, cls):
        from waterslide.render3d import ITEM_SHAPES

        expected = len(ITEM_SHAPES[cls][0]((0.0, 0.0, 0.0), ITEM_SHAPES[cls][1],
                                           (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                                           (0.0, 0.0, 1.0))[0])
        got = len(_shape_verts(cls))
        assert got == expected, f"{cls}: mesh has {got} verts, shape defines {expected}"

    def test_star_points_up_in_the_tube_frame(self):
        from waterslide.render3d import _shape_star

        pts, _ = _shape_star((0.0, 0.0, 0.0), 1.0, (1.0, 0.0, 0.0),
                             (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        top = max(p[1] for p in pts)
        bottom = min(p[1] for p in pts)
        assert top == pytest.approx(1.0)  # a spike at up*s
        # a classic five-point star's two legs sit 54 degrees below
        # horizontal, reaching -cos(36deg); the top spike still reaches
        # further than the legs
        assert bottom == pytest.approx(-math.cos(math.pi / 5), rel=1e-3)
        assert top > abs(bottom)

    def test_boost_pad_lies_flat_on_the_slide(self):
        from waterslide.render3d import _shape_boost

        pts, _ = _shape_boost((0.0, 0.0, 0.0), 1.0, (1.0, 0.0, 0.0),
                              (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        width = max(p[0] for p in pts) - min(p[0] for p in pts)
        thickness = max(p[1] for p in pts) - min(p[1] for p in pts)
        length = max(p[2] for p in pts) - min(p[2] for p in pts)
        assert width > thickness * 3
        assert length > thickness * 3

    def test_all_shapes_centered_on_the_item(self):
        from waterslide.render3d import ITEM_SHAPES

        for cls, (builder, s) in ITEM_SHAPES.items():
            pts, _ = builder((0.0, 0.0, 0.0), s, (1.0, 0.0, 0.0),
                             (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            center = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
            assert all(abs(c) < s * 0.45 for c in center), (cls, center)

    def test_cheap_pairs_differ_in_luminance_too(self):
        # diamond/boost is the closest CVD pair on the blue axis: geometry
        # is the real discriminator, but a luminance gap never hurts
        def lum(c):
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        assert lum(ITEM_PALETTE["boost"]) > lum(ITEM_PALETTE["ring"]) + 0.05


# ------------------------------------------------------------ theme option
class TestThemeOption:
    def test_default_is_auto(self):
        assert Records().options.theme_mode == "auto"

    def test_theme_mode_persists(self, tmp_path, monkeypatch):
        from waterslide import records as records_mod

        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "r"))
        r = Records()
        r.options.theme_mode = "light"
        r.save()
        assert Records.load().options.theme_mode == "light"
        r.options.theme_mode = "bogus"
        r.save()
        assert Records.load().options.theme_mode == "auto"

    def test_override_pins_and_unpins(self, os_probe):
        os_probe(theme="dark")
        t = Theme(watch=False)
        assert t.override == "auto"
        t.set_override("light")
        assert t.scheme == "light"
        assert t.override == "light"
        # the OS flips while the user has pinned light: nothing changes
        os_probe(theme="dark")
        t.refresh()
        assert t.scheme == "light"
        # back to auto: the OS owns the palette again
        t.set_override("auto")
        assert t.scheme == "dark"
        assert t.override == "auto"

    def test_set_override_rejects_unknown(self, os_probe):
        os_probe(theme="dark")
        t = Theme(watch=False)
        with pytest.raises(ValueError):
            t.set_override("sepia")

    def test_pinned_dark_beats_os_light(self, os_probe):
        os_probe(theme="light")
        t = Theme(watch=False)
        t.set_override("dark")
        assert t.scheme == "dark"


class TestThemeOptionsRow:
    def _app(self):
        from waterslide.app import App

        a = App(headless=True)
        a.start()
        return a

    def test_row_present_and_reads_os_state(self, monkeypatch):
        monkeypatch.setattr(theme_mod, "_read_windows", lambda: (None, "light"))
        app = self._app()
        try:
            row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "theme")
            app.option_index = row
            heard = []
            app.speech.say = lambda t, urgent=False, cue=None: heard.append(t)
            app._announce_option()
            text = " ".join(heard).lower()
            assert "theme" in text and "auto" in text
            assert "light" in text  # announces what the OS is doing now
        finally:
            app.quit()

    def test_walk_and_persist(self, monkeypatch, tmp_path):
        from waterslide import records as records_mod

        monkeypatch.setattr(theme_mod, "_read_windows", lambda: (None, "dark"))
        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "r"))
        app = self._app()
        try:
            row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "theme")
            app.option_index = row
            app.option_adjust(1)  # auto -> dark
            assert app.options.theme_mode == "dark"
            assert app.theme.scheme == "dark" and app.theme.override == "dark"
            app.option_adjust(1)  # dark -> light
            assert app.options.theme_mode == "light"
            assert app.theme.scheme == "light"
            app.option_adjust(1)  # light -> auto
            assert app.options.theme_mode == "auto"
            assert app.theme.override == "auto"
            assert app.theme.scheme == "dark"  # OS says dark again
            assert Records.load().options.theme_mode == "auto"
        finally:
            app.quit()

    def test_pinned_light_hides_os_dark_from_row(self, monkeypatch):
        monkeypatch.setattr(theme_mod, "_read_windows", lambda: (None, "dark"))
        app = self._app()
        try:
            row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "theme")
            app.option_index = row
            app.option_adjust(1)  # -> dark
            app.option_adjust(1)  # -> light, pinned
            heard = []
            app.speech.say = lambda t, urgent=False, cue=None: heard.append(t)
            app._announce_option()
            assert "light" in " ".join(heard).lower()
            assert "operating system" not in " ".join(heard).lower()
            # leave the shared theme where later tests expect it
            app.theme.set_override("auto")
        finally:
            app.quit()


class FakeSurface:
    """Minimal pygame.Surface stand-in for the 2D mirror draw."""

    def __init__(self) -> None:
        self.fills = []

    def fill(self, color):
        self.fills.append(tuple(color))

    def blit(self, surf, pos):
        pass

    def get_size(self):
        return (64, 64)
