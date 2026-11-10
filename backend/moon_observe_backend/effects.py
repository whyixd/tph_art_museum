"""Pure Python port of frontend/src/effects (EffectPreset schema v2)."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Any, Mapping

SCHEMA_VERSION = 2
GRADIENT_AXES = ("strip", "radius", "unit")
EFFECT_IDS = (
    "spectrum-cycle",
    "center-out-sine",
    "moving-rings",
    "sequential-center-fill",
    "periodic-leds",
    "random-leds",
    "world-upper-half",
    "reverse-tide",
    "wide-rings",
    "comet-tail",
    "stardust-cloud",
    "color-breathing",
    "sector-smear",
    "twenty-beat",
    "sine-swell",
    "random-fill",
    "full-on",
    "unit-chase",
    "unit-random",
    "roulette-ring",
    "two-tone-trail",
    "firework-rise",
    "pillar-fill",
    "color-alternate",
    "wave-split",
)
# min, max, default, integer
PARAMETERS: dict[str, dict[str, tuple[float, float, float, bool]]] = {
    "spectrum-cycle": {},
    "center-out-sine": {"speedHz": (0.05, 2, 0.35, False)},
    "moving-rings": {
        "speedHz": (0.05, 1.5, 0.25, False),
        "ringCount": (1, 6, 3, True),
    },
    "sequential-center-fill": {
        "expansionSeconds": (0.5, 8, 2.5, False),
        "holdSeconds": (0, 2, 0.4, False),
    },
    "periodic-leds": {
        "litCount": (1, 156, 12, True),
        "darkGap": (0, 155, 12, True),
        "periodSeconds": (0.01, 20, 4, False),
    },
    "random-leds": {
        "litCount": (1, 156, 12, True),
        "holdFrames": (1, 120, 4, True),
        "durationMode": (0, 1, 0, True),
        "minHoldFrames": (1, 120, 1, True),
    },
    "world-upper-half": {},
    "reverse-tide": {"speedHz": (0.05, 1, 0.25, False), "holdSeconds": (0, 3, 0.5, False)},
    "wide-rings": {"speedHz": (0.05, 1, 0.2, False), "ringCount": (1, 6, 3, True), "width": (0.1, 0.8, 0.5, False)},
    "comet-tail": {"periodSeconds": (0.5, 20, 4, False), "tailLength": (0.05, 0.8, 0.25, False)},
    "stardust-cloud": {"holdSeconds": (0.5, 8, 2, False), "cloudCount": (1, 6, 3, True)},
    "color-breathing": {"breatheHz": (0.05, 0.8, 0.2, False)},
    "sector-smear": {},
    "twenty-beat": {},
    "sine-swell": {"speedHz": (0.05, 2, 0.3, False), "waveCount": (0.5, 6, 1, False), "depth": (0.1, 1, 0.6, False)},
    "random-fill": {
        "fillSeconds": (5, 300, 60, False),
        "acceleration": (1, 4, 2, False),
        "ledFadeSeconds": (0, 5, 0, False),
        "holdSeconds": (0, 30, 5, False),
        "fadeOutSeconds": (0, 10, 3, False),
        "staggerSeconds": (0, 30, 0, False),
    },
    "full-on": {"holdSeconds": (0, 60, 1, False), "fadeOutSeconds": (0, 30, 0, False)},
    "unit-chase": {
        "stepSeconds": (0.1, 5, 0.5, False),
        "fadeSeconds": (0, 2, 0, False),
        "gapSeconds": (0, 5, 0.5, False),
        "holdSeconds": (0, 30, 2, False),
        "endDarkSeconds": (0, 10, 0.5, False),
    },
    "unit-random": {
        "fadeInSeconds": (0, 5, 0, False),
        "holdSeconds": (0, 10, 1, False),
        "fadeOutSeconds": (0, 10, 1, False),
        "gapSeconds": (0, 5, 0.2, False),
    },
    "roulette-ring": {
        "sweeps": (0, 20, 4, True),
        "startStepFrames": (1, 40, 2, True),
        "endStepSeconds": (0.05, 3, 0.6, False),
        "holdSeconds": (0, 10, 1, False),
        "dimSeconds": (0.5, 20, 3, False),
        "spreadStepSeconds": (0, 2, 0.15, False),
        "ringHoldSeconds": (0, 30, 2, False),
        "glitchSeconds": (0.1, 10, 1.2, False),
        "glitchSpreadSeconds": (0, 10, 2, False),
        "flickerFrames": (1, 20, 2, True),
        "endDarkSeconds": (0, 10, 0.5, False),
    },
    "two-tone-trail": {"periodSeconds": (0.5, 20, 4, False), "tailLength": (0.05, 1, 0.3, False)},
    "firework-rise": {
        "travelSeconds": (0.25, 10, 1.5, False),
        "gapSeconds": (0, 10, 1, False),
        "headSize": (0.02, 0.3, 0.08, False),
        "trailSeconds": (0.1, 5, 1, False),
        "randomness": (0, 1, 0.6, False),
        "direction": (0, 1, 0, True),
        "trailColor": (0, 1, 0, True),
    },
    "color-alternate": {"intervalFrames": (1, 200, 8, True)},
    "wave-split": {
        "levelMeters": (0, 15, 6.2, False),
        "amplitudeMeters": (0, 6, 2, False),
        "wavelengthMeters": (2, 40, 10, False),
        "speedMps": (0.1, 10, 1.5, False),
        "directionDegrees": (0, 360, 0, False),
        "chop": (0, 1, 0.4, False),
        "foamCm": (0, 40, 6, False),
        "softnessCm": (0, 40, 4, False),
    },
    "pillar-fill": {
        "stepSeconds": (0.05, 5, 0.5, False),
        "acceleration": (1, 4, 1, False),
        "fadeInSeconds": (0, 5, 0.3, False),
        "holdSeconds": (0, 30, 2, False),
        "fadeOutSeconds": (0, 10, 1, False),
        "endDarkSeconds": (0, 10, 0.5, False),
    },
}
# 與前端 manifest 的 step 一致；各燈台數值會對齊這個刻度。整數參數刻度為 1。
# Matches the frontend manifest steps; per-unit values snap to them. Integer parameters step by 1.
PARAMETER_STEPS: dict[str, dict[str, float]] = {
    "center-out-sine": {"speedHz": 0.05},
    "moving-rings": {"speedHz": 0.05},
    "periodic-leds": {"periodSeconds": 0.01},
    "reverse-tide": {"speedHz": 0.05, "holdSeconds": 0.1},
    "wide-rings": {"speedHz": 0.05, "width": 0.05},
    "comet-tail": {"periodSeconds": 0.5, "tailLength": 0.05},
    "stardust-cloud": {"holdSeconds": 0.5},
    "color-breathing": {"breatheHz": 0.05},
    "sine-swell": {"speedHz": 0.05, "waveCount": 0.5, "depth": 0.05},
    "full-on": {"holdSeconds": 0.5, "fadeOutSeconds": 0.25},
    "random-fill": {"fillSeconds": 5, "acceleration": 0.25, "ledFadeSeconds": 0.25, "holdSeconds": 1, "fadeOutSeconds": 0.5,
                    "staggerSeconds": 0.25},
    "two-tone-trail": {"periodSeconds": 0.5, "tailLength": 0.05},
    "firework-rise": {"travelSeconds": 0.25, "gapSeconds": 0.25, "headSize": 0.01, "trailSeconds": 0.1,
                      "randomness": 0.05},
}
# 逐組填滿與整台效果靠九台共用時間表，各台參數不同會錯亂，所以不開放。
# sequential-center-fill and the whole-unit effects share one schedule, so per-unit values would break them.
FIXED_PARAMETERS: dict[str, tuple[str, ...]] = {
    "sequential-center-fill": ("expansionSeconds", "holdSeconds"),
    "random-leds": ("durationMode",),
    "wave-split": ("levelMeters", "amplitudeMeters", "wavelengthMeters", "speedMps", "directionDegrees", "chop",
                   "foamCm", "softnessCm"),
    "pillar-fill": ("stepSeconds", "acceleration", "fadeInSeconds", "holdSeconds", "fadeOutSeconds", "endDarkSeconds"),
    # 錯落順序靠九台共用的整輪長度。 / The stagger order relies on one shared round length.
    "random-fill": ("staggerSeconds",),
    "unit-chase": ("stepSeconds", "fadeSeconds", "gapSeconds", "holdSeconds", "endDarkSeconds"),
    "unit-random": ("fadeInSeconds", "holdSeconds", "fadeOutSeconds", "gapSeconds"),
    "roulette-ring": tuple(  # every parameter
        ("sweeps", "startStepFrames", "endStepSeconds", "holdSeconds", "dimSeconds", "spreadStepSeconds",
         "ringHoldSeconds", "glitchSeconds", "glitchSpreadSeconds", "flickerFrames", "endDarkSeconds")),
}
# 九台共用時間表、不套用天氣多雲錯開的效果（前端 ignoresWeatherTiming）；它們也沒有雨天線條版本。
# Shared-schedule effects that skip weather cloud offsets (frontend ignoresWeatherTiming); they have no rain lines either.
WEATHER_TIMING_FREE = ("unit-chase", "unit-random", "roulette-ring", "pillar-fill", "wave-split")
UNIT_VARIATION_DISTRIBUTIONS = ("linear", "skip", "random")
# 不用到燈台編號／相位的效果：沒有雨、各台不錯開時，每台畫面相同，只需算一次（輸出迴圈依此共用）。
# 其他效果依 unit_index（隨機種子、逐台排程、燈台色盤）或各台相位而不同。測試逐台比對確認。
# Effects that never read the unit index or phase: without rain or per-unit offsets every unit shows the same
# picture, so the output loop renders it once. The others depend on unit_index (random seeds, per-unit
# schedules, unit palette) or the unit phase. A test renders every unit to confirm the list.
UNIT_INDEPENDENT_EFFECTS = (
    "spectrum-cycle", "center-out-sine", "moving-rings", "periodic-leds", "reverse-tide", "wide-rings",
    "comet-tail", "color-breathing", "sector-smear", "twenty-beat", "sine-swell", "full-on", "two-tone-trail",
    "color-alternate",
)


def same_on_every_unit(preset: Mapping[str, Any], raining: bool) -> bool:
    """True when every unit renders the same picture (given equal frame offsets)."""
    if raining or "unitVariation" in preset or preset["effectId"] not in UNIT_INDEPENDENT_EFFECTS:
        return False
    color = preset["color"]
    return color["mode"] == "solid" or (color["axis"] != "unit" and color.get("axisChange", {}).get("to") != "unit")
SPECTRUM = (
    (0x47 / 255, 0xD7 / 255, 1.0),
    (0x7A / 255, 0x5C / 255, 1.0),
    (1.0, 0x4F / 255, 0xA3 / 255),
    (1.0, 0xBA / 255, 0x49 / 255),
    (0x5D / 255, 1.0, 0xB2 / 255),
)
Rgb = tuple[float, float, float]
BLACK: Rgb = (0.0, 0.0, 0.0)
TAU = math.tau


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _number(value: Any, name: str, low: float, high: float, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be finite and in [{low}, {high}]")
    if integer and not result.is_integer():
        raise ValueError(f"{name} must be an integer")
    return result


def _rgb(value: Any, name: str) -> Rgb:
    if not isinstance(value, dict) or set(value) != {"r", "g", "b"}:
        raise ValueError(f"{name} must have r, g, b")
    return tuple(_number(value[key], f"{name}.{key}", 0, 1) for key in ("r", "g", "b"))  # type: ignore[return-value]


def variable_parameters(effect_id: str) -> tuple[str, ...]:
    """Parameters that may differ per unit."""
    return tuple(key for key in PARAMETERS[effect_id] if key not in FIXED_PARAMETERS.get(effect_id, ()))


def _unit_variation(value: Any, effect_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"parameter", "from", "to", "distribution"}:
        raise ValueError("unitVariation must contain parameter, from, to, distribution")
    parameter = value["parameter"]
    if parameter not in variable_parameters(effect_id):
        raise ValueError(f"unitVariation parameter {parameter!r} cannot vary for {effect_id}")
    if value["distribution"] not in UNIT_VARIATION_DISTRIBUTIONS:
        raise ValueError("unitVariation distribution must be linear, skip or random")
    low, high, _, _ = PARAMETERS[effect_id][parameter]
    return {
        "parameter": parameter,
        "from": _number(value["from"], "unitVariation.from", low, high),
        "to": _number(value["to"], "unitVariation.to", low, high),
        "distribution": value["distribution"],
    }


def validate_preset(value: Any, led_count: int = 156) -> dict[str, Any]:
    # 選填 unitVariation：各燈台一個參數取不同的固定值。 / Optional unitVariation: one parameter gets a fixed value per unit.
    keys = {"schemaVersion", "effectId", "parameters", "color"}
    if not isinstance(value, dict) or not keys <= set(value) or set(value) - keys - {"unitVariation"}:
        raise ValueError("preset must contain schemaVersion, effectId, parameters, color")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError("unsupported EffectPreset schemaVersion")
    effect_id = value["effectId"]
    if effect_id not in EFFECT_IDS:
        raise ValueError(f"unknown effectId: {effect_id!r}")
    raw_params = value["parameters"]
    if not isinstance(raw_params, dict) or set(raw_params) - set(PARAMETERS[effect_id]):
        raise ValueError("invalid effect parameters")
    params = {
        key: _number(raw_params.get(key, default), key, low, high, integer)
        for key, (low, high, default, integer) in PARAMETERS[effect_id].items()
    }
    if effect_id == "periodic-leds":
        if (params["litCount"] - 1) * (params["darkGap"] + 1) >= led_count:
            raise ValueError("periodic-leds litCount/darkGap exceed ledCount")
    if effect_id == "random-leds" and params["litCount"] > led_count:
        raise ValueError("random-leds litCount exceeds ledCount")
    if effect_id == "random-leds" and params["minHoldFrames"] > params["holdFrames"]:
        raise ValueError("random-leds minHoldFrames exceeds holdFrames")
    color = value["color"]
    if not isinstance(color, dict):
        raise ValueError("color must be an object")
    # 選填 weatherPalette: false 讓天氣啟用時仍用自己的顏色；省略即套用天氣色盤。
    # Optional weatherPalette: false keeps these colors under weather; omitted means the palette replaces them.
    weather_palette = color.get("weatherPalette", True)
    if type(weather_palette) is not bool:
        raise ValueError("color weatherPalette must be a boolean")
    color = {key: entry for key, entry in color.items() if key != "weatherPalette"}
    if color.get("mode") == "solid" and set(color) == {"mode", "color"}:
        normalized_color: dict[str, Any] = {
            "mode": "solid",
            "color": _rgb(color["color"], "color"),
        }
    elif color.get("mode") == "gradient" and set(color) - {"axisChange"} == {"mode", "colors", "axis"}:
        colors = color["colors"]
        if color["axis"] not in GRADIENT_AXES or not isinstance(colors, list) or not 2 <= len(colors) <= 8:
            raise ValueError("gradient requires axis strip/radius/unit and 2–8 colors")
        normalized_color = {
            "mode": "gradient",
            "axis": color["axis"],
            "colors": tuple(_rgb(entry, f"colors[{i}]") for i, entry in enumerate(colors)),
        }
        if "axisChange" in color:
            normalized_color["axisChange"] = _axis_change(color["axisChange"])
    else:
        raise ValueError("invalid color mode/fields")
    if not weather_palette:
        normalized_color["weatherPalette"] = False
    result = {"schemaVersion": SCHEMA_VERSION, "effectId": effect_id, "parameters": params, "color": normalized_color}
    if "unitVariation" in value:
        result["unitVariation"] = _unit_variation(value["unitVariation"], effect_id)
    return result


AXIS_CHANGE_MAX_SECONDS = 3600


def _axis_change(value: Any) -> dict[str, Any]:
    """中途切換漸層方向 / Switch the gradient axis partway: {to, atSeconds, blendSeconds, repeat}."""
    if not isinstance(value, dict) or set(value) != {"to", "atSeconds", "blendSeconds", "repeat"}:
        raise ValueError("axisChange requires to, atSeconds, blendSeconds and repeat")
    if value["to"] not in GRADIENT_AXES:
        raise ValueError("axisChange to must be strip/radius/unit")
    if type(value["repeat"]) is not bool:
        raise ValueError("axisChange repeat must be a boolean")
    seconds = {}
    for key in ("atSeconds", "blendSeconds"):
        entry = value[key]
        if type(entry) not in (int, float) or not math.isfinite(entry) or not 0 <= entry <= AXIS_CHANGE_MAX_SECONDS:
            raise ValueError(f"axisChange {key} must be 0–{AXIS_CHANGE_MAX_SECONDS}")
        seconds[key] = entry
    return {"to": value["to"], **seconds, "repeat": value["repeat"]}


def axis_change_weight(change: Mapping[str, Any], elapsed_frame: int, fps: float) -> float:
    """Weight 0..1 of the axis switched to; mirrors frontend axisChangeWeight (Math.round = floor(x + 0.5))."""
    at = max(0, math.floor(change["atSeconds"] * fps + 0.5))
    blend = max(0, math.floor(change["blendSeconds"] * fps + 0.5))

    def once(frame: int) -> float:
        if blend == 0:
            return 1.0 if frame >= at else 0.0
        return clamp01((frame - at) / blend)

    if not change["repeat"]:
        return once(elapsed_frame)
    # 來回交替：每個方向停留 at 影格、過渡 blend 影格。 / Alternate: hold each axis for at frames, blend over blend frames.
    half = at + blend
    if half == 0:
        return 1.0
    phase = elapsed_frame % (2 * half)
    return once(phase) if phase < half else 1 - once(phase - half)


def fit_parameters(effect_id: str, params: Mapping[str, float], led_count: int) -> dict[str, float]:
    """Clamp parameters to the strip length (validate_preset limits); mirrors frontend fitParametersToLedCount."""
    result = dict(params)
    if effect_id == "random-leds":
        result["litCount"] = min(result["litCount"], led_count)
    elif effect_id == "periodic-leds":
        lit = max(1, min(result["litCount"], led_count))
        result["litCount"] = lit
        if lit > 1:
            result["darkGap"] = max(0, min(result["darkGap"], (led_count - 1) // (lit - 1) - 1))
    return result


def default_preset(effect_id: str, led_count: int | None = None) -> dict[str, Any]:
    """Default preset; with ``led_count``, parameters are clamped to fit (129 LEDs: periodic-leds gap 12 -> 10)."""
    if effect_id not in EFFECT_IDS:
        raise ValueError("unknown effectId")
    params = {key: default for key, (_, _, default, _) in PARAMETERS[effect_id].items()}
    if led_count is not None:
        params = fit_parameters(effect_id, params, led_count)
    if effect_id in ("spectrum-cycle", "random-fill"):
        color: dict[str, Any] = {
            "mode": "gradient", "axis": "unit" if effect_id == "random-fill" else "strip",
            "colors": [dict(zip(("r", "g", "b"), rgb)) for rgb in SPECTRUM],
        }
    elif effect_id in ("color-breathing", "twenty-beat"):
        color = {"mode": "gradient", "axis": "radius", "colors": [
            {"r": 0.15, "g": 0.8, "b": 1},
            {"r": 0.7, "g": 0.25, "b": 1},
            {"r": 1, "g": 0.55, "b": 0.2},
        ]}
    elif effect_id == "wave-split":
        # 海面上琥珀、浪花白、海面下藍。 / Amber above, white foam, blue below.
        color = {"mode": "gradient", "axis": "radius", "colors": [
            {"r": 1, "g": 0.55, "b": 0.15}, {"r": 1, "g": 1, "b": 1}, {"r": 0, "g": 0.3, "b": 1},
        ]}
    elif effect_id == "color-alternate":
        # 紅、藍交替。 / Red and blue.
        color = {"mode": "gradient", "axis": "strip", "colors": [{"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 0.35, "b": 1}]}
    elif effect_id == "moving-rings":
        # 每個環一色：青、洋紅、琥珀。 / One colour per ring: cyan, magenta, amber.
        color = {"mode": "gradient", "axis": "radius", "colors": [
            {"r": 0.2, "g": 0.85, "b": 1}, {"r": 1, "g": 0.2, "b": 0.75}, {"r": 1, "g": 0.7, "b": 0.15},
        ]}
    elif effect_id == "two-tone-trail":
        # 底色紅、亮點綠。 / Red base, green dot.
        color = {"mode": "gradient", "axis": "strip", "colors": [{"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 1, "b": 0}]}
    elif effect_id == "center-out-sine":
        color = {"mode": "gradient", "axis": "radius", "colors": [
            {"r": 0.2, "g": 0.92, "b": 1},
            {"r": 0.66, "g": 0.28, "b": 1},
        ]}
    else:
        color = {"mode": "solid", "color": {"r": 1, "g": 1, "b": 1}}
    return {"schemaVersion": SCHEMA_VERSION, "effectId": effect_id, "parameters": params, "color": color}


def preset_to_json(normalized: Mapping[str, Any]) -> dict[str, Any]:
    color = normalized["color"]
    if color["mode"] == "solid":
        normalized_color = {"mode": "solid", "color": dict(zip(("r", "g", "b"), color["color"]))}
    else:
        normalized_color = {
            "mode": "gradient", "axis": color["axis"],
            "colors": [dict(zip(("r", "g", "b"), rgb)) for rgb in color["colors"]],
        }
        if "axisChange" in color:
            normalized_color["axisChange"] = dict(color["axisChange"])
    if color.get("weatherPalette") is False:
        normalized_color["weatherPalette"] = False
    result = {
        "schemaVersion": normalized["schemaVersion"],
        "effectId": normalized["effectId"],
        "parameters": normalized["parameters"],
        "color": normalized_color,
    }
    if "unitVariation" in normalized:
        result["unitVariation"] = dict(normalized["unitVariation"])
    return result


@dataclass(frozen=True)
class EffectContext:
    controller_frame: int
    controller_fps: float
    led_index: int
    led_count: int
    unit_index: int = 0
    unit_count: int = 1
    global_controller_frame: int | None = None
    unit_relative_y: float = 0.0
    unit_phase_projection: float = 1.0
    # 依柱子的順序（config.pillar_ranks）；None 時用 unit_index。 / Rank by pillar (config.pillar_ranks); None uses unit_index.
    unit_rank: int | None = None
    # 轉燈中心的場地座標（cm，可缺軸）；wave-split 用。 / Spinner centre in the room (cm, axes may be missing); wave-split.
    unit_position: Mapping[str, float] | None = None

    @property
    def strip(self) -> float:
        return 0.5 if self.led_count <= 1 else self.led_index / (self.led_count - 1)

    @property
    def radius(self) -> float:
        return abs(self.strip * 2 - 1)

    @property
    def global_frame(self) -> int:
        return self.controller_frame if self.global_controller_frame is None else self.global_controller_frame


def _gradient(colors: tuple[Rgb, ...], position: float, loop: bool) -> Rgb:
    coordinate = (((position % 1) + 1) % 1) * len(colors) if loop else clamp01(position) * (len(colors) - 1)
    index = math.floor(coordinate)
    next_index = (index + 1) % len(colors) if loop else min(index + 1, len(colors) - 1)
    amount = clamp01(coordinate - index)
    return tuple(
        colors[index][channel] + (colors[next_index][channel] - colors[index][channel]) * amount
        for channel in range(3)
    )  # type: ignore[return-value]


@lru_cache(maxsize=64)
def _random_ranks(frame: int, unit_index: int, led_count: int) -> tuple[int, ...]:
    mask = 0xFFFFFFFF
    shuffled = list(range(led_count))
    state = (((frame + 1) * 0x9E3779B1) ^ ((unit_index + 1) * 0x85EBCA6B) ^ led_count ^ 0x6D2B79F5) & mask
    if state == 0:
        state = 0x6D2B79F5
    for index in range(led_count - 1, 0, -1):
        state = (state ^ ((state << 13) & mask)) & mask
        state = (state ^ (state >> 17)) & mask
        state = (state ^ ((state << 5) & mask)) & mask
        swap = state % (index + 1)
        shuffled[index], shuffled[swap] = shuffled[swap], shuffled[index]
    ranks = [0] * led_count
    for rank, led_index in enumerate(shuffled):
        ranks[led_index] = rank
    return tuple(ranks)


def _cycle(value: float) -> float:
    return value - math.floor(value)


def _smoothstep(value: float) -> float:
    t = clamp01(value)
    return t * t * (3 - 2 * t)


def _random32(seed: int) -> int:
    mask = 0xFFFFFFFF
    value = seed & mask
    value ^= (value << 13) & mask
    value ^= value >> 17
    value ^= (value << 5) & mask
    return value & mask


SECTOR_LENGTHS = (0.3, 0.6, 1.0, 0.45, 0.8, 0.2, 0.65, 1.0)


def _brightness(preset: Mapping[str, Any], ctx: EffectContext) -> float:
    effect = preset["effectId"]
    p = preset["parameters"]
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    if effect == "spectrum-cycle":
        spatial = 0.42 + 0.58 * math.sin(math.pi * ctx.strip)
        if preset["color"]["mode"] == "gradient":
            return spatial
        phase = (ctx.controller_frame % 20) / 20
        return spatial * (0.7 + 0.3 * math.cos(TAU * phase))
    if effect == "center-out-sine":
        phase = TAU * (1.5 * ctx.radius - p["speedHz"] * time)
        return 0.04 + (0.5 + 0.5 * math.cos(phase)) * 0.96
    if effect == "moving-rings":
        return min(1.0, sum(ring_weights(p, ctx)))
    if effect == "sequential-center-fill":
        expansion = max(0.01, p["expansionSeconds"])
        stage = expansion + max(0, p["holdSeconds"])
        global_time = max(0, ctx.global_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0)
        active = min(math.floor(global_time / stage), max(1, ctx.unit_count) - 1)
        if ctx.unit_index < active:
            return 1.0
        if ctx.unit_index > active:
            return 0.0
        progress = clamp01((global_time - active * stage) / expansion)
        step = 1 if ctx.led_count <= 1 else 2 / (ctx.led_count - 1)
        return clamp01((progress * (1 + step) - ctx.radius) / step)
    if effect == "periodic-leds":
        count = ctx.led_count
        offset = math.floor((time / max(0.01, p["periodSeconds"])) * count) % count
        relative = (ctx.led_index - offset + count) % count
        spacing = int(p["darkGap"]) + 1
        return 1.0 if relative % spacing == 0 and relative // spacing < int(p["litCount"]) else 0.0
    if effect == "random-leds":
        hold = max(1, int(p["holdFrames"]))
        change = math.floor(ctx.controller_frame / hold)
        if _random_ranks(change, ctx.unit_index, ctx.led_count)[ctx.led_index] >= int(p["litCount"]):
            return 0.0
        if p.get("durationMode", 0) != 1:
            return 1.0
        minimum = max(1, min(hold, int(p.get("minHoldFrames", 1))))
        duration = math.ceil(hold / (1 + (hold / minimum - 1) * ctx.radius))
        return 1.0 if ctx.controller_frame % hold < duration else 0.0
    if effect == "reverse-tide":
        # 一輪四段：填滿、收縮、擴散、收縮；頭尾都是全暗，循環時無接縫。
        # Four stages per cycle (fill, shrink, expand, shrink); it starts and ends dark, so wrapping is seamless.
        move, stage_frames = _reverse_tide_stage_frames(p, ctx.controller_fps)
        frame = math.floor(max(0, ctx.controller_frame)) % (4 * stage_frames)
        stage = frame // stage_frames
        progress = min(1.0, (frame - stage * stage_frames) / move)
        pixel = 1 if ctx.led_count <= 1 else 2 / (ctx.led_count - 1)

        # 邊界寬 2 像素；端點外推 1 像素，讓起訖時完全全暗或全亮。
        # The edge spans two pixels; ends overshoot by one so each stage starts and ends cleanly.
        def span(value: float) -> float:
            return -pixel + value * (1 + pixel * 2)

        def edge(boundary: float) -> float:
            return _smoothstep((ctx.radius - boundary) / (pixel * 2) + 0.5)

        if stage == 0:
            return edge(1 - span(progress))
        return 1 - edge(span(progress if stage == 2 else 1 - progress))
    if effect == "wide-rings":
        phase = _cycle(ctx.radius * int(p["ringCount"]) - time * p["speedHz"])
        distance = min(phase, 1 - phase)
        return 1 - _smoothstep((distance - p["width"] / 2 + 0.04) / 0.08)
    if effect == "two-tone-trail":
        # 漸層時整條全亮（底色），亮點與拖尾靠顏色；純色時只亮亮點與拖尾。
        # Gradient: fully lit (the base colour), the dot and trail are colour; solid: only the dot and trail.
        return 1.0 if preset["color"]["mode"] == "gradient" else two_tone_trail_strength(p, ctx)
    if effect == "firework-rise":
        return meteor_strength(p, ctx)
    if effect == "comet-tail":
        head = _cycle(time / p["periodSeconds"])
        behind = _cycle(head - ctx.strip)
        return 1 - _smoothstep(behind / p["tailLength"])
    if effect == "stardust-cloud":
        cloud_time = time / p["holdSeconds"]
        epoch = math.floor(cloud_time)
        age = _cycle(cloud_time)
        width = 0.025 + 0.11 * age
        brightness = 0.0
        for cluster in range(int(p["cloudCount"])):
            seed = ((epoch + 1) * 0x9E3779B1) ^ ((ctx.unit_index + 1) * 0x85EBCA6B) ^ ((cluster + 1) * 0x6D2B79F5)
            center = _random32(seed) / 0x100000000
            distance = abs(ctx.strip - center)
            brightness = max(brightness, (1 - _smoothstep(distance / width)) * (1 - _smoothstep(age)))
        return brightness
    if effect == "color-breathing":
        return 0.25 + 0.75 * (0.5 + 0.5 * math.cos(TAU * p["breatheHz"] * time))
    if effect == "sector-smear":
        length = SECTOR_LENGTHS[ctx.controller_frame % len(SECTOR_LENGTHS)]
        return 1 - _smoothstep((ctx.radius - length + 0.02) / 0.04)
    if effect == "twenty-beat":
        beat = (ctx.controller_frame % 20) // 4
        radius = ctx.radius
        if beat == 0:
            return 1 - _smoothstep((radius - 0.3) / 0.04)
        if beat == 1:
            return _smoothstep((radius - 0.24) / 0.04) * (1 - _smoothstep((radius - 0.58) / 0.04))
        if beat == 2:
            return _smoothstep((radius - 0.64) / 0.04)
        if beat == 3:
            return 0.7
        return 1.0 if math.floor(radius * 10) % 2 == 0 else 0.12
    if effect == "sine-swell":
        phase = TAU * (p["waveCount"] * ctx.strip - p["speedHz"] * time)
        # 波谷亮度 = 1 - depth，波峰 = 1。 / Trough is 1 - depth, crest is full brightness.
        return 1 - p["depth"] * (0.5 - 0.5 * math.cos(phase))
    if effect == "random-fill":
        fill, acceleration, led_fade, hold, fade_out, stagger = _random_fill_timing(p, ctx.controller_fps)
        unit_cycle = fill + led_fade + hold + fade_out
        frame = max(0, ctx.controller_frame)
        loop, elapsed = divmod(frame, unit_cycle + stagger)
        elapsed -= _random_fill_delay(stagger, loop, ctx.unit_index, ctx.unit_count)
        # 還沒輪到或已做完的台全暗。 / Dark before this unit's start and after its round.
        if elapsed < 0 or elapsed >= unit_cycle:
            return 0.0
        # 每一輪、每組各自重新洗牌；同組兩條半圓共用順序。 / Reshuffle per round and unit; both arcs share the order.
        rank = _random_ranks(loop, ctx.unit_index, ctx.led_count)[ctx.led_index]
        # 已亮數量 = 燈數 × (經過時間 / 填滿時間)^加速程度：從全暗開始，一開始慢、後面越來越快。
        # Lit count = ledCount * (t / fill)^acceleration: starts dark, slow at first, then faster.
        start = math.floor(fill * ((rank + 1) / ctx.led_count) ** (1 / acceleration))
        if elapsed < start:
            return 0.0
        lit = 1.0 if led_fade == 0 else clamp01((elapsed - start + 1) / led_fade)
        fade_start = fill + led_fade + hold
        if fade_out == 0 or elapsed < fade_start:
            return lit
        return lit * clamp01(1 - (elapsed - fade_start + 1) / fade_out)
    if effect == "full-on":
        return full_on_brightness(ctx.controller_frame, ctx.controller_fps, p)
    if effect == "unit-chase":
        return _unit_chase(p, ctx)
    if effect == "pillar-fill":
        return _pillar_fill(p, ctx)
    if effect == "wave-split":
        # 純色：海面下亮、海面上暗，浪花全亮。 / Solid: lit below the surface, dark above, the foam fully lit.
        top, foam = _wave_split(p, ctx)
        return max(1 - top, foam)
    if effect == "color-alternate":
        # 純色：亮、暗交替。 / Solid: on, off.
        return 1.0 if _alternate_step(p, ctx) % 2 == 0 else 0.0
    if effect == "unit-random":
        return _unit_random(p, ctx)
    if effect == "roulette-ring":
        return _roulette_ring(p, ctx)
    if effect == "world-upper-half":
        upper_side = ctx.unit_phase_projection * (0.5 - ctx.strip) > 0
        if abs(ctx.unit_phase_projection) <= 0.5 or not upper_side:
            return 0.0
        t = clamp01(ctx.radius / 0.2)
        return t * t * (3 - 2 * t)
    raise ValueError(f"unknown effect: {effect}")


# 雨天線條化的固定常數，與前端 RAIN_LINES 一致。 / Fixed rain-line constants; match frontend RAIN_LINES.
RAIN_LINES = {"stripeSpacing": 6, "streakFrames": 2, "sparseDensity": 0.18, "denseDensity": 0.45}


def _rain_stripe(ctx: EffectContext) -> float:
    """One LED in every 6 from the rim inward, mirrored, so it spins into concentric thin rings."""
    step = _round_half_up((1 - ctx.radius) * (ctx.led_count - 1) / 2)
    return 1.0 if step % RAIN_LINES["stripeSpacing"] == 0 else 0.0


def _rain_ring(radial_distance: float, led_count: int) -> float:
    """Full within half an LED of the ring, fading over the next LED; total energy stays two LEDs."""
    pixel = 1 if led_count <= 1 else 2 / (led_count - 1)
    return clamp01(1.5 - abs(radial_distance) / pixel)


def _rain_streak(ctx: EffectContext, density: float) -> float:
    """Each LED lights per 2-frame cell by chance, drawing short arcs."""
    if density <= 0:
        return 0.0
    cell = max(0, ctx.controller_frame) // RAIN_LINES["streakFrames"]
    return 1.0 if hash01(cell, ctx.unit_index, ctx.led_index + 101) < density else 0.0


def _rain_brightness(preset: Mapping[str, Any], ctx: EffectContext) -> float | None:
    """The effect's line-like rain version; None keeps the dry brightness."""
    effect = preset["effectId"]
    p = preset["parameters"]
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    # 填滿、收縮、呼吸不變，只亮同心條紋。 / Same fill, shrink or breathing; only the stripes light.
    if effect in ("sequential-center-fill", "reverse-tide", "color-breathing"):
        return _brightness(preset, ctx) * _rain_stripe(ctx)
    # 只留波峰細環。 / Only the crest rings.
    if effect == "center-out-sine":
        wave = 1.5 * ctx.radius - p["speedHz"] * time
        return _rain_ring((_cycle(wave + 0.5) - 0.5) / 1.5, ctx.led_count)
    if effect == "sine-swell":
        wave = p["waveCount"] * ctx.strip - p["speedHz"] * time
        return _rain_ring(2 * (_cycle(wave + 0.5) - 0.5) / p["waveCount"], ctx.led_count)
    # 每條寬帶只留內外兩條邊線。 / Each band keeps only its inner and outer edge lines.
    if effect == "wide-rings":
        count = int(p["ringCount"])
        phase = _cycle(ctx.radius * count - time * p["speedHz"])
        return _rain_ring((min(phase, 1 - phase) - p["width"] / 2) / count, ctx.led_count)
    # 彗頭細環，尾巴是越往後越稀疏的雨絲。 / Thin head ring, tail streaks thinning out behind it.
    if effect == "comet-tail":
        behind = _cycle(_cycle(time / p["periodSeconds"]) - ctx.strip)
        ring = _rain_ring(2 * min(behind, 1 - behind), ctx.led_count)
        density = RAIN_LINES["denseDensity"] * (1 - _smoothstep(behind / p["tailLength"]))
        return max(ring, _rain_streak(ctx, density))
    if effect == "stardust-cloud":
        return _rain_streak(ctx, RAIN_LINES["denseDensity"] * _brightness(preset, ctx))
    if effect == "full-on":
        # 滿版雨絲，跟著淡出。 / Streaks across the disc, fading with it.
        return _brightness(preset, ctx) * _rain_streak(ctx, RAIN_LINES["sparseDensity"])
    # 已亮的燈改閃雨絲，填滿時等於全亮的雨絲。 / Lit LEDs flash streaks; the filled disc matches full-on's rain.
    if effect == "random-fill":
        return _brightness(preset, ctx) * _rain_streak(ctx, RAIN_LINES["sparseDensity"])
    return None


