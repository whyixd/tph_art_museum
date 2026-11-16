"""色彩漫遊（color-roam），前端 src/effects/colorRoam.ts 的移植，逐值一致。

Port of frontend src/effects/colorRoam.ts, value for value: the roaming unit shows colour A (the gradient's last
colour), the rest colour B (the first), blending by 3D distance (cm) to the roamer within ``radiusCm``. The roamer
walks from spinner to spinner (a random pick among the closest few, skipping the ones just visited) and slides
continuously between them. The walk never restarts; checkpoints every ROAM_BLOCK_STEPS steps keep seeking cheap.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .effects import _round_half_up, clamp01, hash01

# 沒有場地配置時相鄰兩台的距離（同前端）。 / Spacing without a layout (as the frontend).
ROAM_INDEX_SPACING_CM = 250
START_SALT = 71
PICK_SALT = 73
ROAM_BLOCK_STEPS = 256
DEFAULTS = {"radiusCm": 400, "stepSeconds": 4, "choices": 3, "noReturn": 2, "roamers": 1, "curve": 1}

Positions = Sequence[Mapping[str, float] | None]


class RoamLayout:
    """Each unit's point (the axes every unit has) and the other units by distance, nearest first (ties by index)."""

    __slots__ = ("points", "neighbors")

    def __init__(self, positions: Positions | None, count: int) -> None:
        usable = positions if positions is not None and len(positions) == count else None
        axes = [] if usable is None else [
            key for key in ("x", "y", "z")
            if count and all(position is not None and key in position for position in usable)]
        if axes:
            points = [tuple(float(position[axis]) for axis in axes) for position in usable]  # type: ignore[index, union-attr]
        else:
            points = [(float(index * ROAM_INDEX_SPACING_CM),) for index in range(count)]
        neighbors = []
        for index, point in enumerate(points):
            others = []
            for other in range(count):
                if other == index:
                    continue
                distance = 0.0
                for axis in range(len(point)):
                    delta = point[axis] - points[other][axis]
                    distance += delta * delta
                others.append((distance, other))
            others.sort()
            neighbors.append(tuple(other for _, other in others))
        self.points = points
        self.neighbors = tuple(neighbors)


# 依配置陣列物件快取（以物件本身比對）；沒有配置時依台數。 / Cached by the positions object itself, or by count.
_LAYOUTS: dict[int, tuple[Any, RoamLayout]] = {}
_INDEX_LAYOUTS: dict[int, RoamLayout] = {}


def roam_layout(positions: Positions | None, count: int) -> RoamLayout:
    if positions is not None and len(positions) == count:
        hit = _LAYOUTS.get(id(positions))
        if hit is not None and hit[0] is positions:
            return hit[1]
        layout = RoamLayout(positions, count)
        if len(_LAYOUTS) >= 8:
            _LAYOUTS.clear()
        _LAYOUTS[id(positions)] = (positions, layout)
        return layout
    layout = _INDEX_LAYOUTS.get(count)
    if layout is None:
        layout = _INDEX_LAYOUTS[count] = RoamLayout(None, count)
    return layout


def roam_settings(p: Mapping[str, Any], count: int) -> tuple[int, int, int]:
    """(roamers, choices, no return)."""
    return (min(count, max(1, int(p.get("roamers", DEFAULTS["roamers"])))),
            max(1, int(p.get("choices", DEFAULTS["choices"]))),
            max(0, int(p.get("noReturn", DEFAULTS["noReturn"]))))


def roam_starts(count: int, roamers: int) -> list[int]:
    """The ``roamers`` units with the smallest hash."""
    return sorted(range(count), key=lambda index: (hash01(0, index, START_SALT), index))[:roamers]


def _pick(layout: RoamLayout, choices: int, roamer: int, step: int, tail: list[int]) -> int:
    current = tail[-1]
    every = layout.neighbors[current]
    if not every:
        return current
    recent = tail[:-1]
    candidates = [other for other in every if other not in recent] or list(every)
    candidates = candidates[:choices]
    return candidates[math.floor(hash01(step, roamer, PICK_SALT) * len(candidates))]


