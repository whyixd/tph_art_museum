"""Measure backend render cost per frame for many units and pixel counts.

Usage: uv run --locked python scripts/benchmark_render.py [--units 24] [--pixels 156 312 624] [--frames 40]
Reports milliseconds per output frame (all units), and the share of the frame budget at --fps.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moon_observe_backend.effects import EFFECT_IDS, default_preset, validate_preset  # noqa: E402
from moon_observe_backend.renderer import render_frame, rgb_bytes  # noqa: E402


def frame_ms(preset: dict, units: int, pixels: int, frames: int, fps: int, raining: bool) -> float:
    samples = []
    for frame in range(frames):
        start = time.perf_counter()
        for unit_id in range(1, units + 1):
            unit = {"unitId": unit_id, "logicalPixelCount": pixels, "frameOffset": 0}
            rgb_bytes(render_frame(preset, unit, frame * 7, unit_count=units, fps=fps, raining=raining))
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--units", type=int, default=24)
    parser.add_argument("--pixels", type=int, nargs="+", default=[156, 312, 624])
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--fps", type=int, default=40)
    parser.add_argument("--rain", action="store_true")
    parser.add_argument("--effects", nargs="*", default=list(EFFECT_IDS))
    args = parser.parse_args()
    budget = 1000 / args.fps
    print(f"units={args.units} fps={args.fps} budget={budget:.1f} ms/frame rain={args.rain}")
    print(f"{'effect':24}" + "".join(f"{p:>10}px" for p in args.pixels))
    worst = {pixels: 0.0 for pixels in args.pixels}
    for effect_id in args.effects:
        row = []
        for pixels in args.pixels:
            preset = validate_preset(default_preset(effect_id, pixels), pixels)
            ms = frame_ms(preset, args.units, pixels, args.frames, args.fps, args.rain)
            worst[pixels] = max(worst[pixels], ms)
            row.append(f"{ms:10.1f}ms")
        print(f"{effect_id:24}" + "".join(row), flush=True)
    print(f"{'worst / budget':24}" + "".join(f"{worst[p] / budget:11.0%} " for p in args.pixels))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