_METEOR_TRAIL_DECAY = 5.5  # exp(-5.5) ≈ 1/245 at the trail time
_METEOR_RATE_SALT = 500


def _meteor_timing(parameters: Mapping[str, Any], fps: float) -> tuple[int, int]:
    """(travel, gap) whole frames (frontend meteorTiming)."""
    return max(1, _round_half_up(parameters["travelSeconds"] * fps)), _seconds_frames(parameters, "gapSeconds", fps)


def meteor_strength(p: Mapping[str, Any], ctx: EffectContext) -> float:
    """firework-rise, after WLED's Meteor (frontend meteorStrength): 1 in the head, then each LED it passed fades at
    its own fixed-seed rate, so the tail breaks up; 0 before the first meteor reaches an LED."""
    travel, gap = _meteor_timing(p, ctx.controller_fps)
    cycle = travel + gap
    frame = max(0, ctx.controller_frame)
    loop = frame // cycle
    elapsed = frame - loop * cycle
    # 兩邊半圓對稱；方向 0 由底部往上。 / Both halves alike; direction 0 rises from the bottom.
    height = 1 - ctx.radius
    along = height if p["direction"] == 0 else 1 - height
    # 還沒到這一輪的流星時，看上一輪那顆。 / Before this round's meteor arrives, the previous one counts.
    passed = along * travel
    launch = loop if elapsed >= passed else loop - 1
    if launch < 0:
        return 0.0
    since = elapsed - passed if elapsed >= passed else elapsed + cycle - passed
    head_frames = p["headSize"] * travel
    if since < head_frames:
        return 1.0
    randomness = p["randomness"]
    scale = max(0.05, 1 + randomness * (2 * hash01(launch, ctx.unit_index, _METEOR_RATE_SALT + ctx.led_index) - 1))
    trail = max(1, p["trailSeconds"] * ctx.controller_fps)
    value = math.exp(-_METEOR_TRAIL_DECAY * (since - head_frames) / (trail * scale))
    return 0.0 if value < 1 / 255 else value


