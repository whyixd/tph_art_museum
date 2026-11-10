"""Frame rendering and RGB byte quantization."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .effects import (
    WEATHER_TIMING_FREE, EffectContext, Rgb, clamp01, completion_frames, preset_for_unit, same_on_every_unit, sample_effect,
)


def _virtual_height(led_index: int, led_count: int, global_frame: int, fps: int, rpm: int, phase_seed: float) -> float:
    """Experimental first-arc estimate; shared wiring cannot address both arcs separately."""
    progress = 0.5 if led_count <= 1 else led_index / (led_count - 1)
    angle = math.pi * progress
    x = -math.cos(angle) * 2.5
    z = -0.035
    phase = phase_seed + global_frame * math.tau * (rpm / 60) / fps
    return math.sin(phase) * x - math.cos(phase) * z


def _flow(value: Any) -> tuple[float, float] | None:
    return None if value is None else (float(value[0]), float(value[1]))


def render_frame(
    preset: Mapping[str, Any],
    unit: Mapping[str, Any],
    global_frame: int,
    *,
    unit_count: int = 9,
    fps: int = 40,
    rpm: int = 190,
    phase_frame: int | None = None,
    raining: bool = False,
    cycle_frames: int | None = None,
    color_override: Mapping[str, Any] | None = None,
    shared: dict[Any, list[Rgb]] | None = None,
) -> list[Rgb]:
    """Render one frame; ``global_frame`` counts from playback (or step) start.

    ``raining`` switches effects to their rain lines (see effects.RAIN_LINES).
    ``cycle_frames`` wraps the frame for count-based sequence steps.
    ``shared`` (one dict per output frame) lets units that show the same picture reuse one render
    (see effects.same_on_every_unit); callers must not modify the returned list.
    """
    if type(global_frame) is not int or global_frame < 0:
        raise ValueError("global_frame must be a nonnegative integer")
    count = int(unit["logicalPixelCount"])
    key = None
    if shared is not None and int(unit["frameOffset"]) == 0 and same_on_every_unit(preset, raining):
        key = (id(preset), id(color_override), global_frame, cycle_frames, count, unit_count, fps)
        if key in shared:
            return shared[key]
    if "unitVariation" in preset:
        # 各燈台參數不同時，循環依該台自己的週期。 / Per-unit values: the wrap uses this unit's own cycle.
        preset = preset_for_unit(preset, int(unit["unitId"]) - 1, unit_count)
        if cycle_frames is not None:
            cycle_frames = completion_frames(preset, fps, unit_count) or cycle_frames
    # 共用時間表的效果不套用各台錯開。 / Shared-schedule effects skip unit offsets.
    timing_free = preset["effectId"] in WEATHER_TIMING_FREE
    effect_frame = global_frame
    if cycle_frames is not None:
        effect_frame %= cycle_frames
    controller_frame = effect_frame + (0 if timing_free else int(unit["frameOffset"]))
    phase = float(unit.get("virtualPhaseRadians", 0))
    phase_frame = global_frame if phase_frame is None else phase_frame
    if color_override is not None and preset["color"].get("weatherPalette", True):
        # 天氣色盤只換顏色：漸層效果保留自己的方向（含中途切換），純色效果用色盤的方向。
        # The palette only swaps colors: gradient effects keep their own axis (and axis change); solid ones use the palette's.
        own = preset["color"]
        color = {
            "mode": "gradient",
            "axis": own["axis"] if own["mode"] == "gradient" else color_override["axis"],
            "colors": tuple(
                tuple(stop[channel] for channel in ("r", "g", "b"))
                for stop in color_override["colors"]
            ),
        }
        if own["mode"] == "gradient" and "axisChange" in own:
            color["axisChange"] = own["axisChange"]
        preset = {**preset, "color": color}
    # 只有 world-upper-half 用到各台相位估計的高度。 / Only world-upper-half reads the phase-estimated height.
    upper_half = preset["effectId"] == "world-upper-half"
    projection = -math.sin(phase + phase_frame * math.tau * (rpm / 60) / fps)
    colors = [
        sample_effect(
            preset,
            EffectContext(
                controller_frame=controller_frame,
                controller_fps=fps,
                led_index=index,
                led_count=count,
                unit_index=int(unit["unitId"]) - 1,
                unit_count=unit_count,
                global_controller_frame=global_frame,
                unit_relative_y=_virtual_height(index, count, phase_frame, fps, rpm, phase) if upper_half else 0.0,
                unit_phase_projection=projection,
                unit_rank=unit.get("pillarRank"),
                unit_position=unit.get("position"),
                unit_flow=_flow(unit.get("flowPosition")),
            ),
            global_frame,
            raining,
        )
        for index in range(count)
    ]
    if key is not None:
        shared[key] = colors
    return colors


def rgb_bytes(frame: list[Rgb]) -> bytes:
    return bytes(
        math.floor(clamp01(channel) * 255 + 0.5)
        for pixel in frame
        for channel in pixel
    )
