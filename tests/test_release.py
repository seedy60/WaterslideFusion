"""Tests for the v1.2 release changes: volumes, results dialog, death, music."""

import os
from unittest import mock

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from waterslide.app import App  # noqa: E402
from waterslide.audio import AudioEngine  # noqa: E402
from waterslide.game import FALL_GRACE  # noqa: E402


@pytest.fixture(scope="module")
def app():
    app = App(headless=False)
    app.start()
    yield app
    app.quit()


class TestVolumeHotkeys:
    def test_shift_pgup_pgdn_reaches_sound_volume(self, app):
        """Regression: the run loop called _volume_hotkey without the mods,
        so Shift was silently ignored and Shift+PgUp/PgDn adjusted music
        volume like its unshifted twins."""
        app.screen = "main"
        o = app.options
        o.sound = True
        o.sound_volume = 50
        o.music_volume = 50
        KMOD_SHIFT = 3
        app.handle_keydown(1073741899, "", KMOD_SHIFT)  # Shift+PgUp
        assert o.sound_volume == 60 and o.music_volume == 50
        app.handle_keydown(1073741902, "", KMOD_SHIFT)  # Shift+PgDn
        assert o.sound_volume == 50 and o.music_volume == 50
        o.sound_volume = o.music_volume = 50  # restore shared state

    def test_home_end_mute_and_full(self, app):
        """Home/End = music mute/full instantly; Shift+Home/End = the
        sound-effects channel.  Muting must hush the theme at once."""
        app.screen = "main"
        o = app.options
        o.music_volume = 50
        o.sound_volume = 50
        o.sound = True
        KMOD_SHIFT = 3
        app.handle_keydown(1073741901)              # End: music muted
        assert o.music_volume == 0
        app.handle_keydown(1073741898)              # Home: music full
        assert o.music_volume == 100
        app.handle_keydown(1073741898, "", KMOD_SHIFT)   # Shift+Home: sfx full
        assert o.sound_volume == 100 and o.music_volume == 100
        app.handle_keydown(1073741901, "", KMOD_SHIFT)   # Shift+End: sfx muted
        assert o.sound_volume == 0 and o.music_volume == 100
        o.sound_volume, o.music_volume = 50, 50     # restore shared state
        o.sound = True
        app.audio.enabled = True  # the mute also disabled the engine

    def test_mute_suspends_music_restore_continues(self, app, monkeypatch):
        """Regression: muting mid-race hard-stopped the theme, so bringing
        it back restarted the song from the top.  Mute must suspend the
        song in place (like pausing) and restoring must continue it."""
        app.start_stage(0)
        app.play.phase = "racing"
        app.options.music = True
        app.options.music_volume = 50
        calls = []
        for name in ("play_music", "pause_music", "stop_music"):
            monkeypatch.setattr(
                app.audio, name,
                (lambda n: lambda *a, **k: calls.append(n))(name),
                raising=True,
            )
        app.handle_keydown(1073741901)                    # End: mute
        assert "pause_music" in calls, "mute must suspend, not stop"
        assert "stop_music" not in calls, "mute must never hard-stop mid-race"
        calls.clear()
        app._music_policy()                               # later frame, still muted
        assert "stop_music" not in calls, "policy must not stop while suspended"
        calls.clear()
        app.handle_keydown(1073741898)                    # Home: full
        app._music_policy()                               # the next frame
        assert "play_music" in calls, "restore must continue the song"
        assert "stop_music" not in calls, "restore must not bounce through stop"
        app.options.music_volume = 50                     # restore shared state
        app.audio.stop_music()

    def test_death_dialog_waits_for_cry(self, app):
        """The death dialog appears only after the fall cry finishes, so
        the spoken message never fights the cry for attention."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.lives = 2
        play.apply_fall()
        assert app.dialog is None, "the cry must finish before the dialog"
        assert play._dialog_text is not None          # it is pending
        app.update(0.05)
        assert app.dialog is None, "still inside the cry window"
        app.update(10.0)                              # outlast the cry
        assert app.dialog is not None, "dialog appears after the cry"
        app.dismiss_dialog()
        app.options.sound = True  # restore shared state

    def test_death_dialog_instant_when_sfx_muted(self, app):
        """With sound effects muted there is no cry to wait for: the
        dialog must appear immediately."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.lives = 2
        app.options.sound = False
        play.apply_fall()
        app.update(0.016)              # the pending dialog surfaces on the next tick
        assert app.dialog is not None, "muted sfx: no cry, no wait"
        app.dismiss_dialog()
        app.options.sound = True  # restore shared state

    def test_pgup_pgdn_music_volume(self, app):
        app.screen = "main"
        app.options.music_volume = 50
        app.handle_keydown(1073741899)  # PgUp
        assert app.options.music_volume == 60
        app.handle_keydown(1073741902)  # PgDn
        assert app.options.music_volume == 50

    def test_adjust_music_volume_bounds_and_mute(self, app):
        o = app.options
        o.music = True
        o.music_volume = 5
        app.adjust_music_volume(-10)  # -> 0 mutes, but keeps the on-preference
        assert o.music_volume == 0 and o.music is True
        app.adjust_music_volume(10)   # -> audible again
        assert o.music is True and o.music_volume == 10
        app.adjust_music_volume(200)  # clamped
        assert o.music_volume == 100
        app.adjust_music_volume(-200)  # clamped the other way
        assert o.music_volume == 0
        app.adjust_music_volume(100)   # restore the shared app's state
        app.audio.stop_music()

    def test_adjust_sound_volume_bounds_and_mute(self, app):
        o = app.options
        o.sound = True
        o.sound_volume = 5
        app.adjust_sound_volume(-10)
        assert o.sound_volume == 0 and o.sound is False
        app.adjust_sound_volume(10)
        assert o.sound is True and o.sound_volume == 10
        app.adjust_sound_volume(-200)
        assert o.sound_volume == 0

    def test_option_rows_include_volumes_and_voice(self, app):
        keys = [k for k, _ in app._option_rows()]
        assert "music volume" in keys and "sound volume" in keys and "voice" in keys