WAVE_SPINNER_RADIUS_CM = 35.0  # 轉燈半徑（前端 SPINNER_RADIUS） / rotor radius (frontend SPINNER_RADIUS)
_WAVE_CHOP = (0.43, 35.0, 1.3)  # 碎浪：波長倍數、偏轉角度、速度倍數 / chop: wavelength, degrees off, speed


def height_above_surface(p: Mapping[str, Any], ctx: EffectContext) -> float:
    """wave-split: this LED's height above the sea surface in cm, positive above (frontend heightAboveSurface).

    The LED sits at centre z + radius × cos(π × radius coordinate); the rotor spins about the vertical axis, so
    the height never changes with rotation. The surface is a main wave plus a smaller chop wave."""
    position = ctx.unit_position or {}
    x = position.get("x", 150 * (ctx.unit_index + 1))
    y = position.get("y", 0)
    z = position.get("z", 500)
    led = z + WAVE_SPINNER_RADIUS_CM * math.cos(math.pi * ctx.radius)
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0

    def wave(wavelength: float, degrees: float, speed: float) -> float:
        angle = degrees * math.pi / 180
        along = (x * math.cos(angle) + y * math.sin(angle)) / 100
        return math.sin(2 * math.pi * (along - speed * time) / wavelength)

    main = wave(p["wavelengthMeters"], p["directionDegrees"], p["speedMps"])
    chop = wave(p["wavelengthMeters"] * _WAVE_CHOP[0], p["directionDegrees"] + _WAVE_CHOP[1], p["speedMps"] * _WAVE_CHOP[2])
    surface = 100 * (p["levelMeters"] + p["amplitudeMeters"] * (main + p["chop"] * chop) / (1 + p["chop"]))
    return led - surface


