"""Shared fixtures.

Keep every test away from the user's real ``%APPDATA%``: several suites
build a real App, and ``Records.save()`` writes
``%APPDATA%\\WaterslideFusion\\records.cfg`` on every option change.
Without an isolated APPDATA, running the tests rewrote the player's real
saved volumes (the sound-effects mute test literally set the developer's
config to 0%).  Point APPDATA at a temp directory for the whole session.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_appdata(tmp_path, monkeypatch):
    """Per-test APPDATA isolation.

    Several suites build a real App and ``Records.save()`` writes
    ``%APPDATA%\\WaterslideFusion\\records.cfg`` on every option change;
    without isolation the tests would rewrite the player's real settings
    (they once set the developer's sound volume to 0).  Per-test scope
    also keeps tests from leaking settings into each other: a voice
    change in one test must not change what a later test hears.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
