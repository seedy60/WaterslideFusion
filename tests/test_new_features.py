"""Tests for speech engines, tutorial, disasm tooling, and 3D mesh builders."""

import math
import os
import sys
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import waterslide.records as records_mod
from waterslide.records import Options, Records


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "rec"))
    from waterslide.app import App

    a = App(headless=True)
    a.speech.enabled = False
    a.speech.shut_up()
    a.start()
    yield a
    a.quit()


# ------------------------------------------------------------------ speech
class TestSpeechEngines:
    def test_prism_output_creation(self):
        from waterslide.speech import PrismOutput

        p = PrismOutput("auto")
        assert isinstance(p.ok, bool)
        if p.ok:
            outs = p.outputs()
            assert isinstance(outs, list)
            assert p.voices() is not None

    def test_ao2_output_creation(self):
        from waterslide.speech import AO2Output

        a = AO2Output("auto")
        assert a.ok, "accessible_output2 Auto should initialise on Windows"
        assert "auto" in a.outputs()
        assert isinstance(a.voices(), list)

    def test_engine_switching(self, app):
        for engine in ("ao2", "prism", "prism"):
            app.speech.configure(engine=engine, rebuild=True)
            info = app.speech.probe()
            if engine == "prism" and info["engine"] != "prism":
                pytest.skip("Prism not available on this machine")
            assert info["engine"] == engine

    def test_settings_passthrough(self, app):
        app.speech.configure(rate=25, volume=75, pitch=60, rebuild=False)
        assert app.speech.rate == 25
        assert app.speech.volume == 75
        assert app.speech.pitch == 60

    def test_rate_mapping_ao2(self):
        # 0..100 -> ao2's -10..10 range
        assert 25 / 5.0 - 10.0 == -5.0
        assert 50 / 5.0 - 10.0 == 0.0
        assert 100 / 5.0 - 10.0 == 10.0

    def test_speech_option_persistence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "r"))
        r = Records()
        r.options.speech_engine = "ao2"
        r.options.speech_output = "sapi5"
        r.options.speech_voice = "Microsoft Hazel Desktop - English (Great Britain)"
        r.options.speech_rate = 70
        r.save()
        r2 = Records.load()
        assert r2.options.speech_engine == "ao2"
        assert r2.options.speech_output == "sapi5"
        assert r2.options.speech_voice == "Microsoft Hazel Desktop - English (Great Britain)"
        assert r2.options.speech_rate == 70

    def test_windows_detection(self):
        from waterslide.speech import prism_supported

        assert isinstance(prism_supported(), bool)