def _wave_split(p: Mapping[str, Any], ctx: EffectContext) -> tuple[float, float]:
    """(share above the water 0..1, foam strength 0..1)."""
    above = height_above_surface(p, ctx)
    softness = p["softnessCm"]
    top = (1.0 if above >= 0 else 0.0) if softness <= 0 else _smoothstep(above / softness + 0.5)
    foam = 0.0 if p["foamCm"] <= 0 else 1 - _smoothstep(abs(above) / p["foamCm"])
    return top, foam


def _mix(start: Rgb, end: Rgb, amount: float) -> Rgb:
    t = clamp01(amount)
    return tuple(start[channel] + (end[channel] - start[channel]) * t for channel in range(3))  # type: ignore[return-value]


def _alternate_step(parameters: Mapping[str, Any], ctx: EffectContext) -> int:
    """color-alternate: interval index; whole frames, at least one Art-Net update (frontend alternateStep)."""
    return max(0, ctx.controller_frame) // max(1, int(parameters["intervalFrames"]))


def full_on_brightness(controller_frame: int, fps: float, parameters: Mapping[str, Any]) -> float:
    """full-on (frontend fullOnBrightness): always 1 with no fade; otherwise full for holdSeconds, then a linear fade
    over fadeOutSeconds, then dark (no loop). Both stages are whole frames."""
    fade = _seconds_frames(parameters, "fadeOutSeconds", fps)
    if fade == 0:
        return 1.0
    hold = _seconds_frames(parameters, "holdSeconds", fps)
    frame = max(0, controller_frame)
    return 1.0 if frame < hold else clamp01(1 - (frame - hold + 1) / fade)