class TestResultsDialog:
    def test_results_text_win(self, app):
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.man.finished = True
        app.finish_stage(play)
        text = app.results_text()
        assert "Stage 1 complete" in text and "final score" in text
        assert app.screen == "gameover"

    def test_next_stage_option_after_win(self, app):
        labels = [l for l, _ in app._gameover_options()]
        assert labels[0] == "next stage" and "replay stage" in labels

    def test_enter_dismisses_dialog_then_selects(self, app):
        # Enter first dismisses the modal dialog, then selects next stage
        if app.dialog is not None:
            app.dismiss_dialog()
        app.handle_keydown(13)
        assert app.screen == "game" and app.play.stage_number == 2

    def test_no_next_stage_option_after_loss(self, app):
        if app.dialog is not None:
            app.dismiss_dialog()
        play = app.play
        play.lives = 1
        play.apply_fall()  # dies
        labels = [l for l, _ in app._gameover_options()]
        assert "next stage" not in labels and labels[0] == "replay stage"

    def test_r_repeats_results(self, app):
        if app.dialog is not None:
            app.dismiss_dialog()
        app.handle_keydown(114)  # must not raise
        app.show_main_menu()


class TestDeathAndFall:
    def test_pause_suspends_whole_soundscape_and_resume_continues(self, app, monkeypatch):
        """Pausing hushes everything: music suspends via the policy and the
        water/wind beds via pause_ambience; resume continues both in place.
        Restart from the pause menu gets fresh beds, not unpaused ones."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        calls = []
        monkeypatch.setattr(app.audio, "pause_ambience",
                            lambda: calls.append("pause_bed"))
        monkeypatch.setattr(app.audio, "resume_ambience",
                            lambda: calls.append("resume_bed"))
        monkeypatch.setattr(app.audio, "stop_ambience",
                            lambda: calls.append("stop_bed"))
        monkeypatch.setattr(app.audio, "pause_music", lambda: calls.append("pause_music"))
        monkeypatch.setattr(app.audio, "stop_music", lambda: calls.append("stop_music"))

        app.enter_pause(play)
        app.update(0.016)
        assert "pause_music" in calls and "pause_bed" in calls
        assert "stop_music" not in calls and "stop_bed" not in calls

        calls.clear()
        app.pause_select()             # resume
        assert "resume_bed" in calls

        calls.clear()
        app.pause_index = 1            # restart stage from the pause menu
        app.pause_select()
        assert "stop_bed" in calls and "stop_music" in calls

    def test_escape_resume_also_restores_ambience(self, app, monkeypatch):
        """Regression: Escape-to-resume only flipped the screen back, so the
        water/wind beds stayed suspended until a stage restart (music came
        back because the policy tick unpauses it - the beds had no such
        safety net).  Every resume path must restore the soundscape."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        calls = []
        monkeypatch.setattr(app.audio, "resume_ambience",
                            lambda: calls.append("resume_bed"))
        app.enter_pause(play)
        app.handle_keydown(27)         # escape resumes directly
        assert app.screen == "game"
        assert calls == ["resume_bed"], calls

    def test_music_only_while_stage_in_progress(self, app, monkeypatch):
        # The theme plays only while a stage is in progress; menus,
        # dialogs and the results screen are all silent.  Pausing the
        # stage suspends the song in place; resuming continues it - a
        # pause must never restart the theme from the top.
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        calls = []
        monkeypatch.setattr(app.audio, "stop_music", lambda: calls.append("stop"))
        monkeypatch.setattr(app.audio, "play_music", lambda: calls.append("play"))
        monkeypatch.setattr(app.audio, "pause_music", lambda: calls.append("pause"))

        app.update(0.016)              # stage racing: music wanted
        assert calls and calls[-1] == "play"

        calls.clear()
        app.enter_pause(play)          # paused: suspended, not stopped
        app.update(0.016)
        assert calls[-1] == "pause"
        assert "stop" not in calls

        calls.clear()
        app.screen = "game"            # resumed: continues where it left off
        app.update(0.016)
        assert calls[-1] == "play"
        assert "stop" not in calls

        calls.clear()
        app.show_main_menu()           # menus: silent
        app.update(0.016)
        assert calls[-1] == "stop"

    def test_death_stops_music_until_respawn(self, app, monkeypatch):
        # Authentic to the original: music stops at death and only returns
        # once you respawn with your remaining lives (via the policy).
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        calls = []
        monkeypatch.setattr(app.audio, "stop_music", lambda: calls.append("stop"))
        monkeypatch.setattr(app.audio, "play_music", lambda: calls.append("play"))
        play.lives = 2
        play.apply_fall()
        app.update(5.0)                # the death dialog waits out the fall cry
        assert app.dialog is not None
        app.update(0.016)              # policy sees the dialog: stays stopped
        assert "play" not in calls
        app.dismiss_dialog()           # respawn
        app.update(0.016)              # policy brings the theme back
        assert calls[-1] == "play"

    def test_fall_costs_life_then_gameover(self, app):
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.lives = 2
        play.apply_fall()
        assert play.lives == 1 and app.screen == "game"
        play.apply_fall()
        assert play.lives == 0 and app.screen == "gameover"

    def test_god_mode_absorbs_fall(self, app):
        """The star's promise: while god mode is live, a fall costs nothing."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.man.give_god()
        assert play.man.god_active
        play.lives = 2
        play.apply_fall()
        assert play.lives == 2, "god mode must absorb the fall"
        assert app.dialog is None, "no death dialog while invincible"
        # ... and the protection expires with the star
        play.man.god_timer = 0.0
        play.apply_fall()
        assert play.lives == 1, "after expiry a fall costs a life again"

    def test_god_mode_blocks_edge_fall_event(self, app):
        """Riding the wall under god mode never emits a fall event."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.man.give_god()
        # park the man beyond the fall angle for longer than the grace
        play.man.angle = 10.0
        for _ in range(int((FALL_GRACE + 0.5) * 60)):
            play.update(1 / 60, 0.0, False)
        assert play.lives == 3, "god mode must prevent the edge fall"
        assert app.dialog is None

    def test_countdown_has_no_extra_beeps(self, app):
        """The countdown WAV and the spoken ticks carry it; no sine beeps."""
        app.start_stage(0)
        app.play.phase = "countdown"
        app.speech.beeps = []
        for _ in range(int(3.5 * 60)):
            app.update(1 / 60)
            if app.play.phase != "countdown":
                break
        assert app.speech.beeps == [], f"stray countdown beeps: {app.speech.beeps}"

    def test_god_mode_expiry_is_announced(self, app, monkeypatch):
        """The star's end must be audible: a contour plus a spoken line."""
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        heard, tones = [], []
        monkeypatch.setattr(app.speech, "say",
                            lambda text, urgent=False, cue=None: heard.append(text))
        monkeypatch.setattr(app.speech, "beep",
                            lambda pattern, urgent=False: tones.append(tuple(pattern)))
        play.man.give_god()
        play.man.god_timer = 0.05
        for _ in range(5):  # tick past zero
            play.update(1 / 60, 0.0, False)
            if not play.man.god_active:
                break
        assert not play.man.god_active
        assert "God mode over." in heard
        assert tones, "expiry must play a sound contour"
        # and it fires exactly once, not on every following frame
        heard.clear()
        play.update(1 / 60, 0.0, False)
        assert "God mode over." not in heard

    def test_go_sting_removed(self, app, monkeypatch):
        """The start WAV and spoken 'Go' carry the launch; no sine sting."""
        app.start_stage(0)
        tones = []
        monkeypatch.setattr(app.speech, "beep",
                            lambda pattern, urgent=False: tones.append(tuple(pattern)))
        for _ in range(int(4.5 * 60)):
            app.update(1 / 60)
            if app.play.phase != "countdown":
                break
        assert tones == [], f"stray launch beeps: {tones}"
        assert app.play.phase == "racing"


