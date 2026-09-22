#!/usr/bin/env python
"""Dump human-readable summaries of the original .prt track files.

Examples:
    python tools/dump_tracks.py                     # summary of all 9 stages
    python tools/dump_tracks.py 3                   # detail for stage 3
    python tools/dump_tracks.py 3 --curves          # include per-handle curve rows
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from waterslide.track import Track, load_all_tracks


def summary(t: Track, index: int) -> str:
    counts = {}
    for it in t.items:
        counts[it.cls] = counts.get(it.cls, 0) + 1
    twisted = sum(1 for h in t.handles if h.is_loop)
    full_loops = sum(1 for h in t.handles if 180 in (h.roll_in, h.roll_out))
    return (
        f"Stage {index}: declared {t.declared_length} units, plays at {t.total_length:.0f}; "
        f"{len(t.handles)} handles, {t.num_profiles} cross-sections, "
        f"{twisted} rolled, {full_loops} with 180-degree inversions\n"
        f"  items: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) +
        f"  (total {len(t.items)})"
    )


def detail(t: Track, index: int, curves: bool) -> str:
    out = [summary(t, index), ""]
    out.append("  spline handles (start -> finish):")
    for i, h in enumerate(t.handles):
        roll = ""
        if h.roll_in == 180 or h.roll_out == 180:
            roll = f"  INVERSION roll {h.roll_in:.0f}->{h.roll_out:.0f}"
        elif h.is_loop:
            roll = f"  roll {h.roll_in:.0f}->{h.roll_out:.0f}"
        prof = f"profile {h.profile}"
        out.append(
            f"   #{i:02d} pos=({h.pos[0]:.0f},{h.pos[1]:.0f},{h.pos[2]:.0f}) "
            f"{prof}{roll}"
        )
        if curves:
            out.append(f"        tin={h.tin} tout={h.tout} flags={h.flags}")
    out.append("")
    out.append("  first items on the slide:")
    for it in t.items[:12]:
        out.append(f"   {it.cls:8s} t={it.t:.3f} angle={it.angle:.2f} rad")
    return "\n".join(out)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    curves = "--curves" in sys.argv
    tracks = load_all_tracks()
    if not tracks:
        print("No tracks found. Run: python -m waterslide.ipa_extract")
        return 1
    if not args:
        for i, t in enumerate(tracks, 1):
            print(summary(t, i))
        return 0
    idx = int(args[0])
    t = tracks[idx - 1]
    print(detail(t, idx, curves))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
