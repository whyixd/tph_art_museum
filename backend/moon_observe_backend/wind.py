"""Standby wind-flow: the wind effect on spinners, balls and washes, and the standby switch (PLAN.md §10).

A soft band travels across the site toward where the wind blows, faster in stronger wind; at or above the
sparkle threshold a band of random sparkles also sweeps along the wind (direction locked at its start, always to
the far end, faster in stronger wind). The colour is picked along a gradient by the (slower smoothed) wind speed.
The effect advances by wall time, independent of the playback frame. It matches the frontend prototype
(``frontend/src/wind/windPreview.ts``) in behaviour, not value for value: the sparkles are random and each side
keeps its own clock.

Until the anemometer is read (M16a), the wind is the *test wind* set from the page (speed, direction, smoothing,
gustiness).

待機的風流：柔和的亮帶沿風吹往的方向流過全場，風越大越快；風速達到亮點門檻時，另有一道隨機亮點帶沿風向掃過
（開始時鎖定風向、一定掃到底、風越大掃得越快）。顏色依（較慢平滑的）風速在漸層上取。依實際時間推進，不看播放影格。
行為同前端原型，但不逐值相同（亮點是隨機的、兩邊各有時鐘）。風速計接上（M16a）之前，風力是頁面設定的「測試風」。
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import tempfile
from threading import Lock
import time
from typing import Any, Callable, Mapping, Sequence

from .effects import hash01

Rgb = tuple[float, float, float]
GROUPS = ("spinner", "ball", "wash")
WIND_SCHEMA_VERSION = 1
MAX_TURN_DEG_PER_SECOND = 3.0
CALM_DIRECTION_MS = 0.3
TWINKLE_FPS = 40

# key: (min, max, default). Shared by every group.
_SHARED: dict[str, tuple[float, float, float]] = {
    "speedHz": (0.02, 1, 0.15),
    "minRate": (0, 2, 0.6),
    "maxRate": (1, 8, 3),
    "calmMs": (0, 5, 1),
    "fullMs": (3, 25, 12),
    "curve": (0.5, 3, 1),
    "waveCount": (0.5, 4, 1),
    "bandSoftness": (0.1, 1, 0.5),
    "minLevel": (0, 0.9, 0.2),
    "colorSpread": (0, 0.5, 0.15),
    "sparkleBelowMs": (0, 15, 3),
    "sweepSeconds": (0.3, 30, 2),
    "sweepFastSeconds": (0.2, 30, 0.6),
    "gapSeconds": (0, 60, 6),
    "bandWidth": (0.05, 1, 0.3),
    "density": (0.02, 1, 0.15),
    "twinkleFrames": (1, 40, 6),
}
PARAMETERS: dict[str, dict[str, tuple[float, float, float]]] = {
    "spinner": {**_SHARED, "sphereSpan": (0, 1, 0.25)},
    # One colour per fixture, so a higher default sparkle density.
    "ball": {**_SHARED, "heightSpan": (0, 1, 0), "density": (0.02, 1, 0.5)},
    "wash": {**_SHARED, "heightSpan": (0, 1, 0), "density": (0.02, 1, 0.5)},
}
# Groups with the same values of these share one state, so their bands and sweeps stay in step.
TIMING_KEYS = ("speedHz", "minRate", "maxRate", "calmMs", "fullMs", "curve",
               "sparkleBelowMs", "sweepSeconds", "sweepFastSeconds", "gapSeconds")
DEFAULT_COLORS: tuple[Rgb, ...] = (
    # 無風 → 強風：橙、黃、綠（同前端 WIND_FLOW_COLORS）。 / Calm to strong: orange, yellow, green.
    (1.0, 0.45, 0.05), (1.0, 0.85, 0.1), (0.1, 1.0, 0.3),
)
DEFAULT_TARGET = {"speedMs": 3.0, "flowDeg": 0.0, "smoothingSeconds": 2.0, "gustiness": 0.0}
_TARGET_RANGES = {"speedMs": (0, 30), "flowDeg": (0, 360), "smoothingSeconds": (0, 60), "gustiness": (0, 0.6)}


def _number(value: Any, name: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number in {low:g}..{high:g}")
    return float(value)


def default_preset(group: str) -> dict[str, Any]:
    preset: dict[str, Any] = {
        "parameters": {key: float(default) for key, (_, _, default) in PARAMETERS[group].items()},
        "colors": [{"r": r, "g": g, "b": b} for r, g, b in DEFAULT_COLORS],
    }
    if group != "spinner":
        preset["maxLevel"] = 1.0
    return preset


def validate_preset(group: str, raw: Any) -> dict[str, Any]:
    """Canonical wind-flow preset of one group: ``{parameters, colors[, maxLevel]}`` or ValueError."""
    allowed = {"parameters", "colors"} | ({"maxLevel"} if group != "spinner" else set())
    if not isinstance(raw, dict) or not {"parameters", "colors"} <= set(raw) or set(raw) - allowed:
        raise ValueError(f"wind {group} preset requires parameters and colors")
    definitions = PARAMETERS[group]
    parameters = raw["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != set(definitions):
        raise ValueError(f"wind {group} parameters must be exactly: {', '.join(sorted(definitions))}")
    colors = raw["colors"]
    if not isinstance(colors, list) or not 2 <= len(colors) <= 8:
        raise ValueError(f"wind {group} colors must list 2..8 colours")
    preset: dict[str, Any] = {
        "parameters": {key: _number(parameters[key], f"wind {group} {key}", low, high)
                       for key, (low, high, _) in definitions.items()},
        "colors": [],
    }
    for index, color in enumerate(colors):
        if not isinstance(color, dict) or set(color) != {"r", "g", "b"}:
            raise ValueError(f"wind {group} colors[{index}] must be {{r, g, b}}")
        preset["colors"].append({channel: _number(color[channel], f"wind {group} colors[{index}].{channel}", 0, 1)
                                 for channel in ("r", "g", "b")})
    if group != "spinner":
        preset["maxLevel"] = _number(raw.get("maxLevel", 1.0), f"wind {group} maxLevel", 0, 1)
    return preset


def validate_target(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != set(_TARGET_RANGES):
        raise ValueError("wind target requires speedMs, flowDeg, smoothingSeconds and gustiness")
    target = {key: _number(raw[key], f"wind target {key}", low, high) for key, (low, high) in _TARGET_RANGES.items()}
    target["flowDeg"] %= 360
    return target


def wind_strength(speed_ms: float, p: Mapping[str, float]) -> float:
    """0 below calmMs, 1 at fullMs, then the curve."""
    calm = p["calmMs"]
    full = max(calm + 0.1, p["fullMs"])
    return min(1.0, max(0.0, (speed_ms - calm) / (full - calm))) ** p["curve"]


def flow_rate(speed_ms: float, p: Mapping[str, float]) -> float:
    """Band speed in turns per second: speedHz × (minRate at calm → maxRate at fullMs)."""
    return p["speedHz"] * (p["minRate"] + (p["maxRate"] - p["minRate"]) * wind_strength(speed_ms, p))


def _along(flow_deg: float) -> tuple[float, float, float, float]:
    """Coefficients (c, s, low, scale): along = (x × c + y × s − low) × scale, 0 most upwind, 1 most downwind."""
    angle = math.radians(flow_deg)
    c, s = math.cos(angle), math.sin(angle)
    low = min(0.0, c) + min(0.0, s)
    high = max(0.0, c) + max(0.0, s)
    return c, s, low, (0.0 if high == low else 1 / (high - low))


def _fluctuation(seconds: float) -> float:
    return math.sin(seconds * 0.71) * 0.5 + math.sin(seconds * 1.93 + 1.3) * 0.3 + math.sin(seconds * 4.1 + 2.1) * 0.2


def _gradient(colors: Sequence[Rgb], position: float) -> Rgb:
    coordinate = min(1.0, max(0.0, position)) * (len(colors) - 1)
    index = min(int(coordinate), len(colors) - 2)
    amount = coordinate - index
    a, b = colors[index], colors[index + 1]
    return a[0] + (b[0] - a[0]) * amount, a[1] + (b[1] - a[1]) * amount, a[2] + (b[2] - a[2]) * amount


class _Timing:
    """Turns travelled and the sparkle sweep of one set of timing values."""

    def __init__(self, flow_deg: float) -> None:
        self.played = 0.0
        self.turns = 0.0
        self.sweep: float | None = None
        self.sweep_deg = flow_deg
        self.next_sweep = 0.0

    def advance(self, elapsed: float, speed_ms: float, flow_deg: float, p: Mapping[str, float]) -> None:
        self.played += elapsed
        self.turns += elapsed * flow_rate(speed_ms, p)
        below = p["sparkleBelowMs"]
        if self.sweep is not None:
            # Faster in stronger wind: sweepSeconds at the threshold, sweepFastSeconds at fullMs.
            slow = 1 / p["sweepSeconds"]
            fast = 1 / min(p["sweepFastSeconds"], p["sweepSeconds"])
            full = max(below + 0.1, p["fullMs"])
            amount = min(1.0, max(0.0, (speed_ms - below) / (full - below)))
            self.sweep += elapsed * (slow + (fast - slow) * amount)
            if self.sweep >= 1:
                self.sweep = None
                self.next_sweep = self.played + p["gapSeconds"]
        if self.sweep is None and speed_ms >= below and self.played >= self.next_sweep:
            self.sweep = 0.0
            self.sweep_deg = flow_deg


class WindFrame:
    """One output frame of wind-flow: built once per frame by ``WindStandby.frame``, then sampled per light."""

    def __init__(self, presets: Mapping[str, Any], timings: Mapping[str, _Timing], flow_deg: float, color_speed: float) -> None:
        self._groups: dict[str, dict[str, Any]] = {}
        for group in GROUPS:
            preset = presets[group]
            p = preset["parameters"]
            timing = timings[group]
            self._groups[group] = {
                "p": p,
                "colors": [(color["r"], color["g"], color["b"]) for color in preset["colors"]],
                "maxLevel": preset.get("maxLevel", 1.0),
                "turns": timing.turns, "sweep": timing.sweep, "played": timing.played,
                "strength": wind_strength(color_speed, p),
                "flow": _along(flow_deg), "sweepFlow": _along(timing.sweep_deg),
            }
        self._heights: dict[int, list[float]] = {}

    def _sample(self, state: Mapping[str, Any], x: float, y: float, vertical: float, point: int) -> tuple[float, float, float, float]:
        """(band, sparkle, mix, colour position) of one point; see the module docstring."""
        p = state["p"]
        c, s, low, scale = state["flow"]
        along = 0.5 if scale == 0 else (x * c + y * s - low) * scale
        cycle = p["waveCount"] * along + vertical - state["turns"]
        distance = abs(cycle - round(cycle)) * 2
        crest = math.exp(-((distance / p["bandSoftness"]) ** 2) * 2)
        band = p["minLevel"] + (1 - p["minLevel"]) * crest
        position = state["strength"] + p["colorSpread"] * (crest - 0.5)
        sweep = state["sweep"]
        if sweep is None:
            return band, 0.0, 0.0, position
        width = p["bandWidth"]
        centre = -width / 2 + sweep * (1 + width)
        c, s, low, scale = state["sweepFlow"]
        sweep_along = 0.5 if scale == 0 else (x * c + y * s - low) * scale
        inside = min(1.0, max(0.0, 1 - abs(sweep_along - centre) / (width / 2)))
        mix = inside * inside * (3 - 2 * inside)
        if mix <= 0:
            return band, 0.0, 0.0, position
        length = max(1, round(p["twinkleFrames"]))
        local = math.floor(state["played"] * TWINKLE_FPS) + math.floor(hash01(0, point, 67) * length)
        cell, within = divmod(local, length)
        sparkle = math.sin(math.pi * (within + 0.5) / length) if hash01(cell, point, 71) < p["density"] else 0.0
        return band, sparkle, mix, position

    def spinner_pixels(self, unit_id: int, unit_count: int, pixel_count: int, flow_position: Sequence[float] | None) -> list[Rgb]:
        """Effect pixels of one spinner (pixel 0 … count − 1; the effect centre, the middle pixel, is the top)."""
        state = self._groups["spinner"]
        heights = self._heights.get(pixel_count)
        if heights is None:
            # Real height on the sphere (top 0, bottom 1): the band wipes down each spinner as it passes.
            heights = [
                (1 - math.cos(math.pi * abs((0.5 if pixel_count <= 1 else index / (pixel_count - 1)) * 2 - 1))) / 2
                for index in range(pixel_count)
            ]
            self._heights[pixel_count] = heights
        order = 0.5 if unit_count <= 1 else (unit_id - 1) / (unit_count - 1)
        x, y = flow_position if flow_position is not None else (order, order)
        span = state["p"]["sphereSpan"]
        colors = state["colors"]
        pixels: list[Rgb] = []
        for index, height in enumerate(heights):
            band, sparkle, mix, position = self._sample(state, x, y, span * height, unit_id * 1024 + index)
            r, g, b = _gradient(colors, position)
            if mix <= 0:
                pixels.append((r * band, g * band, b * band))
                continue
            # Sparkles use a whiter tint of the wind colour.
            keep, glint = band * (1 - mix), sparkle * mix
            pixels.append((r * keep + (r + (1 - r) * 0.5) * glint,
                           g * keep + (g + (1 - g) * 0.5) * glint,
                           b * keep + (b + (1 - b) * 0.5) * glint))
        return pixels

    def fixture_colors(self, group: str, geometry: Sequence[Sequence[float]]) -> list[Rgb]:
        """One colour per fixture; geometry is (normalized height, x, y) per fixture."""
        state = self._groups[group]
        span = state["p"]["heightSpan"]
        colors = state["colors"]
        level = state["maxLevel"]
        result: list[Rgb] = []
        for index, (height, x, y) in enumerate(geometry):
            # Higher fixtures lead (as the spinners wipe top to bottom).
            band, sparkle, mix, position = self._sample(state, x, y, span * (1 - height), 900000 + index)
            brightness = (band * (1 - mix) + sparkle * mix) * level
            r, g, b = _gradient(colors, position)
            white = 0.5 * mix
            result.append(((r + (1 - r) * white) * brightness, (g + (1 - g) * white) * brightness,
                           (b + (1 - b) * white) * brightness))
        return result


class WindStandby:
    """Standby switch, wind-flow presets and the test wind; ``frame`` is called by the output loop every frame."""

    def __init__(self, path: str | Path | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self.lock = Lock()
        # Memory only: the backend starts out of standby.
        self.enabled = False
        self.presets = {group: default_preset(group) for group in GROUPS}
        self.target = dict(DEFAULT_TARGET)
        self.warning: str | None = None
        if self.path is not None and self.path.exists():
            try:
                self._load(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as error:
                # A broken file must not stop the lights: defaults, and the page shows the warning.
                self.warning = f"wind settings ignored ({self.path.name}): {error}"
        self.speed = self.target["speedMs"]
        self.color_speed = self.target["speedMs"]
        self.flow_deg = self.target["flowDeg"]
        self.last_tick: float | None = None
        self.timings: dict[tuple[float, ...], _Timing] = {}

    def _load(self, raw: Any) -> None:
        if not isinstance(raw, dict) or raw.get("schemaVersion") != WIND_SCHEMA_VERSION:
            raise ValueError("unsupported schemaVersion")
        self.presets = {group: validate_preset(group, raw["presets"][group]) for group in GROUPS}
        self.target = validate_target(raw["target"])

    def _save_unlocked(self) -> None:
        if self.path is None:
            return
        document = {"schemaVersion": WIND_SCHEMA_VERSION, "presets": self.presets, "target": self.target}
        encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".wind-", suffix=".tmp", dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _state_unlocked(self) -> dict[str, Any]:
        return {
            "standby": self.enabled,
            "presets": copy.deepcopy(self.presets),
            "target": dict(self.target),
            # What the effect is using now (smoothed).
            "wind": {"speedMs": self.speed, "colorSpeedMs": self.color_speed, "flowDeg": self.flow_deg},
            "warning": self.warning,
        }

    def state(self) -> dict[str, Any]:
        with self.lock:
            return self._state_unlocked()

    def active(self) -> bool:
        with self.lock:
            return self.enabled

    def set_standby(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != {"enabled"} or type(raw["enabled"]) is not bool:
            raise ValueError("standby must be {\"enabled\": true|false}")
        with self.lock:
            if raw["enabled"] and not self.enabled:
                # Start from the current wind without a catch-up jump.
                self.last_tick = None
            self.enabled = raw["enabled"]
            return self._state_unlocked()

    def leave_standby(self) -> None:
        with self.lock:
            self.enabled = False

    def apply_presets(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != set(GROUPS):
            raise ValueError("wind presets require spinner, ball and wash")
        presets = {group: validate_preset(group, raw[group]) for group in GROUPS}
        with self.lock:
            self.presets = presets
            self._save_unlocked()
            return self._state_unlocked()

    def apply_target(self, raw: Any) -> dict[str, Any]:
        target = validate_target(raw)
        with self.lock:
            self.target = target
            self._save_unlocked()
            return self._state_unlocked()

    def _tick_unlocked(self) -> float:
        """Advance the smoothed wind; returns the elapsed seconds (at most 0.25 after a gap)."""
        now = self.clock()
        elapsed = 0.0 if self.last_tick is None else min(max(0.0, now - self.last_tick), 0.25)
        self.last_tick = now
        target = self.target
        instant = max(0.0, target["speedMs"] * (1 + target["gustiness"] * _fluctuation(now)))
        tau = target["smoothingSeconds"]
        if tau <= 0:
            self.speed = self.color_speed = instant
        else:
            self.speed += (instant - self.speed) * (1 - math.exp(-elapsed / tau))
            self.color_speed += (instant - self.color_speed) * (1 - math.exp(-elapsed / (tau * 3)))
        if self.speed >= CALM_DIRECTION_MS or tau <= 0:
            delta = (target["flowDeg"] - self.flow_deg + 540) % 360 - 180
            limit = 180.0 if tau <= 0 else MAX_TURN_DEG_PER_SECOND * elapsed
            self.flow_deg = (self.flow_deg + min(limit, max(-limit, delta))) % 360
        return elapsed

    def frame(self) -> WindFrame | None:
        """None out of standby; otherwise this frame's wind-flow (advances the wind and the bands)."""
        with self.lock:
            if not self.enabled:
                return None
            elapsed = self._tick_unlocked()
            timings: dict[str, _Timing] = {}
            advanced: set[tuple[float, ...]] = set()
            for group in GROUPS:
                p = self.presets[group]["parameters"]
                key = tuple(p[name] for name in TIMING_KEYS)
                timing = self.timings.get(key)
                if timing is None:
                    if len(self.timings) > 16:
                        self.timings.clear()
                    timing = self.timings[key] = _Timing(self.flow_deg)
                if key not in advanced:
                    advanced.add(key)
                    timing.advance(elapsed, self.speed, self.flow_deg, p)
                timings[group] = timing
            return WindFrame(self.presets, timings, self.flow_deg, self.color_speed)