class TestMusicSeamless:
    def test_trim_silence_shortens_tail(self):
        import numpy as np
        import pygame

        if not pygame.get_init():
            pygame.mixer.init(22050, -16, 2, 1024)
        n = 22050
        arr = np.zeros((n, 2), dtype=np.int16)
        arr[1000:18000] = 8000  # wide loud span, silence on both ends
        snd = pygame.sndarray.make_sound(arr.copy())
        trimmed = AudioEngine._trim_silence(snd)
        out = pygame.sndarray.array(trimmed)
        assert out.shape[0] < n  # silence removed
        assert out.shape[0] >= 4410

    def test_set_music_volume_clamps(self, app):
        app.audio.set_music_volume(150)
        assert app.audio.music_volume == 100
        app.audio.set_music_volume(-5)
        assert app.audio.music_volume == 0
        app.audio.set_music_volume(app.options.music_volume or 50)


class TestVoiceVariants:
    def test_sfx_key(self):
        from waterslide.audio import sfx_key

        assert sfx_key("loop", "female") == "loop_female"
        assert sfx_key("loop", "") == "loop"

    def test_playstate_uses_voice_option(self, app):
        app.options.voice = "female"
        app.start_stage(0)
        assert app.play.gender == "female"
        app.options.voice = "male"

    def test_female_loop_variant_loaded(self):
        ae = app_audio()
        assert "loop_female" in ae._sounds and ae._sounds["loop_female"] is not None
        assert "fall_male" in ae._sounds and ae._sounds["fall_male"] is not None