def ring_weights(parameters: Mapping[str, Any], ctx: EffectContext) -> list[float]:
    """moving-rings: each ring's brightness at this LED (frontend ringWeights).

    Every ring travels the whole half (top 0 to bottom 1); neighbours move in opposite directions with phases
    π/N apart, so they cross. The centre is split between the two nearest radial LED coordinates."""
    count = int(parameters["ringCount"])
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    step = 1 if ctx.led_count <= 1 else 2 / (ctx.led_count - 1)
    offset = step / 2 if ctx.led_count % 2 == 0 else 0
    tolerance = step * 1e-6
    weights = []
    for ring in range(count):
        direction = 1 if ring % 2 == 0 else -1
        centre = 0.5 + 0.5 * direction * math.sin(TAU * parameters["speedHz"] * time + math.pi * ring / count)
        position = (max(offset, clamp01(centre)) - offset) / step
        lower = math.floor(position)
        upper = math.ceil(position)
        blend = position - lower
        weight = 0.0
        if abs(ctx.radius - (offset + lower * step)) <= tolerance:
            weight = max(weight, 1 - blend)
        if abs(ctx.radius - (offset + upper * step)) <= tolerance:
            weight = max(weight, blend)
        weights.append(weight)
    return weights


def two_tone_trail_strength(parameters: Mapping[str, Any], ctx: EffectContext) -> float:
    """Trail strength 0..1 (frontend twoToneTrailStrength).

    The strip splits into two halves (middle at the top, ends at the bottom); on both a dot runs from the bottom up
    to the top and back, one round trip per periodSeconds. Each LED shows how long ago the dot passed it: 1 right
    after, back to 0 once the dot has covered tailLength of the half."""
    time = ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0
    phase = _cycle(time / parameters["periodSeconds"])
    # 半徑 0 = 頂部，1 = 底部；上行在 (1 - r) / 2 經過，下行在 0.5 + r / 2 經過。
    # Radius 0 is the top, 1 the bottom; going up the dot passes at (1 - r) / 2, coming down at 0.5 + r / 2.
    radius = ctx.radius
    since = min(_cycle(phase - (1 - radius) / 2), _cycle(phase - (0.5 + radius / 2)))
    # 走完半圓要半個來回。 / Covering the half takes half a round trip.
    return 1 - _smoothstep(since / (parameters["tailLength"] / 2))


