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
    "fixture-firework",
    "fixture-rise",
    "pole-rise",
    "base-wave",
    "horizontal-flow",
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
    "fixture-firework": {
        "spreadSeconds": (0, 10, 2, False),
        "burstSeconds": (0.25, 10, 2.5, False),
        "gapSeconds": (0, 10, 0.5, False),
        "density": (0.05, 1, 0.6, False),
        "flickerFrames": (1, 20, 2, True),
    },
    "fixture-rise": {
        "riseSeconds": (0, 60, 10, False),
        "fadeInSeconds": (0, 20, 3, False),
        "repeat": (0, 1, 0, True),
        "holdSeconds": (0, 60, 5, False),
        "fadeOutSeconds": (0, 20, 3, False),
        "endDarkSeconds": (0, 20, 1, False),
    },
    "pole-rise": {
        "stepSeconds": (0, 5, 0.2, False),
        "attackSeconds": (0, 2, 0.1, False),
        "decaySeconds": (0.1, 20, 2.5, False),
        "staggerSeconds": (0, 10, 0.3, False),
        "order": (0, 1, 0, True),
        "periodSeconds": (1, 120, 8, False),
    },
    "base-wave": {
        "baseLevel": (0, 1, 0.3, False),
        "depth": (0, 1, 0.7, False),
        "speedHz": (0.05, 4, 0.3, False),
        "waveCount": (0.5, 6, 1, False),
        "waveAxis": (0, 3, 0, True),
        "direction": (0, 1, 0, True),
    },
    "horizontal-flow": {
        "speedHz": (0.02, 2, 0.2, False),
        "waveCount": (0.5, 6, 1, False),
        "directionDegrees": (0, 360, 0, False),
        "depth": (0, 1, 0.5, False),
        "acceleration": (1, 10, 1, False),
        "rampSeconds": (0.5, 120, 10, False),
    },
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
    # 第幾根柱子、柱子總數、在柱子上由低到高第幾顆（pole-rise；None 時每顆自成一根）。
    # Pole rank, pole count and order on the pole, low to high (pole-rise; None: each fixture alone).
    pole_rank: int | None = None
    pole_count: int | None = None
    pole_step: int | None = None

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


def fixture_firework_timing(parameters: Mapping[str, Any], fps: float) -> tuple[int, int, int]:
    """(spread, burst, gap) whole frames; spread and gap may be 0 (frontend fixtureFireworkTiming)."""
    return (max(0, _round_half_up(parameters["spreadSeconds"] * fps)), _frames(parameters["burstSeconds"], fps),
            max(0, _round_half_up(parameters["gapSeconds"] * fps)))