def app_audio():
    ae = AudioEngine(None)
    return ae


class TestDialogSequencing:
    """Regression: the results dialog was being truncated to its first
    word by the menu line queued right after it (every call used
    urgent=True, and urgent speech interrupts)."""

    def test_non_urgent_messages_wait_for_busy_engine(self):
        from waterslide.speech import Speech, _estimate_seconds

        text = ("Stage 1 complete! Time 25.4 seconds, 12 of 17 diamonds, "
                "final score 5,230.")
        assert _estimate_seconds(text, 50) > 2.0
        # a busy engine holds non-urgent lines back; urgent preempts;
        # an idle engine plays immediately
        assert Speech._queue_delay(False, 100.0, 0.0) > 2.0
        assert Speech._queue_delay(True, 100.0, 0.0) == 0.0
        assert Speech._queue_delay(False, 0.0, 100.0) == 0.0

    def test_results_speak_before_menu_as_non_urgent(self, app, monkeypatch):
        app.start_stage(0)
        play = app.play
        play.phase = "racing"
        play.man.finished = True
        calls = []
        monkeypatch.setattr(app.speech, "say",
                            lambda text, urgent=False, cue=None:
                            calls.append((text, urgent)))
        app.finish_stage(play)
        assert app.dialog is not None, "results open as a modal dialog"
        assert calls, "results dialog must be spoken"
        assert "Stage 1 complete" in calls[0][0]
        assert calls[0][1] is False, "urgent results would be interruptible"
        # the menu line is only announced after the dialog is dismissed
        app.dismiss_dialog()
        assert any("next stage" in c[0] or "replay stage" in c[0] for c in calls)

    def test_shut_up_drop_pending_removes_queued_lines(self):
        from waterslide.speech import Speech

        class NoThread(Speech):
            def _start_thread(self):
                self._thread = None

        s = NoThread.__new__(NoThread)  # build state manually, no thread
        s.enabled = True
        s._busy_until = 0.0
        s._last_spoken = {}
        import queue as _queue
        s._queue = _queue.Queue()
        s._queue.put(("say", "stale line", False, None, None, 0.0))
        s._queue.put(("say", "another stale line", True, None, None, 0.0))
        s.shut_up(drop_pending=True)
        tags = [item[0] for item in list(s._queue.queue)]
        assert "say" not in tags  # chatter dropped, stop remains
        s._queue.put(("say", "kept line", False, None, None))
        s.shut_up()               # default: pending lines are re-queued
        texts = [i[1] for i in list(s._queue.queue) if i[0] == "say"]
        assert "kept line" in texts

    def test_busy_engine_does_not_delay_fresh_navigation(self):
        """The arrow-key lag: lines enqueued while the engine is busy are
        stamped with their wait at enqueue time and share one slot, so a
        second navigation line is not pushed behind the first."""
        from waterslide.speech import Speech

        class NoThread(Speech):
            def _start_thread(self):
                self._thread = None

        s = NoThread.__new__(NoThread)
        s.enabled = True
        s._busy_until = __import__("time").monotonic() + 3.0  # engine mid-sentence
        s._last_spoken = {}
        s._queue = __import__("queue").Queue()
        s.say("Replay stage, 2 of 4.")
        s.say("Stage select, 3 of 4.")
        items = [i for i in list(s._queue.queue) if i[0] == "say"]
        assert len(items) == 2
        # both pinned to the same enqueue-time slot: the second line's
        # speak_at must not include the first line's wait
        assert abs(items[0][-1] - items[1][-1]) < 0.1
        assert items[1][-1] <= s._busy_until

    def test_navigation_resets_speech_state_immediately(self):
        """shut_up clears busy/dupe state on the calling thread, so the
        next navigation line speaks at once instead of waiting out the
        interrupted line's estimate or being eaten by the dupe guard."""
        from waterslide.speech import Speech

        class NoThread(Speech):
            def _start_thread(self):
                self._thread = None

        s = NoThread.__new__(NoThread)
        s.enabled = True
        s._busy_until = 10**9
        s._last_spoken = {}
        s._queue = __import__("queue").Queue()
        s.say("Replay stage, 2 of 4.")          # would wait / be deduped
        s.shut_up(drop_pending=True)             # what the menu movers do
        s.say("Next stage, 1 of 4.")
        delay = Speech._queue_delay(False, s._busy_until, __import__("time").monotonic())
        assert delay == 0.0, "after shut_up the next line must not wait"


