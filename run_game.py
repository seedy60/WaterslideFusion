#!/usr/bin/env python
"""Launch Waterslide Fusion (accessible port of Waterslide Extreme).

Usage:
    python run_game.py                 # play
    python run_game.py --selftest      # headless smoke test
    python run_game.py --ipa PATH     # (re)extract assets from the ipa first
"""

import sys

from waterslide.app import App


def main() -> int:
    argv = sys.argv[1:]
    if "--ipa" in argv:
        i = argv.index("--ipa")
        path = argv[i + 1] if i + 1 < len(argv) else None
        from waterslide.ipa_extract import extract

        rep = extract(path or "IPA/Waterslide Extreme (iOS).ipa")
        print(f"extracted {sum(1 for _, s in rep['files'] if s != 'missing')} files to assets/")
        argv = [a for j, a in enumerate(argv) if not (a == "--ipa" or (j == i + 1))]
    return App.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
