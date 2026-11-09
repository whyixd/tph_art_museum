"""DMX fixture effects (ball lights, wall washers): pure Python port of frontend/src/fixtures.

Each fixture shows one colour per frame. Time is counted in controller frames, like the spinner
effects. Geometry is per group: ``normalized_height`` / ``normalized_x`` / ``normalized_y`` are the
fixture's position within its group (0..1, from the layout; the fixture order when the layout lacks
that axis), ``fixture_index`` is the order by id (the DMX chain order).

球燈／洗牆燈效果：每顆每影格一個顏色，與前端 frontend/src/fixtures 逐值一致。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .effects import BLACK, TAU, Rgb, _gradient, _number, _rgb, _round_half_up, clamp01, hash01

FIXTURE_SCHEMA_VERSION = 1
FIXTURE_AXES = ("height", "x", "y", "index")
FIXTURE_EFFECT_IDS = (
    "off",
    "fixture-solid",
    "fixture-breathing",
    "height-sweep",
    "space-wave",
    "fixture-twinkle",
    "fixture-chase",
)
# min, max, default, integer
FIXTURE_PARAMETERS: dict[str, dict[str, tuple[float, float, float, bool]]] = {
    "off": {},
    "fixture-solid": {},
    "fixture-breathing": {"breatheHz": (0.05, 1, 0.2, False), "minLevel": (0, 1, 0.1, False)},
    "height-sweep": {"speedHz": (0.05, 2, 0.25, False), "width": (0.05, 1, 0.3, False), "direction": (0, 1, 0, True)},
    "space-wave": {"speedHz": (0.05, 2, 0.2, False), "waveCount": (0.5, 4, 1, False), "direction": (0, 1, 0, True)},
    "fixture-twinkle": {"holdSeconds": (0.1, 10, 1, False), "density": (0.05, 1, 0.3, False)},
    "fixture-chase": {"stepSeconds": (0.05, 5, 0.3, False), "tailCount": (0, 10, 2, True), "direction": (0, 1, 0, True)},
}
# 雨天：每顆各自隨機相位、短暫閃亮後衰減，其餘時間保留 RAIN_FLOOR 的亮度。
# Rain: each fixture flashes at its own random phase and decays; between flashes it keeps RAIN_FLOOR.
RAIN_HZ = 0.7
RAIN_FLASH = 0.25
RAIN_FLOOR = 0.2
# 多雲：依 x 位置錯開，最左到最右相差 CLOUD_SPREAD_STEPS 個 cloud_step。
# Cloudy: offset by x position; the leftmost and rightmost fixtures differ by CLOUD_SPREAD_STEPS cloud steps.
CLOUD_SPREAD_STEPS = 8


@dataclass(frozen=True)
class FixtureContext:
    controller_frame: int
    controller_fps: float
    fixture_index: int = 0
    fixture_count: int = 1
    normalized_height: float = 0.5
    normalized_x: float = 0.5
    normalized_y: float = 0.5

    @property
    def normalized_index(self) -> float:
        return 0.5 if self.fixture_count <= 1 else self.fixture_index / (self.fixture_count - 1)


def validate_fixture_preset(value: Any) -> dict[str, Any]:
    """FixturePreset v1: {schemaVersion, effectId, parameters, color[, maxLevel]}."""
    keys = {"schemaVersion", "effectId", "parameters", "color"}
    if not isinstance(value, dict) or not keys <= set(value) or set(value) - keys - {"maxLevel"}:
        raise ValueError("fixture preset must contain schemaVersion, effectId, parameters, color (optional: maxLevel)")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported FixturePreset schemaVersion")
    effect_id = value["effectId"]
    if effect_id not in FIXTURE_EFFECT_IDS:
        raise ValueError(f"unknown fixture effectId: {effect_id!r}")
    raw_params = value["parameters"]
    if not isinstance(raw_params, dict) or set(raw_params) - set(FIXTURE_PARAMETERS[effect_id]):
        raise ValueError("invalid fixture effect parameters")
    params = {
        key: _number(raw_params.get(key, default), key, low, high, integer)
        for key, (low, high, default, integer) in FIXTURE_PARAMETERS[effect_id].items()
    }
    color = value["color"]
    if not isinstance(color, dict):
        raise ValueError("color must be an object")
    weather_palette = color.get("weatherPalette", True)
    if type(weather_palette) is not bool:
        raise ValueError("color weatherPalette must be a boolean")
    color = {key: entry for key, entry in color.items() if key != "weatherPalette"}
    if color.get("mode") == "solid" and set(color) == {"mode", "color"}:
        normalized_color: dict[str, Any] = {"mode": "solid", "color": _rgb(color["color"], "color")}
    elif color.get("mode") == "gradient" and set(color) == {"mode", "colors", "axis"}:
        colors = color["colors"]
        if color["axis"] not in FIXTURE_AXES or not isinstance(colors, list) or not 2 <= len(colors) <= 8:
            raise ValueError("fixture gradient requires axis height/x/y/index and 2–8 colors")
        normalized_color = {
            "mode": "gradient", "axis": color["axis"],
            "colors": tuple(_rgb(entry, f"colors[{i}]") for i, entry in enumerate(colors)),
        }
    else:
        raise ValueError("invalid color mode/fields")
    if not weather_palette:
        normalized_color["weatherPalette"] = False
    result = {"schemaVersion": FIXTURE_SCHEMA_VERSION, "effectId": effect_id, "parameters": params, "color": normalized_color}
    if "maxLevel" in value:
        result["maxLevel"] = _number(value["maxLevel"], "maxLevel", 0, 1)
    return result


def default_fixture_preset(effect_id: str) -> dict[str, Any]:
    if effect_id not in FIXTURE_EFFECT_IDS:
        raise ValueError("unknown fixture effectId")
    params = {key: default for key, (_, _, default, _) in FIXTURE_PARAMETERS[effect_id].items()}
    return {"schemaVersion": FIXTURE_SCHEMA_VERSION, "effectId": effect_id, "parameters": params,
            "color": {"mode": "solid", "color": {"r": 1, "g": 1, "b": 1}}}


def fixture_preset_to_json(normalized: Mapping[str, Any]) -> dict[str, Any]:
    color = normalized["color"]
    if color["mode"] == "solid":
        normalized_color: dict[str, Any] = {"mode": "solid", "color": dict(zip(("r", "g", "b"), color["color"]))}
    else:
        normalized_color = {"mode": "gradient", "axis": color["axis"],
                            "colors": [dict(zip(("r", "g", "b"), rgb)) for rgb in color["colors"]]}
    if color.get("weatherPalette") is False:
        normalized_color["weatherPalette"] = False
    result = {"schemaVersion": normalized["schemaVersion"], "effectId": normalized["effectId"],
              "parameters": dict(normalized["parameters"]), "color": normalized_color}
    if "maxLevel" in normalized:
        result["maxLevel"] = normalized["maxLevel"]
    return result


def _frames(seconds: float, fps: float) -> int:
    return max(1, _round_half_up(seconds * fps))


def fixture_completion_frames(preset: Mapping[str, Any], fps: int, fixture_count: int) -> int | None:
    """Frames for one natural cycle, or None when the effect has no boundary."""
    effect = preset["effectId"]
    p = preset["parameters"]
    if effect == "fixture-breathing":
        return max(1, math.ceil(fps / p["breatheHz"]))
    if effect in ("height-sweep", "space-wave"):
        return max(1, math.ceil(fps / p["speedHz"]))
    if effect == "fixture-chase":
        return _frames(p["stepSeconds"], fps) * max(1, fixture_count)
    return None


def _cycle(value: float) -> float:
    return value - math.floor(value)


def _smoothstep(value: float) -> float:
    t = clamp01(value)
    return t * t * (3 - 2 * t)


def fixture_brightness(preset: Mapping[str, Any], ctx: FixtureContext) -> float:
    effect = preset["effectId"]
    p = preset["parameters"]
    frame = ctx.controller_frame
    time = frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    if effect == "off":
        return 0.0
    if effect == "fixture-solid":
        return 1.0
    if effect == "fixture-breathing":
        return p["minLevel"] + (1 - p["minLevel"]) * (0.5 - 0.5 * math.cos(TAU * p["breatheHz"] * time))
    if effect == "height-sweep":
        position = ctx.normalized_height if p["direction"] == 0 else 1 - ctx.normalized_height
        half = p["width"] / 2
        centre = -half + _cycle(p["speedHz"] * time) * (1 + p["width"])
        return _smoothstep(1 - abs(position - centre) / half)
    if effect == "space-wave":
        position = ctx.normalized_x if p["direction"] == 0 else 1 - ctx.normalized_x
        return 0.5 + 0.5 * math.cos(TAU * (p["waveCount"] * position - p["speedHz"] * time))
    if effect == "fixture-twinkle":
        hold = _frames(p["holdSeconds"], ctx.controller_fps)
        local = frame + math.floor(hash01(0, ctx.fixture_index, 7) * hold)
        cell, within = divmod(local, hold)
        if hash01(cell, ctx.fixture_index, 11) >= p["density"]:
            return 0.0
        return math.sin(math.pi * (within + 0.5) / hold)
    if effect == "fixture-chase":
        count = max(1, ctx.fixture_count)
        head = (frame // _frames(p["stepSeconds"], ctx.controller_fps)) % count
        order = ctx.fixture_index if p["direction"] == 0 else count - 1 - ctx.fixture_index
        behind = (head - order) % count
        tail = int(p["tailCount"])
        return 1 - behind / (tail + 1) if behind <= tail else 0.0
    raise ValueError(f"unknown fixture effectId: {effect!r}")


def _rain(ctx: FixtureContext) -> float:
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    pulse = clamp01(1 - _cycle(RAIN_HZ * time + hash01(0, ctx.fixture_index, 23)) / RAIN_FLASH)
    return RAIN_FLOOR + (1 - RAIN_FLOOR) * pulse


def _gradient_position(axis: str, ctx: FixtureContext) -> float:
    if axis == "height":
        return ctx.normalized_height
    if axis == "x":
        return ctx.normalized_x
    if axis == "y":
        return ctx.normalized_y
    return ctx.normalized_index


def sample_fixture(preset: Mapping[str, Any], ctx: FixtureContext, raining: bool = False) -> Rgb:
    """One fixture's RGB (0..1): colour × brightness × maxLevel; rain flashes every effect except off."""
    brightness = fixture_brightness(preset, ctx)
    if raining and preset["effectId"] != "off":
        brightness *= _rain(ctx)
    brightness *= preset.get("maxLevel", 1.0)
    if brightness <= 0:
        return BLACK
    color = preset["color"]
    base = color["color"] if color["mode"] == "solid" else _gradient(
        color["colors"], _gradient_position(color["axis"], ctx), False)
    return tuple(clamp01(channel * brightness) for channel in base)  # type: ignore[return-value]