class TestOptionsChooser:
    """Multi-choice options step through a list with left/right."""

    def test_rate_steps_through_list_with_position(self, app):
        app.menu_index = 5
        app.menu_select()  # options screen
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech rate")
        app.option_index = row
        before = app.options.speech_rate
        app.option_adjust(1)
        assert app.options.speech_rate != before
        assert app._chooser_lines[0] == "speech rate"
        assert any(l.startswith("* ") for l in app._chooser_lines[1:])

    def test_right_arrow_walks_every_choice(self, app):
        """Regression: choice lists rebuilt as 'current first' made right
        arrow ping-pong between two values.  Stepping right must visit
        every choice before wrapping to the start."""
        app.menu_index = 5
        app.menu_select()
        for key, attr in (("speech rate", "speech_rate"),
                          ("voice", "voice"),
                          ("speech engine", "speech_engine")):
            row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == key)
            app.option_index = row
            n = len(app._option_choices(key))
            start = getattr(app.options, attr)
            seen = [start]
            for _ in range(n):
                app.option_adjust(1)
                seen.append(getattr(app.options, attr))
            assert len(set(seen)) == n, f"{key}: right must walk all {n}, saw {seen}"
            assert seen[-1] == start, f"{key}: full cycle returns to start"

    def test_left_and_right_are_mirror_images(self, app):
        app.menu_index = 5
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech rate")
        app.option_index = row
        start = app.options.speech_rate
        app.option_adjust(1)
        right_target = app.options.speech_rate
        app.option_index = row
        app.options.speech_rate = start
        app.option_adjust(-1)
        left_target = app.options.speech_rate
        choices = app._option_choices("speech rate")
        i = choices.index(start)
        assert right_target == choices[(i + 1) % len(choices)]
        assert left_target == choices[(i - 1) % len(choices)]

    def test_left_and_right_move_both_ways(self, app):
        app.menu_index = 4
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "voice")
        app.option_index = row
        start = app.options.voice
        app.option_adjust(1)
        other = app.options.voice
        assert other != start
        app.option_adjust(-1)
        assert app.options.voice == start

    def test_speech_mode_row_opens_chooser_on_enter(self, app):
        app.menu_index = 5
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech")
        app.option_index = row
        app.option_toggle()  # multi-choice now: opens the chooser
        assert app._chooser_lines and app._chooser_lines[0] == "speech"

    def test_speech_mode_chooser_walks_all_three_modes(self, app):
        """The speech-mode row cycles auto - on - off - auto and activates."""
        app.options.speech = "auto"
        app.speech.mode_pref = "auto"
        app.menu_index = 5
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech")
        app.option_index = row
        for expected in ("on", "off", "auto"):
            app.option_adjust(1)
            assert app.options.speech == expected
            assert app.speech.mode_pref == expected

    def test_speech_mode_on_and_off_force_speech(self, app):
        app.menu_index = 5
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech")
        app.option_index = row
        app.option_adjust(1)  # auto -> on
        assert app.speech.enabled is True
        app.option_adjust(1)  # on -> off
        assert app.speech.enabled is False
        # leave the shared module app in a neutral state
        app.options.speech = "auto"
        app.speech.mode_pref = "auto"
        app.speech.enabled = True

    def test_enter_on_multi_choice_opens_chooser_without_crashing(self, app):
        """Regression: Enter used to call a removed cycle helper and crash."""
        app.menu_index = 5
        app.menu_select()
        attrs = {"voice": "voice", "speech engine": "speech_engine",
                 "speech output": "speech_output", "speech rate": "speech_rate",
                 "speech volume": "speech_volume", "speech pitch": "speech_pitch"}
        for key, attr in attrs.items():
            row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == key)
            app.option_index = row
            before = getattr(app.options, attr)
            app.option_toggle()  # must not raise
            assert app._chooser_lines, f"{key}: Enter must open the chooser"
            assert app._chooser_lines[0] == key
            after = getattr(app.options, attr)
            assert after != before or key == "speech output", f"{key}: value stepped"

    def test_engine_change_persists(self, app):
        app.menu_index = 5
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech engine")
        app.option_index = row
        app.option_adjust(1)
        from waterslide.records import Records
        assert Records.load().options.speech_engine == app.options.speech_engine

    def test_engine_switch_updates_output_and_voice_choices(self, app):
        """Regression: after switching engines the choosers kept showing the
        previous engine's outputs and voices (Prism's SAPI voices appeared
        under accessible_output2)."""
        from waterslide import speech as speech_mod

        app.menu_index = 5
        app.menu_select()

        def fake_probe(engine):
            return lambda timeout=2.0: {
                "engine": engine,
                "outputs": [f"{engine}-out-a", f"{engine}-out-b"],
                "voices": [f"{engine}-voice-1", f"{engine}-voice-2"],
                "ok": True, "error": "", "build_errors": [],
            }

        app.speech.engine_pref = "prism"
        app.speech.probe = fake_probe("prism")
        prism_outs = app._speech_outputs()
        prism_voices = app._speech_voices()

        app.speech.engine_pref = "ao2"
        app.speech.probe = fake_probe("ao2")
        ao2_outs = app._speech_outputs()
        ao2_voices = app._speech_voices()

        # the probes themselves must differ, then the chooser must agree
        assert prism_outs != ao2_outs or prism_voices != ao2_voices, "probes are not engine-specific"
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "speech output")
        app.option_index = row
        app.option_adjust(1)  # walks into the current engine's list, re-probing
        assert app._chooser_lines, "chooser must render"
        assert app._chooser_lines[0] == "speech output"
        labels = " ".join(app._chooser_lines[1:])
        expected = sorted("auto" in app._option_choices("speech output") and
                          [x for x in app._option_choices("speech output") if x != "auto"])
        for choice in expected:
            assert choice in labels, f"chooser missing {choice!r} for the active engine"
        app.speech.engine_pref = "prism"
        app.speech.probe = speech_mod.Speech.probe.__get__(app.speech)
        # put the shared app's worker back on a clean Prism/auto state
        app.options.speech_output = "auto"
        app.speech.configure(engine="prism", output="auto", rebuild=True)

    def test_engine_change_snapshots_stale_output_and_voice(self, app):
        """Switching engines drops stored output/voice values the new engine
        does not have, so the rows can never show the old engine's state."""
        app.options.speech_engine = "prism"
        app.options.speech_output = "prism-only-output"
        app.options.speech_voice = "Prism Voice"
        app.speech.engine_pref = "ao2"
        app.speech.probe = lambda timeout=2.0: {
            "engine": "ao2", "outputs": ["nvda", "sapi5"], "voices": [],
            "ok": True, "error": "", "build_errors": [],
        }
        app._option_apply("speech engine", "ao2")
        assert app.options.speech_output == "auto"
        assert app.options.speech_voice == ""
        # restore the real facade and the worker's Prism state for the
        # shared module app
        from waterslide import speech as speech_mod
        app.speech.probe = speech_mod.Speech.probe.__get__(app.speech)
        app.speech.configure(engine="prism", output="auto", voice="", rebuild=True)

    def test_volume_rows_are_sliders_with_twenty_one_steps(self, app):
        for key in ("music volume", "sound volume"):
            choices = app._option_choices(key)
            assert choices == list(range(0, 101, 5)), key

    def test_volume_rows_walk_full_range_both_ways(self, app):
        app.menu_index = 5
        app.menu_select()
        for key, attr in (("music volume", "music_volume"),
                          ("sound volume", "sound_volume")):
            row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == key)
            app.option_index = row
            app.options.__setattr__(attr, 50)
            app.option_adjust(1)
            assert getattr(app.options, attr) == 55, f"{key}: right must raise"
            app.option_adjust(-1)
            assert getattr(app.options, attr) == 50, f"{key}: left must lower"
            app.option_adjust(-1)
            assert getattr(app.options, attr) == 45

    def test_volume_slider_applies_to_audio(self, app):
        app.menu_index = 5
        app.menu_select()
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "music volume")
        app.option_index = row
        app.options.music_volume = 50
        app.option_adjust(1)
        assert app.audio.music_volume == 55
        row = next(i for i, (k, _) in enumerate(app._option_rows()) if k == "sound volume")
        app.option_index = row
        app.options.sound_volume = 50
        app.option_adjust(1)
        assert app.audio.sound_volume == 55
        app.audio.stop_music()

    def test_option_hint_never_says_enter_for_choices(self, app):
        """The multi-choice hint is the left/right instruction; 'enter for
        choices' mislead players into pressing Enter first."""
        app.menu_index = 5
        app.menu_select()
        rows = app._option_rows()
        for idx, (key, _) in enumerate(rows):
            app.option_index = idx
            heard = []
            app.speech.say = lambda text, urgent=False, cue=None: heard.append(text)
            app._announce_option()
            line = " ".join(heard).lower()
            assert "for choices" not in line, f"{key}: stale hint {line!r}"
            if key in app.MULTI_CHOICE:
                assert "left and right" in line, f"{key}: missing slider hint"

    def test_announce_hint_varies_by_row_kind(self, app):
        app.menu_index = 5
        app.menu_select()
        heard = []
        app.speech.say = lambda text, urgent=False, cue=None: heard.append(text)
        slider_row = next(i for i, (k, _) in enumerate(app._option_rows())
                          if k == "music volume")
        app.option_index = slider_row
        app._announce_option()
        assert "left and right to adjust" in " ".join(heard).lower()
        heard.clear()
        toggle_row = next(i for i, (k, _) in enumerate(app._option_rows())
                          if k == "speedrun mode")
        app.option_index = toggle_row
        app._announce_option()
        assert "enter to change" in " ".join(heard).lower()