def _axis_color(preset: Mapping[str, Any], colors: tuple[Rgb, ...], axis: str, ctx: EffectContext) -> Rgb:
    if preset["effectId"] == "random-fill":
        # 不論漸層方向，每組跳著取一個純色 / Every axis gives each unit one solid colour, skipping along the palette.
        return _gradient(colors, unit_palette_position(ctx.unit_index, ctx.unit_count), False)
    if preset["effectId"] == "firework-rise" and preset["parameters"]["trailColor"] == 1:
        # 依拖尾變色：第一色是流星頭，尾巴往最後一色。 / Colour by trail: the head takes the first colour, the tail the last.
        return _gradient(colors, 1 - meteor_strength(preset["parameters"], ctx), False)
    if preset["effectId"] == "two-tone-trail":
        # 不論方向：第一色底色、最後一色亮點，依拖尾強度取色。 / Any axis: base colour to dot colour by trail strength.
        return _gradient(colors, two_tone_trail_strength(preset["parameters"], ctx), False)
    if axis == "unit":
        # 每台燈在漸層上取一個純色 / One solid color per unit, sampled across the units.
        position = 0.0 if ctx.unit_count <= 1 else ctx.unit_index / (ctx.unit_count - 1)
        return _gradient(colors, position, False)
    position = ctx.radius if axis == "radius" else ctx.strip
    loop = preset["effectId"] in ("spectrum-cycle", "color-breathing", "twenty-beat")
    if preset["effectId"] == "spectrum-cycle":
        position = (ctx.controller_frame % 20) / 20 + position * 1.8 / len(colors)
    elif preset["effectId"] == "color-breathing":
        position += (ctx.controller_frame / ctx.controller_fps if ctx.controller_fps > 0 else 0) * 0.05
    elif preset["effectId"] == "twenty-beat":
        position = (ctx.controller_frame % 20) / 20 + position * 0.25
    return _gradient(colors, position, loop)


def sample_effect(
    preset: Mapping[str, Any], ctx: EffectContext, elapsed_frame: int | None = None, raining: bool = False,
) -> Rgb:
    """``elapsed_frame`` only drives the gradient axis change (default: the shared global frame);
    ``raining`` switches to the effect's rain lines."""
    rain = _rain_brightness(preset, ctx) if raining else None
    color = preset["color"]
    if preset["effectId"] == "wave-split" and color["mode"] == "gradient" and rain is None:
        # 第一色海面上、最後一色海面下、中間色浪花（兩色時白色）。 / Above, below, foam (white with two colours).
        stops = color["colors"]
        top, foam = _wave_split(preset["parameters"], ctx)
        return _mix(_mix(stops[-1], stops[0], top), stops[1] if len(stops) >= 3 else (1.0, 1.0, 1.0), foam)
    if preset["effectId"] == "color-alternate" and color["mode"] == "gradient" and rain is None:
        # 第一色、最後一色交替。 / The first and last colours alternate.
        stops = color["colors"]
        return stops[0] if _alternate_step(preset["parameters"], ctx) % 2 == 0 else stops[-1]
    if preset["effectId"] == "moving-rings" and color["mode"] == "gradient" and rain is None:
        # 每個環一色（第 k 環取第 k 色），重疊處相加。 / Ring k takes colour k; overlaps add up.
        colors = color["colors"]
        total = [0.0, 0.0, 0.0]
        for ring, weight in enumerate(ring_weights(preset["parameters"], ctx)):
            if weight > 0:
                stop = colors[ring % len(colors)]
                for channel in range(3):
                    total[channel] += stop[channel] * weight
        return tuple(clamp01(channel) for channel in total)  # type: ignore[return-value]
    brightness = _brightness(preset, ctx) if rain is None else rain
    if color["mode"] == "solid":
        rgb: Rgb = color["color"]
    else:
        change = color.get("axisChange")
        weight = 0.0 if change is None else axis_change_weight(
            change, ctx.global_frame if elapsed_frame is None else elapsed_frame, ctx.controller_fps,
        )
        if weight <= 0:
            rgb = _axis_color(preset, color["colors"], color["axis"], ctx)
        elif weight >= 1:
            rgb = _axis_color(preset, color["colors"], change["to"], ctx)
        else:
            start = _axis_color(preset, color["colors"], color["axis"], ctx)
            end = _axis_color(preset, color["colors"], change["to"], ctx)
            rgb = tuple(start[channel] + (end[channel] - start[channel]) * weight for channel in range(3))  # type: ignore[assignment]
    return tuple(clamp01(channel * brightness) for channel in rgb)  # type: ignore[return-value]


def _round_half_up(value: float) -> int:
    # 與前端 Math.round 一致（非銀行家捨入）/ Matches the frontend's Math.round, not banker's rounding.
    return math.floor(value + 0.5)


def _reverse_tide_stage_frames(parameters: Mapping[str, Any], fps: int) -> tuple[int, int]:
    """(move, stage) frames: one move is 1/(3*speedHz) s, plus the hold, both whole frames."""
    move = max(1, _round_half_up(fps / (3 * parameters["speedHz"])))
    return move, move + _round_half_up(fps * parameters["holdSeconds"])