def fixture_rise_timing(parameters: Mapping[str, Any], fps: float) -> tuple[int, int, int, int, int]:
    """(rise, fade in, fade start, fade out, cycle) frames (frontend fixtureRiseTiming)."""
    def frames(key: str) -> int:
        return max(0, _round_half_up(parameters[key] * fps))
    rise, fade_in = frames("riseSeconds"), frames("fadeInSeconds")
    fade_start = rise + fade_in + frames("holdSeconds")
    fade_out = frames("fadeOutSeconds")
    return rise, fade_in, fade_start, fade_out, max(1, fade_start + fade_out + frames("endDarkSeconds"))


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
    if effect == "fixture-firework":
        return sum(fixture_firework_timing(p, fps))
    if effect == "fixture-rise":
        return fixture_rise_timing(p, fps)[-1] if p["repeat"] == 1 else None
    if effect == "pole-rise":
        return _frames(p["periodSeconds"], fps)
    if effect == "base-wave":
        return max(1, math.ceil(fps / p["speedHz"]))
    if effect == "horizontal-flow":
        # 加速時以一次加速為一輪。 / With acceleration, one ramp is one round.
        return _frames(p["rampSeconds"], fps) if p["acceleration"] > 1 else max(1, math.ceil(fps / p["speedHz"]))
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
    if effect == "pole-rise":
        return _pole_rise(p, ctx)
    if effect == "horizontal-flow":
        # 亮帶跟著顏色一起流：最暗 1 - depth。 / Bright bands flow with the colours; the darkest is 1 - depth.
        return 1 - p["depth"] * (0.5 - 0.5 * math.cos(TAU * horizontal_flow_phase(p, ctx)))
    if effect == "base-wave":
        # 基本亮度＋正弦波：波峰在 base + (1 - base) × depth。 / Base plus a sine: the crest reaches base + (1 - base) × depth.
        axis = FIXTURE_AXES[min(3, max(0, int(p["waveAxis"])))]
        along = _gradient_position(axis, ctx)
        position = along if p["direction"] == 0 else 1 - along
        wave = 0.5 + 0.5 * math.cos(TAU * (p["waveCount"] * position - p["speedHz"] * time))
        return p["baseLevel"] + (1 - p["baseLevel"]) * p["depth"] * wave
    if effect == "fixture-rise":
        # 依高度晚開始：最低一顆先亮；淡入用 smoothstep。 / Delay by height, lowest first; smoothstep fade-in.
        rise, fade_in, fade_start, fade_out, cycle = fixture_rise_timing(p, ctx.controller_fps)
        repeat = p["repeat"] == 1
        local = frame - (frame // cycle) * cycle if repeat else max(0, frame)
        delay = _round_half_up(ctx.normalized_height * rise)
        if local < delay:
            return 0.0
        lit = 1.0 if fade_in == 0 else _smoothstep((local - delay + 1) / fade_in)
        if not repeat or local < fade_start:
            return lit
        return 0.0 if fade_out == 0 else lit * max(0.0, 1 - (local - fade_start + 1) / fade_out)
    if effect == "fixture-firework":
        # 依高度晚開始：最低一顆先炸開。 / Delay by height: the lowest bursts first.
        spread, burst, gap = fixture_firework_timing(p, ctx.controller_fps)
        cycle = spread + burst + gap
        local = frame - (frame // cycle) * cycle - _round_half_up(ctx.normalized_height * spread)
        if local < 0 or local >= burst:
            return 0.0
        # 先全亮，之後隨機閃爍並以平方漸暗。 / Fully lit on ignition, then random flicker fading quadratically.
        flicker = max(1, int(p["flickerFrames"]))
        if local < flicker:
            return 1.0
        if hash01(frame // flicker, ctx.fixture_index, 31) >= p["density"]:
            return 0.0
        remaining = 1 - local / burst
        return remaining * remaining
    raise ValueError(f"unknown fixture effectId: {effect!r}")


def _pole_rise(p: Mapping[str, Any], ctx: FixtureContext) -> float:
    """Each pole lights its fixtures bottom to top, fast attack and slow quadratic decay; poles start
    staggerSeconds apart in pole order or a fixed-seed shuffle per round (frontend pole-rise)."""
    fps = ctx.controller_fps
    period = _frames(p["periodSeconds"], fps)
    frame = max(0, ctx.controller_frame)
    loop = frame // period
    local = frame - loop * period
    rank = ctx.fixture_index if ctx.pole_rank is None else ctx.pole_rank
    count = ctx.fixture_count if ctx.pole_count is None else ctx.pole_count
    order = rank
    if p["order"] == 1:
        mine = hash01(loop, rank, 43)
        order = 0
        for other in range(count):
            theirs = hash01(loop, other, 43)
            if theirs < mine or (theirs == mine and other < rank):
                order += 1

    def frames_or_zero(key: str) -> int:
        return max(0, _round_half_up(p[key] * fps))

    start = order * frames_or_zero("staggerSeconds") + (ctx.pole_step or 0) * frames_or_zero("stepSeconds")
    elapsed = local - start
    if elapsed < 0:
        return 0.0
    attack = frames_or_zero("attackSeconds")
    if elapsed < attack:
        return (elapsed + 1) / attack
    remaining = max(0.0, 1 - (elapsed - attack + 1) / _frames(p["decaySeconds"], fps))
    return remaining * remaining


def flow_distance(p: Mapping[str, Any], time: float) -> float:
    """Turns travelled (frontend flowDistance): the speed rises linearly from v0 to v0 × acceleration over the
    ramp, then holds; the turns are its integral, so the flow never jumps."""
    start = p["speedHz"]
    end = p["speedHz"] * p["acceleration"]
    ramp = p["rampSeconds"]
    if end == start:
        return start * time
    if time <= ramp:
        return start * time + (end - start) * time * time / (2 * ramp)
    return start * ramp + (end - start) * ramp / 2 + end * (time - ramp)


def horizontal_flow_phase(p: Mapping[str, Any], ctx: FixtureContext) -> float:
    """horizontal-flow phase in turns (frontend horizontalFlowPhase): distance along the plan direction × bands −
    turns travelled."""
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    angle = p["directionDegrees"] * math.pi / 180
    along = ctx.normalized_x * math.cos(angle) + ctx.normalized_y * math.sin(angle)
    return p["waveCount"] * along - flow_distance(p, time)


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
    if color["mode"] == "solid":
        base = color["color"]
    elif preset["effectId"] == "horizontal-flow":
        # 水平流動：漸層沿流動方向首尾相接地取色。 / horizontal-flow: the gradient loops along the flow.
        base = _gradient(color["colors"], horizontal_flow_phase(preset["parameters"], ctx), True)
    else:
        base = _gradient(color["colors"], _gradient_position(color["axis"], ctx), False)
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
    poles: Sequence[tuple[int, int]] | None = None,
) -> list[Rgb]:
    """One group's colours for a frame counted from playback (or step) start.

    ``poles`` gives each fixture's (pole rank, order on the pole) for pole-rise (config.fixture_poles).

    ``frame_step`` (cloudy) offsets each fixture by its x position; ``cycle_frames`` wraps the frame
    for count-based sequence steps.
    """
    if type(frame) is not int or frame < 0:
        raise ValueError("frame must be a nonnegative integer")
    effect_frame = frame % cycle_frames if cycle_frames else frame
    preset = weather_fixture_preset(preset, color_override)
    count = len(geometry)
    pole_count = max((rank + 1 for rank, _ in poles), default=0) if poles else None
    return [
        sample_fixture(preset, FixtureContext(
            controller_frame=effect_frame + _round_half_up(x * CLOUD_SPREAD_STEPS * frame_step),
            controller_fps=fps, fixture_index=index, fixture_count=count,
            normalized_height=height, normalized_x=x, normalized_y=y,
            pole_rank=poles[index][0] if poles else None, pole_count=pole_count,
            pole_step=poles[index][1] if poles else None,
        ), raining)
        for index, (height, x, y) in enumerate(geometry)
    ]


PRESET_GROUPS = ("spinner", "ball", "wash")


def parse_preset_document(raw: Any, pixels: int) -> dict[str, Any]:
    """A single-effect document: an EffectPreset v2 (spinner only), or {"spinner"?, "ball"?, "wash"?}.

    Returns the groups present, normalized; ``ball`` / ``wash`` may be None (dark). Used for preset.json
    and ``PUT /api/preset`` (where only the given groups change).
    單一效果文件：舊格式（只有轉燈）或 {"spinner"?, "ball"?, "wash"?}；ball／wash 可為 null（全暗）。
    """
    from .effects import validate_preset

    if isinstance(raw, dict) and "schemaVersion" in raw:
        return {"spinner": validate_preset(raw, pixels)}
    if not isinstance(raw, dict) or not raw or set(raw) - set(PRESET_GROUPS):
        raise ValueError("preset must be an EffectPreset or {\"spinner\"?, \"ball\"?, \"wash\"?}")
    result: dict[str, Any] = {}
    if "spinner" in raw:
        result["spinner"] = validate_preset(raw["spinner"], pixels)
    for group in ("ball", "wash"):
        if group in raw:
            result[group] = None if raw[group] is None else validate_fixture_preset(raw[group])
    return result