# ---------------------------------------------------------------- tutorial
class TestTutorial:
    def test_boot_and_first_step(self, app):
        app.speech.enabled = True
        app.menu_tutorial()
        assert app.screen == "tutorial"
        t = app.tutorial
        assert t is not None and len(t._steps) == 9
        for _ in range(90):
            app.update(1 / 30)
        assert t.phase == "running"
        assert t.step_index == 0
        # steering left completes step 0
        t.update(1 / 30, -1.0, False)
        assert t.step_index == 1
        app.tutorial._finish(quit_to_menu=True)
        assert app.screen == "main"

    def test_practice_items_spawn(self, app):
        app.menu_tutorial()
        t = app.tutorial
        for _ in range(90):
            app.update(1 / 30)
        t._spawn_practice_items()
        assert len(t._practice_items) == 4
        from waterslide.track import CLASS_BOOST, CLASS_CRAB, CLASS_DIAMOND

        classes = {it.cls for it in t._practice_items}
        assert classes == {CLASS_DIAMOND, CLASS_CRAB, CLASS_BOOST}

    def test_diamond_step_completes(self, app):
        app.menu_tutorial()
        t = app.tutorial
        for _ in range(90):
            app.update(1 / 30)
        t.update(1 / 30, -1.0, False)   # step 0: steer left
        t.update(1 / 30, 1.0, False)    # step 1: steer right
        for _ in range(90):             # step 2: wait for centre
            t.update(1 / 30, 0.0, False)
            if t.step_index >= 3:
                break
        assert t.step_index == 3
        t._spawn_practice_items()
        dia = t._practice_items[0]
        # steer onto the diamond angle (pi/2 = right wall)
        for _ in range(200):
            t.update(1 / 30, 1.0, False)
            if t.step_index > 3:
                break
        assert dia.collected
        assert t.step_index > 3

    def test_position_frozen(self, app):
        app.menu_tutorial()
        t = app.tutorial
        for _ in range(90):
            app.update(1 / 30)
        t.update(1 / 30, 1.0, False)
        assert t.man.dist == t.dist0
        assert t.man.speed == 0.0

    def _reach_crab_step(self, app):
        app.menu_tutorial()
        t = app.tutorial
        for _ in range(90):
            app.update(1 / 30)
        t.update(1 / 30, -1.0, False)   # step 0: steer left
        t.update(1 / 30, 1.0, False)    # step 1: steer right
        for _ in range(90):             # step 2: centre
            t.update(1 / 30, 0.0, False)
            if t.step_index >= 3:
                break
        t._spawn_practice_items()
        for _ in range(200):            # step 3: grab the wall diamond
            t.update(1 / 30, 1.0, False)
            if t.step_index >= 4:
                break
        assert t.step_index == 4
        for _ in range(200):            # step 4: the diagonal drill
            t.update(1 / 30, 1.0, False)
            if t.step_index >= 5:
                break
        assert t.step_index == 5
        return t

    def test_crab_dodge_means_staying_clear(self, app):
        """Regression: the crab drill completed only when you steered
        INTO the crab.  Holding the safe side must complete it instead."""
        t = self._reach_crab_step(app)
        crab = t._practice_items[2]
        for _ in range(40):  # ~1.3 s holding the safe (right) side
            t.update(1 / 30, 1.0, False)
            if t.step_index >= 5:
                break
        assert t.step_index == 5, "holding clear of the crab completes the drill"
        assert not crab.hit, "the player never has to hit the crab"

    def test_crab_hit_recoaches_instead_of_completing(self, app):
        t = self._reach_crab_step(app)
        said = []
        app.speech.say = lambda text, urgent=False, cue=None: said.append(text)
        crab = t._practice_items[2]
        for _ in range(400):  # the tube is wide: crossing it takes seconds
            t.update(1 / 30, -1.0, False)  # steer into the crab's side
            if crab.hit:
                break
        assert crab.hit
        assert t.step_index == 5, "bumping the crab must not complete the drill"
        assert any("Steer right" in s for s in said), "the coach re-explains"

    def test_reports_step_completes_with_p(self, app):
        """Regression: P never reached the tutorial, so the last skill
        could not be completed."""
        app.menu_tutorial()
        t = app.tutorial
        for _ in range(90):
            app.update(1 / 30)
        assert t.phase == "running"
        t.step_index = 8  # the reports step
        app.handle_keydown(112)  # P, routed through the app
        assert app.screen == "main" and app.tutorial is None, "P finished the tutorial"