def _random_fill_timing(parameters: Mapping[str, Any], fps: int) -> tuple[int, float, int, int, int, int]:
    """(fill, acceleration, LED fade, hold, fade out, stagger); stages are whole frames.

    A unit's round is fill + LED fade + hold + fade out; the whole round adds the stagger."""
    def frames(key: str, minimum: int) -> int:
        return max(minimum, _round_half_up(parameters.get(key, 0) * fps))
    return (frames("fillSeconds", 1), max(1.0, parameters["acceleration"]), frames("ledFadeSeconds", 0),
            frames("holdSeconds", 0), frames("fadeOutSeconds", 0), frames("staggerSeconds", 0))


def _random_fill_delay(stagger: int, loop: int, unit_index: int, unit_count: int) -> int:
    """A unit's start delay: a fresh random order per round, spread evenly over the stagger (frontend unitDelay)."""
    if stagger == 0 or unit_count <= 1:
        return 0
    # 以台數當作「台」的種子，和各台燈點的洗牌分開。 / Seeded with unit_count as the unit, apart from the LED shuffles.
    order = _random_ranks(loop, unit_count, unit_count)[unit_index]
    return _round_half_up(stagger * order / (unit_count - 1))


def _seconds_frames(parameters: Mapping[str, Any], key: str, fps: float, minimum: int = 0) -> int:
    return max(minimum, _round_half_up(parameters[key] * fps))


def _pulse(frame: int, start: int, end: int, fade: int) -> float:
    """Lit over [start, end); with a fade it rises from start and falls from end (unit-chase)."""
    if fade == 0:
        return 1.0 if start <= frame < end else 0.0
    if frame < start:
        return 0.0
    if frame < start + fade:
        return (frame - start + 1) / fade
    if frame < end:
        return 1.0
    if frame < end + fade:
        return 1 - (frame - end + 1) / fade
    return 0.0


def _pillar_fill_timing(parameters: Mapping[str, Any], fps: float, unit_count: int) -> tuple[int, int, int, int, int]:
    """(span, fade in, fade start, fade out, cycle) whole frames (frontend pillarFillTiming).

    span = (N - 1) × step is the time from the first unit's start to the last one's."""
    span = (max(1, unit_count) - 1) * max(1, _round_half_up(parameters["stepSeconds"] * fps))
    fade_in = _seconds_frames(parameters, "fadeInSeconds", fps)
    fade_out = _seconds_frames(parameters, "fadeOutSeconds", fps)
    fade_start = span + fade_in + _seconds_frames(parameters, "holdSeconds", fps)
    return span, fade_in, fade_start, fade_out, max(1, fade_start + fade_out + _seconds_frames(parameters, "endDarkSeconds", fps))