class TestLoopPassThrough:
    def test_pass_through_calls_out_after_exiting(self, app):
        app.start_stage(0)
        play = app.play
        calls = []
        import types
        monkey = app.audio  # keep audio quiet
        monkey.enabled = False
        class FakeTrack:
            pass
        # Fabricate a one-cluster track situation: mark the first cluster
        # as announced, then run the man past its exit.
        t = play.track
        clusters = play._loop_clusters()
        if not clusters:
            import pytest
            pytest.skip("track has no loop clusters")
        s, e = clusters[0]
        entry = t._cum_len[s]
        play._loops_announced.add(s)
        exit_d = t._cum_len[min(e + 2, len(t.handles) - 1)]
        played = []
        play.app.audio.play = lambda key, pan=0.0, vol=1.0: played.append(key)
        play.app.speech.say = lambda text, urgent=False, cue=None: calls.append(text)
        play.man.dist = entry + 10.0  # entering the loop
        play._check_loops(play.man.dist)
        # the scream fires for whichever voice is active (the module-wide
        # app fixture shares option state across tests)
        assert played and played[0].startswith("loop"), f"scream at entry, got {played}"
        play.man.dist = exit_d + 50.0  # exiting
        play._check_loops(play.man.dist)
        assert len(played) == 1, "the scream fires once, not on exit"
        assert not any("Loop complete" in c for c in calls), "exit is silent by design"