class _Walk:
    __slots__ = ("layout", "settings", "checkpoints", "block_index", "block_nodes")

    def __init__(self, layout: RoamLayout, settings: tuple[int, int, int]) -> None:
        self.layout = layout
        self.settings = settings
        # checkpoints[b][roamer]：第 b × BLOCK 步時的 tail。 / The tail at step b × BLOCK.
        self.checkpoints = [[[start] for start in roam_starts(len(layout.points), settings[0])]]
        self.block_index = -1
        self.block_nodes: list[list[int]] = []

    def run(self, index: int) -> tuple[list[list[int]], list[list[int]]]:
        _, choices, no_return = self.settings
        nodes, following = [], []
        for roamer, start in enumerate(self.checkpoints[index]):
            tail = list(start)
            path = [tail[-1]]
            for step in range(index * ROAM_BLOCK_STEPS, (index + 1) * ROAM_BLOCK_STEPS):
                unit = _pick(self.layout, choices, roamer, step, tail)
                path.append(unit)
                tail.append(unit)
                if len(tail) > no_return + 1:
                    del tail[0]
            nodes.append(path)
            following.append(tail)
        return nodes, following

    def segment(self, step: int) -> list[tuple[int, int]]:
        index = step // ROAM_BLOCK_STEPS
        if self.block_index != index:
            while len(self.checkpoints) <= index:
                last = len(self.checkpoints) - 1
                nodes, following = self.run(last)
                self.checkpoints.append(following)
                self.block_index, self.block_nodes = last, nodes
            if self.block_index != index:
                self.block_index, self.block_nodes = index, self.run(index)[0]
        offset = step - index * ROAM_BLOCK_STEPS
        return [(path[offset], path[offset + 1]) for path in self.block_nodes]


_WALKS: list[_Walk] = []


def roam_segment(layout: RoamLayout, settings: tuple[int, int, int], step: int) -> list[tuple[int, int]]:
    """Each roamer's (from, to) units on step ``step`` (frontend roamSegment)."""
    for walk in _WALKS:
        if walk.layout is layout and walk.settings == settings:
            break
    else:
        walk = _Walk(layout, settings)
        _WALKS.insert(0, walk)
        del _WALKS[4:]
    return walk.segment(step)


def roam_step_frames(p: Mapping[str, Any], fps: float) -> int:
    return max(1, _round_half_up(p.get("stepSeconds", DEFAULTS["stepSeconds"]) * fps))


def _smoothstep(value: float) -> float:
    t = clamp01(value)
    return t * t * (3 - 2 * t)


def color_roam_strength(p: Mapping[str, Any], frame: int, fps: float, unit_index: int, unit_count: int,
                        positions: Positions | None) -> float:
    """How close this unit is to colour A, 0..1 (1 = the roamer is here); frontend colorRoamStrength."""
    count = max(1, unit_count)
    unit = min(count - 1, max(0, unit_index))
    layout = roam_layout(positions, count)
    settings = roam_settings(p, count)
    step_frames = roam_step_frames(p, fps)
    at = max(0, frame)
    step = at // step_frames
    linear = (at - step * step_frames) / step_frames
    # 移動曲線：0 等速，1 兩端放慢。 / Curve: 0 linear, 1 eased at both ends.
    progress = _smoothstep(linear) if p.get("curve", DEFAULTS["curve"]) == 1 else linear
    radius = p.get("radiusCm", DEFAULTS["radiusCm"])
    point = layout.points[unit]
    strength = 0.0
    for start, end in roam_segment(layout, settings, step):
        a = layout.points[start]
        b = layout.points[end]
        squared = 0.0
        for axis in range(len(point)):
            delta = point[axis] - (a[axis] + (b[axis] - a[axis]) * progress)
            squared += delta * delta
        strength = max(strength, 1 - _smoothstep(math.sqrt(squared) / radius))
    return strength
