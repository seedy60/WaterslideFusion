"""Headless tests for the App screen flow (menus, options, progression)."""

import os

import pytest

from waterslide.app import App
import waterslide.records as records_mod


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(records_mod, "records_dir", lambda: str(tmp_path / "rec"))
    a = App(headless=True)
    a.speech.enabled = False          # keep pytest quiet
    a.speech.shut_up()
    a.start()
    yield a
    a.quit()


def key(app, k):
    app.handle_keydown(k)
    app.handle_keyup(k)


class TestMainFlow:
    def test_boots_to_main_menu(self, app):
        assert app.screen == "main"
        assert len(app.menu_items) == 7
        assert [m.label for m in app.menu_items][:2] == ["play", "tutorial"]

    def test_menu_navigation(self, app):
        key(app, 1073741905)  # down
        assert app.menu_index == 1
        key(app, 1073741906)  # up
        assert app.menu_index == 0
        key(app, 1073741906)  # wraps to bottom
        assert app.menu_index == len(app.menu_items) - 1

    def test_help_screen(self, app):
        # move to instructions (3rd item)
        app.show_main_menu()
        app.menu_index = 2
        app.menu_select()
        assert app.screen == "help"
        key(app, 27)
        assert app.screen == "main"

    def test_scores_screen(self, app):
        app.menu_index = 3
        app.menu_select()
        assert app.screen == "scores"
        key(app, 27)
        assert app.screen == "main"

    def test_tutorial_entry(self, app):
        app.menu_index = 1
        app.menu_select()
        assert app.screen == "tutorial"
        assert app.tutorial is not None
        app.tutorial._finish(quit_to_menu=True)
        assert app.screen == "main"


class TestStageFlow:
    def test_stage_select_starts_stage(self, app):
        app.menu_select()  # play -> stage list
        assert app.screen == "stages"
        key(app, 13)  # choose stage 1
        assert app.screen == "game"
        assert app.play is not None
        assert app.play.stage_number == 1
        assert app.play.phase == "countdown"

    def test_stage2_locked_initially(self, app):
        app.menu_select()
        app.stage_index = 1
        app.stage_select()
        assert app.screen == "stages"  # refused

    def test_unlock_after_finish(self, app):
        app.menu_select()
        key(app, 13)
        play = app.play
        play.phase = "racing"
        play.man.dist = play.track.total_length  # force finish
        app.update(0.016)
        assert play.man.finished
        app.finish_stage(play)
        assert app.records.unlocked_stage == 2
        assert app.screen == "gameover"

    def test_pause_cycle(self, app):
        app.menu_select()
        key(app, 13)
        key(app, 27)  # escape -> pause
        assert app.screen == "pause"
        app.pause_index = 0
        key(app, 13)  # resume
        assert app.screen == "game"

    def test_restart_from_pause(self, app):
        app.menu_select()
        key(app, 13)
        old = app.play
        key(app, 27)
        app.pause_index = 1
        key(app, 13)
        assert app.screen == "game"
        assert app.play is not old


class TestOptions:
    def _goto(self, app, row_key):
        app.menu_index = 5  # options
        app.menu_select()
        assert app.screen == "options"
        for i, (key, _text) in enumerate(app._option_rows()):
            if key == row_key:
                app.option_index = i
                return
        raise AssertionError(f"option row {row_key!r} missing")

    def test_speech_mode_is_auto_by_default(self, app):
        self._goto(app, "speech")
        assert app.options.speech == "auto"

    def test_speech_mode_steps_through_choices(self, app):
        self._goto(app, "speech")
        app.option_adjust(1)  # auto -> on
        assert app.options.speech == "on"
        assert app.speech.enabled is True
        app.option_adjust(1)  # on -> off
        assert app.options.speech == "off"
        assert app.speech.enabled is False
        app.option_adjust(-1)  # off -> on
        assert app.options.speech == "on"
        app.option_adjust(1)  # on -> off; two rights from auto wrapped
        app.option_adjust(1)  # off -> auto
        assert app.options.speech == "auto"

    def test_toggle_music(self, app):
        self._goto(app, "music")
        before = app.options.music
        app.option_toggle()
        assert app.options.music is (not before)

    def test_rate_cycle(self, app):
        self._goto(app, "speech rate")
        before = app.options.speech_rate
        app.option_toggle()
        assert app.options.speech_rate != before
        assert 0 <= app.options.speech_rate <= 100

    def test_cycle_audio_hotkey(self, app):
        app.options.speech = "auto"
        app.cycle_audio()
        assert app.options.speech == "on"
        app.cycle_audio()
        assert app.options.speech == "off"
        assert app.speech.enabled is False
        app.cycle_audio()
        assert app.options.speech == "auto"


class TestRecordsIntegration:
    def test_finish_writes_score(self, app, tmp_path):
        app.menu_select()
        key(app, 13)
        play = app.play
        play.phase = "racing"
        play.score = 4321
        play.man.dist = play.track.total_length
        app.update(0.016)
        app.finish_stage(play)
        rec = records_mod.Records.load()
        assert rec.scores, "score should be persisted"
        assert rec.scores[0].score >= 4321