class TestHeartPickup:
    def test_heart_is_pickable_end_to_end(self, app):
        """Regression: hearts were pinned to the unreachable top of the
        tube, so no playtest ever produced an extra life."""
        app.start_stage(0)
        play = app.play
        heart = next(it for it in play.items if it.cls == "heart")
        total = play.track.total_length
        # park just before the heart, aimed straight at it
        play.man.dist = heart.t * total - 5.0
        play.man.angle = heart.angle
        play.countdown_t = 0.01  # skip the countdown
        lives_before = play.lives
        heard = []
        app.speech.say = lambda text, urgent=False, cue=None: heard.append(text)
        for _ in range(240):
            play.update(1 / 60, 0.0, False)
            if heart.collected:
                break
        assert heart.collected, "heart must be collectible"
        assert play.lives == lives_before + 1
        assert any("Extra life" in c for c in heard)
        app.audio.stop_ambience()


class TestPlayerName:
    def test_name_entry_commits_and_persists(self, app):
        app.menu_index = 4  # player name
        app.menu_select()
        assert app.screen == "nameentry"
        for ch in "Zed":
            app._nameentry_key(ord(ch), ch)
        app._nameentry_key(8, "")  # backspace deletes 'd'
        app._nameentry_key(ord("d"), "d")
        app._nameentry_key(13, "")
        assert app.records.player_name == "Zed"
        assert app.screen == "main"

    def test_name_entry_escape_cancels(self, app):
        before = app.records.player_name
        app.menu_index = 4
        app.menu_select()
        app._nameentry_key(ord("Q"), "Q")
        app._nameentry_key(27, "")
        assert app.records.player_name == before
        assert app.screen == "main"
