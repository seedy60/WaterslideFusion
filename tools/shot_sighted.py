"""Sighted-mode verification: run the game speechless in a real window,
capture the 3D framebuffer, and prove pixels are actually being drawn.

This exists because the primary audience cannot see the screen.  It
asserts, programmatically:

* the 3D renderer produces non-uniform frames (geometry, not a blank
  clear color),
* the GL HUD text renders (bright pixels in the HUD strip),
* the V key path works: 2D -> 3D -> 2D window recreation with a live
  stage renderer,
* sighted mode (speech off) never builds a speech engine: nothing can
  talk, ever, through an entire simulated stage.

Run from the repo root:  uv run python tools/shot_sighted.py
Screenshots land in screenshots/ as PNG files.
"""

import os
import sys
import tempfile
import time

os.environ.pop("SDL_VIDEODRIVER", None)  # a real window is the point

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Sighted mode via config: speech off, music off (this is a robot running
# it, not a player), in a throwaway APPDATA so real settings are untouched.
tmp_cfg = tempfile.mkdtemp(prefix="wsf-shot-")
os.environ["APPDATA"] = tmp_cfg
cfg_dir = os.path.join(tmp_cfg, "WaterslideFusion")
os.makedirs(cfg_dir, exist_ok=True)
with open(os.path.join(cfg_dir, "records.cfg"), "w", encoding="utf-8") as fh:
    fh.write("[options]\nspeech,off\nmusic,0\nsound,0\n")

from waterslide.app import App, STAGE_NAMES, WINDOW_SIZE  # noqa: E402
from waterslide.render3d import Renderer3D  # noqa: E402

SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots")
os.makedirs(SHOTS, exist_ok=True)


def grab(renderer, name: str) -> dict:
    """Read the GL framebuffer, save it, and return pixel statistics."""
    import pygame

    data = renderer.ctx.screen.read(components=3)
    w, h = renderer.surface.get_size()
    surf = pygame.image.frombuffer(data, (w, h), "RGB")
    surf = pygame.transform.flip(surf, False, True)  # GL is bottom-up
    pygame.image.save(surf, os.path.join(SHOTS, name))
    # full-frame color histogram (every 4th pixel): a fog-only or blank
    # frame yields a handful of colors, a rendered striped tube yields 100+
    samples = [surf.get_at((x, y))[:3]
               for y in range(0, h, 4) for x in range(0, w, 4)]
    uniq = len(set(samples))
    # HUD strip: the score/progress lines draw near the top left
    hud = [surf.get_at((x, y))[:3]
           for y in range(24, 110, 3) for x in range(36, 460, 4)]
    bright = sum(1 for r, g, b in hud if r + g + b > 450)
    print(f"  {name}: {w}x{h}, unique colors={uniq}, hud bright px={bright}")
    return {"unique": uniq, "hud": bright}


def draw_game_frame(app, flip: bool = True) -> None:
    """One frame exactly as the run loop draws it (3D branch).

    With flip=False the frame stays in the back buffer so the caller can
    read it deterministically (a read after pygame.display.flip() races
    the swap and can return the previous frame).
    """
    import pygame

    if app.renderer is not None and app.screen == "game" and app.play:
        lines = [
            (STAGE_NAMES[app.play.stage_number - 1], 40, 30),
            (f"score {app.play.score}   lives {app.play.lives}   "
             f"diamonds {app.play.rings_got}/{app.play.rings_total}", 40, 62),
            (f"progress {int(app.play.man.progress() * 100)}%   "
             f"speed {app.play.speed_display()[0]} "
             f"{'mph' if app.options.speed_units == 'mph' else 'km/h'}", 40, 94),
        ]
        app.renderer.draw(app.play, lines)
        if flip:
            pygame.display.flip()


def main() -> int:
    app = App(headless=False)
    app.start()
    try:
        assert app.options.speech == "off", "config did not apply"
        assert app.speech.enabled is False, "speech must be off in sighted mode"
        assert app.use_3d is True, "off mode must default to the 3D view"
        print(f"boot: speech off, engine never built = "
              f"{app.speech.engine_kind == 'none'}, window 3D = {app.use_3d}")

        app.start_stage(0)
        dt = 1 / 60
        # countdown frame
        for _ in range(6):
            app.update(dt)
            draw_game_frame(app)
        assert app.renderer is not None, "renderer did not build for the stage"
        draw_game_frame(app, flip=False)
        stats = grab(app.renderer, "stage1_countdown.png")
        import pygame

        pygame.display.flip()
        assert stats["unique"] > 64, "countdown frame looks blank"
        assert stats["hud"] > 40, "HUD text did not render"

        # racing frames with items ahead
        for _ in range(int(6.0 * 60)):
            app.update(dt)
            if app.play is None or app.play.phase == "finished":
                break
            draw_game_frame(app)
        draw_game_frame(app, flip=False)
        stats = grab(app.renderer, "stage1_racing.png")
        pygame.display.flip()
        assert stats["unique"] > 64, "racing frame looks blank"
        assert stats["hud"] > 40, "HUD text did not render in race"

        # --- the V path: back to 2D, then 3D again, renderer rebuilt
        app._toggle_3d()  # -> 2D
        assert app.use_3d is False and app.renderer is None
        app.update(dt)
        assert app.play is not None, "stage died across the V toggle"
        app._toggle_3d()  # -> 3D again, renderer rebuilds on update
        app.update(dt)
        assert app.use_3d is True
        assert isinstance(app.renderer, Renderer3D), "renderer did not rebuild"
        draw_game_frame(app, flip=False)
        stats = grab(app.renderer, "stage1_after_v_toggle.png")
        pygame.display.flip()
        assert stats["unique"] > 64, "frame after V toggle looks blank"

        # --- the audit: a whole simulated stage, engine must stay unbuilt
        for _ in range(int(12.0 * 60)):
            app.update(dt)
            if app.play is None or app.play.phase == "finished":
                break
        assert app.speech.engine_kind == "none", (
            f"sighted mode built a speech engine: {app.speech.engine_kind!r}"
        )
        print("audit: engine never built through countdown + race + toggles")
        print("SCREENSHOT VERIFICATION PASSED")
        return 0
    finally:
        try:
            app.quit()
        except Exception:
            pass
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