def fixture_geometry(positions: Sequence[Mapping[str, float] | None]) -> list[tuple[float, float, float]]:
    """(normalized height, x, y) per fixture, from layout positions {x, y, z} (cm; any may be missing).

    Each axis is normalized within the group when every fixture has it; otherwise that axis falls back
    to the fixture order (index / (count - 1)).
    """
    count = len(positions)
    fallback = [0.5 if count <= 1 else index / (count - 1) for index in range(count)]
    axes = []
    for key in ("z", "x", "y"):
        if count and all(position is not None and key in position for position in positions):
            values = [float(position[key]) for position in positions]  # type: ignore[index]
            low, high = min(values), max(values)
            axes.append([0.5 if high == low else (value - low) / (high - low) for value in values])
        else:
            axes.append(fallback)
    return list(zip(*axes))


def weather_fixture_preset(preset: Mapping[str, Any], palette: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Weather palette swaps the colours: gradients keep their own axis, solid colours spread by height."""
    if palette is None or preset["color"].get("weatherPalette", True) is False:
        return preset
    own = preset["color"]
    colors = tuple(tuple(stop[channel] for channel in ("r", "g", "b")) for stop in palette["colors"])
    axis = own["axis"] if own["mode"] == "gradient" else "height"
    return {**preset, "color": {"mode": "gradient", "axis": axis, "colors": colors}}


def render_fixture_frame(
    preset: Mapping[str, Any],
    geometry: Sequence[tuple[float, float, float]],
    frame: int,
    *,
    fps: int = 40,
    raining: bool = False,
    color_override: Mapping[str, Any] | None = None,
    frame_step: int = 0,
    cycle_frames: int | None = None,
) -> list[Rgb]:
    """One group's colours for a frame counted from playback (or step) start.

    ``frame_step`` (cloudy) offsets each fixture by its x position; ``cycle_frames`` wraps the frame
    for count-based sequence steps.
    """
    if type(frame) is not int or frame < 0:
        raise ValueError("frame must be a nonnegative integer")
    effect_frame = frame % cycle_frames if cycle_frames else frame
    preset = weather_fixture_preset(preset, color_override)
    count = len(geometry)
    return [
        sample_fixture(preset, FixtureContext(
            controller_frame=effect_frame + _round_half_up(x * CLOUD_SPREAD_STEPS * frame_step),
            controller_fps=fps, fixture_index=index, fixture_count=count,
            normalized_height=height, normalized_x=x, normalized_y=y,
        ), raining)
        for index, (height, x, y) in enumerate(geometry)
    ]

