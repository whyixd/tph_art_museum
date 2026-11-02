"""DMX fixtures (ball lights, wall washers) sent through Art-Net -> DMX nodes.

Pure functions only (no sockets, no state). Every fixture shows one colour per frame; a profile
says how that colour becomes the fixture's channels, the patch says where those channels sit
(node universe + start address). Each node universe is sent as one full 512-channel frame.

球燈、洗牆燈：每顆每影格一個顏色，依 profile 轉成通道值，依 patch 放進所屬 universe 的 512 bytes。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from .effects import Rgb, clamp01

DMX_CHANNELS = 512
GROUPS = ("ball", "wash")
COLOR_CHANNELS = ("r", "g", "b", "w")
WHITE_MODES = ("min", "none")


def _byte(value: float) -> int:
    # 與轉燈相同的捨入：floor(x + 0.5)。 / Same rounding as the spinners.
    return math.floor(clamp01(value) * 255 + 0.5)


def color_levels(color: Rgb, white: str, brightness: float = 1.0) -> dict[str, float]:
    """RGB (0..1) -> r/g/b/w levels (0..1).

    ``white = "min"`` moves the common part of R, G and B to the white LED (W = min(R, G, B)),
    so white looks white and saturated colours stay saturated; ``"none"`` leaves W dark.
    """
    r, g, b = (clamp01(channel) * brightness for channel in color)
    if white == "min":
        w = min(r, g, b)
        return {"r": r - w, "g": g - w, "b": b - w, "w": w}
    if white == "none":
        return {"r": r, "g": g, "b": b, "w": 0.0}
    raise ValueError(f"white must be one of {', '.join(WHITE_MODES)}")


def fixture_bytes(color: Rgb, profile: Mapping[str, Any]) -> bytes:
    """One fixture's channel values in profile order; non-colour channels use their static value."""
    levels = color_levels(color, profile["white"], profile["brightness"])
    return bytes(
        _byte(levels[name]) if name in COLOR_CHANNELS else profile["static"][name]
        for name in profile["channels"]
    )


def blank_universes(dmx: Mapping[str, Any]) -> list[bytearray]:
    return [bytearray(DMX_CHANNELS) for _ in dmx["nodes"]]


def build_universes(colors: Mapping[str, Sequence[Rgb]], dmx: Mapping[str, Any]) -> list[bytes]:
    """512-byte frames, one per configured node universe (in ``dmx["nodes"]`` order).

    ``colors[group][i]`` is the colour of ``dmx["fixtures"][group][i]`` (fixtures sorted by id).
    Channels no fixture uses stay 0.
    """
    frames = blank_universes(dmx)
    for group, fixtures in dmx["fixtures"].items():
        group_colors = colors.get(group)
        if group_colors is None:
            continue
        if len(group_colors) != len(fixtures):
            raise ValueError(f"{group} needs {len(fixtures)} colours")
        for fixture, color in zip(fixtures, group_colors):
            values = fixture_bytes(color, dmx["profiles"][fixture["profile"]])
            start = fixture["address"] - 1
            frames[fixture["node"]][start:start + len(values)] = values
    return [bytes(frame) for frame in frames]


def patch_report(dmx: Mapping[str, Any]) -> Iterable[str]:
    """Human-readable patch: one line per fixture, grouped by node universe (for on-site wiring)."""
    for index, node in enumerate(dmx["nodes"]):
        yield f"{node['name']}  {node['ip']}  universe {node['universe']}"
        rows = sorted(
            (fixture["address"], group, fixture)
            for group, fixtures in dmx["fixtures"].items()
            for fixture in fixtures
            if fixture["node"] == index
        )
        if not rows:
            yield "  (no fixtures)"
        for address, group, fixture in rows:
            profile = dmx["profiles"][fixture["profile"]]
            end = address + len(profile["channels"]) - 1
            yield (f"  {group:4} {fixture['id']:3}  ch {address:3}-{end:3}  "
                   f"{fixture['profile']} ({' '.join(profile['channels'])})")