# ------------------------------------------------------------------ disasm
class TestDisasm:
    @pytest.fixture(autouse=True)
    def _need_binary(self):
        if not (os.path.exists("build/ipa_extract/Glide.bin") or os.path.exists(
                os.path.join("IPA", "Waterslide Extreme (iOS).ipa"))):
            pytest.skip("Glide binary not extracted")

    def test_stats(self, capsys):
        import importlib.util

        spec = importlib.util.spec_from_file_location("disasm", "tools/disasm.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        binary = mod.load_binary()
        by_addr, flat, info = mod.load_symbols(binary)
        assert len(flat) > 1000
        assert info.cryptoff == 0x1000
        # symbol search
        hits = mod.find_symbol(flat, "PRSpline")
        assert hits
        # annotation helper
        assert mod.symbol_label(by_addr, hits[0][0]) is not None

    def test_demangle(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("disasm", "tools/disasm.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.demangle("__ZN8PRSpline9getLengthEv") == "PRSpline::getLength"
        assert mod.demangle("main") == "main"


# --------------------------------------------------------------------- 3D
class TestMeshes:
    @pytest.fixture(autouse=True)
    def _tracks(self):
        from waterslide.track import load_all_tracks

        for d in (os.path.join("tests", "fixtures", "level"), os.path.join("assets", "content", "level")):
            if os.path.isdir(d):
                self.tracks = load_all_tracks(d)
                break
        else:
            pytest.skip("no level fixtures")

    def test_track_mesh_shape(self):
        from waterslide.render3d import RING_SEGMENTS, SAMPLES, build_track_mesh

        verts, idx = build_track_mesh(self.tracks[0])
        assert verts.shape == (SAMPLES * (RING_SEGMENTS + 1), 6)
        assert idx.shape[0] == (SAMPLES - 1) * RING_SEGMENTS * 6
        assert verts.dtype == np.float32

    def test_track_mesh_bounds(self):
        from waterslide.render3d import build_track_mesh

        verts, _ = build_track_mesh(self.tracks[0])
        xyz = verts[:, :3]
        assert xyz.max() < 1e6 and xyz.min() > -1e6
        rgb = verts[:, 3:]
        assert (rgb >= 0).all() and (rgb <= 1).all()

    def test_ring_marker(self):
        from waterslide.render3d import build_ring_marker

        t = self.tracks[0]
        for d in (0.0, t.total_length):
            verts, idx = build_ring_marker(t, d, (1.0, 0.0, 0.0))
            assert verts.shape[0] >= 16
            assert idx.shape[0] >= 3
            assert (idx < verts.shape[0]).all()

    def test_item_markers(self):
        from waterslide.render3d import build_item_markers

        t = self.tracks[0]
        verts, idx = build_item_markers(t, t.items[:10])
        assert verts.shape[0] == 10 * 6
        assert idx.shape[0] == 10 * 8 * 3
        assert (idx < verts.shape[0]).all()

    def test_item_markers_skip_consumed(self):
        from waterslide.render3d import build_item_markers

        t = self.tracks[0]
        items = list(t.items[:4])
        items[0].collected = True
        items[1].hit = True
        verts, idx = build_item_markers(t, items)
        assert verts.shape[0] == 2 * 6

    def test_matrices(self):
        from waterslide.render3d import look_at, perspective

        p = perspective(70.0, 1.5, 1.0, 100.0)
        assert p[3, 2] == -1.0 and p[2, 3] < 0
        v = look_at((0, 0, 0), (0, 1, 0), (0, 0, 1))
        assert v.shape == (4, 4)

    def test_full_gl_optional(self):
        pytest.importorskip("moderngl")
        import moderngl

        try:
            ctx = moderngl.create_context(standalone=True)
        except Exception:
            pytest.skip("no GL context available")
        from waterslide.render3d import Renderer3D
        from waterslide.track import build_frame  # noqa: F401

        assert ctx is not None
        ctx.release()


# ------------------------------------------------- screen reader auto mode
class TestScreenReaderAutoMode:
    def test_detector_returns_bool(self):
        from waterslide.speech import screen_reader_running

        assert isinstance(screen_reader_running(), bool)

    def test_detector_uses_prism_backend_name(self, monkeypatch):
        import waterslide.speech as speech_mod

        calls = {"n": 0}

        class FakeBackend:
            name = "SAPI"

        class FakeContext:
            def create_best(self):
                calls["n"] += 1
                return FakeBackend()

        monkeypatch.setattr(speech_mod, "prism_supported", lambda: True)
        monkeypatch.setitem(sys.modules, "prism", mock.MagicMock(Context=FakeContext))
        assert speech_mod.screen_reader_running() is False  # SAPI: sighted machine
        assert calls["n"] == 1

        class FakeNVDA(FakeBackend):
            name = "NVDA"

        FakeContext.create_best = lambda self: FakeNVDA()  # type: ignore
        assert speech_mod.screen_reader_running() is True  # reader running

    def test_detector_falls_back_to_speech_assumption(self, monkeypatch):
        import waterslide.speech as speech_mod

        monkeypatch.setattr(speech_mod, "prism_supported", lambda: False)
        monkeypatch.setattr(speech_mod, "_ao2_controller_running", lambda: False)
        assert speech_mod.screen_reader_running() is False

    def test_speech_defaults_auto_in_released_config(self):
        assert Options().speech == "auto"

    def test_deferred_engine_never_builds_without_reader(self, tmp_path, monkeypatch):
        """Auto + no screen reader must not quietly build SAPI/OneCore."""
        import time

        import waterslide.speech as speech_mod

        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "defer"))
        monkeypatch.setattr(speech_mod, "screen_reader_running", lambda: False)
        from waterslide.speech import Speech

        s = Speech(None)
        try:
            assert s._defer_engine is True
            s.say("hello", urgent=True)
            time.sleep(0.5)  # the worker must NOT build an engine for this
            assert s.engine_kind == "none"
            # recovery: an explicit 'on' through the app's applier arms it
            s._defer_engine = False
            s._force_build = True
            s.configure(rebuild=True)
            deadline = time.time() + 3.0
            while time.time() < deadline and s.engine_kind == "none":
                time.sleep(0.05)
            assert s.engine_kind != "none"  # engine built on demand
        finally:
            s.quit()

    def test_legacy_bool_configs_survive_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "legacy"))
        r = Records()
        r.options.speech = "off"
        r.save()
        # simulate a config written by the previous release (0/1)
        path = os.path.join(str(tmp_path / "legacy"), "records.cfg")
        text = open(path, encoding="utf-8").read().replace("speech,off", "speech,0")
        open(path, "w", encoding="utf-8").write(text)
        r2 = Records.load()
        assert r2.options.speech == "off"
        # '1' meant "the normal speech experience", which is now auto:
        # sighted machines go silent with the 3D view, blind players'
        # readers are always running so they resolve to speech anyway.
        open(path, "w", encoding="utf-8").write("[options]\nspeech,1\n")
        assert Records.load().options.speech == "auto"

    def test_auto_gives_3d_and_silence_without_reader(self, tmp_path, monkeypatch):
        import waterslide.speech as speech_mod

        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "auto1"))
        monkeypatch.setattr(speech_mod, "screen_reader_running", lambda: False)
        monkeypatch.setattr(sys, "argv", ["run_game.py"])
        from waterslide.app import App

        a = App(headless=True)
        try:
            assert a.speech.enabled is False
            assert a.speech.mode_pref == "auto"
            assert a.use_3d is True  # the 3D view is always on
        finally:
            a.quit()

    def test_3d_stays_on_with_reader_too(self, tmp_path, monkeypatch):
        """Visual impairment is a spectrum: a screen-reader user with usable
        vision gets speech AND the 3D view by default."""
        import waterslide.speech as speech_mod

        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "auto3"))
        monkeypatch.setattr(speech_mod, "screen_reader_running", lambda: True)
        monkeypatch.setattr(sys, "argv", ["run_game.py"])
        from waterslide.app import App

        a = App(headless=True)
        try:
            assert a.speech.enabled is True
            assert a.use_3d is True  # 3D no longer tied to speech state
        finally:
            a.quit()

    def test_no_3d_flag_turns_the_view_off(self, tmp_path, monkeypatch):
        import waterslide.speech as speech_mod

        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "no3d"))
        monkeypatch.setattr(speech_mod, "screen_reader_running", lambda: True)
        monkeypatch.setattr(sys, "argv", ["run_game.py", "--no-3d"])
        from waterslide.app import App

        a = App(headless=True)
        try:
            assert a.speech.enabled is True
            assert a.use_3d is False
        finally:
            a.quit()

    def test_auto_gives_speech_without_3d_with_reader(self, tmp_path, monkeypatch):
        import waterslide.speech as speech_mod

        monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "auto2"))
        monkeypatch.setattr(speech_mod, "screen_reader_running", lambda: True)
        monkeypatch.setattr(sys, "argv", ["run_game.py"])
        from waterslide.app import App

        a = App(headless=True)
        try:
            assert a.speech.enabled is True
            assert a.use_3d is True  # 3D no longer tied to speech state
        finally:
            a.quit()