def _pillar_fill(p: Mapping[str, Any], ctx: EffectContext) -> float:
    """Units light one by one in pillar order and stay lit; then hold, fade out together and stay dark."""
    span, fade_in, fade_start, fade_out, cycle = _pillar_fill_timing(p, ctx.controller_fps, ctx.unit_count)
    frame = max(0, ctx.controller_frame)
    elapsed = frame - (frame // cycle) * cycle
    units = max(1, ctx.unit_count)
    rank = min(units - 1, max(0, ctx.unit_index if ctx.unit_rank is None else ctx.unit_rank))
    # 加速：第 r 台在 span × (r / (N - 1))^(1 / 加速程度) 開始。 / Acceleration: rank r starts at span × (r/(N-1))^(1/a).
    start = 0 if units <= 1 else _round_half_up(span * (rank / (units - 1)) ** (1 / max(1.0, p["acceleration"])))
    if elapsed < start:
        return 0.0
    lit = 1.0 if fade_in == 0 else min(1.0, (elapsed - start + 1) / fade_in)
    if elapsed < fade_start:
        return lit
    return 0.0 if fade_out == 0 else lit * max(0.0, 1 - (elapsed - fade_start + 1) / fade_out)


def _unit_chase_timing(parameters: Mapping[str, Any], fps: float, unit_count: int) -> tuple[int, int, int, int, int]:
    """(step, fade, gap, hold, cycle): N steps left to right, dark gap, ceil(N/2) inward steps, hold, end dark."""
    units = max(1, unit_count)
    step = _seconds_frames(parameters, "stepSeconds", fps, 1)
    fade = min(step, _seconds_frames(parameters, "fadeSeconds", fps))
    gap = _seconds_frames(parameters, "gapSeconds", fps)
    hold = _seconds_frames(parameters, "holdSeconds", fps)
    end_dark = _seconds_frames(parameters, "endDarkSeconds", fps)
    return step, fade, gap, hold, (units + math.ceil(units / 2)) * step + gap + hold + end_dark


def _unit_chase(parameters: Mapping[str, Any], ctx: EffectContext) -> float:
    units = max(1, ctx.unit_count)
    step, fade, gap, hold, cycle = _unit_chase_timing(parameters, ctx.controller_fps, units)
    frame = max(0, ctx.controller_frame) % cycle
    unit = min(max(0, ctx.unit_index), units - 1)
    outward = _pulse(frame, unit * step, (unit + 1) * step, fade)
    # 兩側向內：第 k 步點亮 k 與 N-1-k，最後一步加上中間停留。 / Inward: step k lights k and N-1-k; the last also holds.
    pair = min(unit, units - 1 - unit)
    start = units * step + gap + pair * step
    last = pair == math.ceil(units / 2) - 1
    return max(outward, _pulse(frame, start, start + step + (hold if last else 0), fade))


UNIT_RANDOM_SEED = 1009


def _unit_random_timing(parameters: Mapping[str, Any], fps: float) -> tuple[int, int, int, int]:
    """(fade in, hold, fade out, slot): one unit's turn, whole frames."""
    fade_in = _seconds_frames(parameters, "fadeInSeconds", fps)
    hold = _seconds_frames(parameters, "holdSeconds", fps)
    fade_out = _seconds_frames(parameters, "fadeOutSeconds", fps)
    gap = _seconds_frames(parameters, "gapSeconds", fps)
    return fade_in, hold, fade_out, max(1, fade_in + hold + fade_out + gap)


def unit_turn(round_index: int, unit_index: int, unit_count: int) -> int:
    """This unit's turn in a round: every unit once in seeded random order, no unit twice in a row."""
    if unit_count <= 2:
        return unit_index
    ranks = _random_ranks(round_index, UNIT_RANDOM_SEED, unit_count)
    rank = ranks[unit_index]
    if round_index == 0:
        return rank
    previous = _random_ranks(round_index - 1, UNIT_RANDOM_SEED, unit_count)
    if previous[ranks.index(0)] != unit_count - 1:
        return rank
    return 1 if rank == 0 else 0 if rank == 1 else rank


def _unit_random(parameters: Mapping[str, Any], ctx: EffectContext) -> float:
    units = max(1, ctx.unit_count)
    fade_in, hold, fade_out, slot = _unit_random_timing(parameters, ctx.controller_fps)
    frame = max(0, ctx.controller_frame)
    turn = frame // slot
    round_index = turn // units
    if unit_turn(round_index, min(max(0, ctx.unit_index), units - 1), units) != turn - round_index * units:
        return 0.0
    elapsed = frame - turn * slot
    if elapsed < fade_in:
        return (elapsed + 1) / fade_in
    if elapsed < fade_in + hold:
        return 1.0
    if elapsed < fade_in + hold + fade_out:
        return 1 - (elapsed - fade_in - hold + 1) / fade_out
    return 0.0


def hash01(round_index: int, unit: int, salt: int) -> float:
    """Fixed-seed hash to 0..1 with 32-bit integer math, identical to the frontend."""
    mask = 0xFFFFFFFF
    state = (((round_index + 1) * 0x9E3779B1) ^ ((unit + 1) * 0x85EBCA6B) ^ ((salt + 1) * 0xC2B2AE35)) & mask
    state = ((state ^ (state >> 16)) * 0x7FEB352D) & mask
    state = ((state ^ (state >> 15)) * 0x846CA68B) & mask
    state = (state ^ (state >> 16)) & mask
    return state / 0x100000000


_ROULETTE_KEYS = tuple(PARAMETERS["roulette-ring"])


def _roulette_timing(parameters: Mapping[str, Any], fps: float, unit_count: int) -> dict[str, Any]:
    return _roulette_timing_cached((fps, unit_count, *map(parameters.__getitem__, _ROULETTE_KEYS)))


@lru_cache(maxsize=32)
def _roulette_timing_cached(key: tuple[Any, ...]) -> dict[str, Any]:
    fps, unit_count, *values = key
    parameters = dict(zip(PARAMETERS["roulette-ring"], values))
    units = max(1, unit_count)
    centre = (units - 1) // 2
    # 輪盤路徑：從第 1 台左右來回 sweeps 趟，再走到中間那台。 / Bounce `sweeps` times from unit 1, then run to the centre.
    path = [0]
    direction = 1
    if units > 1:
        for _ in range(max(0, _round_half_up(parameters["sweeps"]))):
            for _ in range(units - 1):
                path.append(path[-1] + direction)
            direction = -direction
    while path[-1] != centre:
        path.append(path[-1] + (1 if centre > path[-1] else -1))
    # 每步整數影格，由起始每步（影格）等比拉長到最後每步，且不比前一步短。
    # Whole-frame steps growing geometrically from the first step (frames) to the last, never shorter than before.
    first = max(1, _round_half_up(parameters["startStepFrames"]))
    last = max(first, _round_half_up(max(0, parameters["endStepSeconds"]) * fps))
    steps = len(path) - 1
    starts = [0]
    previous = first
    for step in range(steps):
        length = last if steps == 1 else max(previous, _round_half_up(first * (last / first) ** (step / (steps - 1))))
        previous = length
        starts.append(starts[-1] + length)
    dim_start = starts[steps] + _seconds_frames(parameters, "holdSeconds", fps)
    dim = max(1, _seconds_frames(parameters, "dimSeconds", fps))
    spread_start = dim_start + dim
    spread_step = _seconds_frames(parameters, "spreadStepSeconds", fps)
    glitch_start = (spread_start + spread_step * max(centre, units - 1 - centre)
                    + _seconds_frames(parameters, "ringHoldSeconds", fps))
    glitch = max(1, _seconds_frames(parameters, "glitchSeconds", fps))
    glitch_spread = _seconds_frames(parameters, "glitchSpreadSeconds", fps)
    return {
        "starts": tuple(starts), "path": tuple(path), "centre": centre, "dim_start": dim_start, "dim": dim,
        "spread_start": spread_start, "spread_step": spread_step, "glitch_start": glitch_start,
        "glitch": glitch, "glitch_spread": glitch_spread,
        "flicker": max(1, _round_half_up(parameters["flickerFrames"])),
        "cycle": glitch_start + glitch_spread + glitch + _seconds_frames(parameters, "endDarkSeconds", fps),
    }


def _roulette_ring(parameters: Mapping[str, Any], ctx: EffectContext) -> float:
    units = max(1, ctx.unit_count)
    t = _roulette_timing(parameters, ctx.controller_fps, units)
    round_index, frame = divmod(max(0, ctx.controller_frame), t["cycle"])
    unit = min(max(0, ctx.unit_index), units - 1)
    if frame < t["dim_start"]:
        # 輪盤：目前停在哪一台（起點嚴格遞增，取最後一個不晚於 frame 的）。
        # Roulette: which position is lit now (starts strictly increase; the last one not after frame).
        position = bisect_right(t["starts"], frame) - 1
        return 1.0 if t["path"][position] == unit else 0.0
    pixel_step = 1.0 if ctx.led_count <= 1 else 2 / (ctx.led_count - 1)
    outermost = ctx.radius > 1 - pixel_step / 2
    if frame < t["spread_start"]:
        if unit != t["centre"]:
            return 0.0
        if outermost:
            return 1.0
        # 變暗前緣由圓心走到圓周；最外圈一顆不受影響。 / The dark front moves centre to rim; the outermost LED stays.
        front = (frame - t["dim_start"] + 1) / t["dim"]
        return clamp01((ctx.radius - front) / pixel_step + 1)
    if not outermost:
        return 0.0
    if frame < t["spread_start"] + abs(unit - t["centre"]) * t["spread_step"]:
        return 0.0
    # 各台隨機開始閃爍（可重疊），越來越常熄滅，結束後關閉。 / Random overlapping glitch starts, then off.
    start = t["glitch_start"] + math.floor(hash01(round_index, unit, 0) * (t["glitch_spread"] + 1))
    if frame < start:
        return 1.0
    if frame < start + t["glitch"]:
        progress = (frame - start) / t["glitch"]
        cell = (frame - start) // t["flicker"]
        return 1.0 if hash01(round_index, unit, cell + 1) < 0.85 - 0.75 * progress else 0.0
    return 0.0


def unit_palette_position(unit_index: int, unit_count: int) -> float:
    """Palette position for random-fill: stride through the units by a coprime step near half."""
    if unit_count <= 1:
        return 0.0
    stride = max(1, unit_count // 2)
    while stride > 1 and math.gcd(stride, unit_count) != 1:
        stride -= 1
    return ((unit_index * stride) % unit_count) / (unit_count - 1)


def unit_variation_position(distribution: str, unit_index: int, unit_count: int) -> float:
    """Position (0..1) of a unit between from and to: in order, skipping (as random-fill), or fixed-seed random."""
    if unit_count <= 1:
        return 0.0
    if distribution == "skip":
        return unit_palette_position(unit_index, unit_count)
    if distribution == "random":
        return _random32(((unit_index + 1) * 0x9E3779B1) ^ 0x85EBCA6B) / 0x100000000
    return unit_index / (unit_count - 1)


def preset_for_unit(preset: Mapping[str, Any], unit_index: int, unit_count: int) -> Mapping[str, Any]:
    """The preset one unit plays: its own value of the varied parameter, without unitVariation."""
    variation = preset.get("unitVariation")
    if variation is None:
        return preset
    key = variation["parameter"]
    low, high, _, integer = PARAMETERS[preset["effectId"]][key]
    step = 1.0 if integer else PARAMETER_STEPS[preset["effectId"]][key]
    position = unit_variation_position(variation["distribution"], unit_index, unit_count)
    raw = variation["from"] + (variation["to"] - variation["from"]) * position
    # 對齊刻度並夾在範圍內，與前端 unitVariationValue 一致。 / Snap and clamp exactly like the frontend.
    snapped = round(low + _round_half_up((raw - low) / step) * step, 6)
    value = min(high, max(low, snapped))
    base = {name: entry for name, entry in preset.items() if name != "unitVariation"}
    return {**base, "parameters": {**preset["parameters"], key: value}}


def completion_frames(preset: Mapping[str, Any], fps: int, unit_count: int) -> int | None:
    """Frames for one natural effect cycle, or None when the effect has no boundary.

    With unitVariation it is the longest unit cycle, so every unit finishes its count.
    """
    if "unitVariation" in preset:
        cycles = [completion_frames(preset_for_unit(preset, index, unit_count), fps, unit_count)
                  for index in range(max(1, unit_count))]
        return None if cycles[0] is None else max(cycles)  # type: ignore[type-var]
    effect = preset["effectId"]
    parameters = preset["parameters"]
    if effect == "spectrum-cycle":
        return 20
    if effect in ("center-out-sine", "moving-rings"):
        return max(1, math.ceil(fps / parameters["speedHz"]))
    if effect == "reverse-tide":
        return 4 * _reverse_tide_stage_frames(parameters, fps)[1]
    if effect in ("wide-rings", "sine-swell"):
        return max(1, math.ceil(fps / parameters["speedHz"]))
    if effect in ("comet-tail", "two-tone-trail"):
        return max(1, math.ceil(fps * parameters["periodSeconds"]))
    if effect == "firework-rise":
        return sum(_meteor_timing(parameters, fps))
    if effect == "random-fill":
        fill, _, led_fade, hold, fade_out, stagger = _random_fill_timing(parameters, fps)
        return fill + led_fade + hold + fade_out + stagger
    if effect == "unit-chase":
        return _unit_chase_timing(parameters, fps, unit_count)[-1]
    if effect == "pillar-fill":
        return _pillar_fill_timing(parameters, fps, unit_count)[-1]
    if effect == "color-alternate":
        return 2 * max(1, int(parameters["intervalFrames"]))
    if effect == "wave-split":
        return max(1, math.ceil(fps * parameters["wavelengthMeters"] / parameters["speedMps"]))
    if effect == "unit-random":
        return _unit_random_timing(parameters, fps)[-1] * max(1, unit_count)
    if effect == "roulette-ring":
        return _roulette_timing(parameters, fps, unit_count)["cycle"]
    if effect == "sector-smear":
        return 8
    if effect == "twenty-beat":
        return 20
    if effect == "periodic-leds":
        return max(1, math.ceil(fps * parameters["periodSeconds"]))
    if effect == "sequential-center-fill":
        expansion = parameters["expansionSeconds"]
        hold = parameters["holdSeconds"]
        return math.ceil(((unit_count - 1) * (expansion + hold) + expansion) * fps) + 1
    return None
