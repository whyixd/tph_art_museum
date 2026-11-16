"""亮度漫遊（brightness-walk），前端 src/fixtures/brightnessWalk.ts 的移植，逐值一致。

Port of frontend src/fixtures/brightnessWalk.ts, value for value: a few random balls start; brightness flows
from the current ball (A) to a nearby next one (B) with A + B = 1, then on from B. The next ball is a random pick
among the closest few, skipping the balls just visited. Paths meeting on a ball add up, capped at 1.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
import math
from typing import Any, Mapping, Sequence

from .effects import _round_half_up, clamp01, hash01

Neighbors = tuple[tuple[int, ...], ...]

# 雜湊的鹽與路徑編號間距（同前端）。 / Hash salts and the walker stride (as the frontend).
START_SALT = 61
PICK_SALT = 67
WALKER_STRIDE = 1024


def fixture_neighbors(positions: Sequence[Mapping[str, float] | None]) -> Neighbors:
    """Every other fixture by squared distance over the axes all fixtures have (x, y, z), ties by index.

    Without a shared axis the order is by index difference (frontend fixtureNeighbors)."""
    count = len(positions)
    axes = [key for key in ("x", "y", "z")
            if count and all(position is not None and key in position for position in positions)]
    table = []
    for index in range(count):
        others = []
        for other in range(count):
            if other == index:
                continue
            if not axes:
                distance: float = (index - other) * (index - other)
            else:
                distance = 0
                for axis in axes:
                    delta = positions[index][axis] - positions[other][axis]  # type: ignore[index]
                    distance += delta * delta
            others.append((distance, other))
        others.sort()
        table.append(tuple(other for _, other in others))
    return tuple(table)


@lru_cache(maxsize=8)
def _neighbors_by_index(count: int) -> Neighbors:
    return fixture_neighbors([None] * count)


def walk_settings(p: Mapping[str, Any], count: int) -> tuple[int, int, int, int]:
    """(walkers, choices, no return, steps per round)."""
    return (min(count, max(1, int(p["walkers"]))), max(1, int(p["choices"])), max(0, int(p["noReturn"])),
            max(1, int(p["roundSteps"])))


def walk_step_frames(p: Mapping[str, Any], fps: float) -> int:
    return max(1, _round_half_up(p["stepSeconds"] * fps))


def walk_paths(round_index: int, neighbors: Neighbors, settings: tuple[int, int, int, int]) -> list[list[int]]:
    """Each path's fixtures in a round (steps + 1 each); the starts are the fixtures with the smallest hash."""
    walkers, choices, no_return, steps = settings
    count = len(neighbors)
    order = sorted(range(count), key=lambda index: (hash01(round_index, index, START_SALT), index))
    paths = []
    for walker, start in enumerate(order[:walkers]):
        path = [start]
        for step in range(steps):
            current = path[step]
            recent = path[max(0, step - no_return):step]
            candidates = [other for other in neighbors[current] if other not in recent] or list(neighbors[current])
            candidates = candidates[:choices]
            pick = math.floor(hash01(round_index, walker * WALKER_STRIDE + step, PICK_SALT) * len(candidates))
            path.append(candidates[pick])
        paths.append(path)
    return paths


# 每顆燈各問一次，路徑只算一次：記住最近幾組（以鄰近表物件本身比對）。 / Asked per fixture; keep the last few path sets.
_PATHS: OrderedDict[tuple[int, int, tuple[int, int, int, int]], tuple[Neighbors, list[list[int]]]] = OrderedDict()


def _cached_paths(round_index: int, neighbors: Neighbors, settings: tuple[int, int, int, int]) -> list[list[int]]:
    key = (id(neighbors), round_index, settings)
    hit = _PATHS.get(key)
    if hit is not None and hit[0] is neighbors:
        _PATHS.move_to_end(key)
        return hit[1]
    paths = walk_paths(round_index, neighbors, settings)
    _PATHS[key] = (neighbors, paths)
    while len(_PATHS) > 8:
        _PATHS.popitem(last=False)
    return paths


def _smoothstep(value: float) -> float:
    t = clamp01(value)
    return t * t * (3 - 2 * t)


def brightness_walk(p: Mapping[str, Any], frame: int, fps: float, fixture_index: int, fixture_count: int,
                    neighbors: Neighbors | None) -> float:
    if fixture_count <= 1:
        return 1.0
    table = neighbors if neighbors is not None and len(neighbors) == fixture_count else _neighbors_by_index(fixture_count)
    settings = walk_settings(p, fixture_count)
    step_frames = walk_step_frames(p, fps)
    round_frames = step_frames * settings[3]
    at = max(0, frame)
    round_index = at // round_frames
    local = at - round_index * round_frames
    step = local // step_frames
    linear = (local - step * step_frames) / step_frames
    # 流動曲線：0 等速，1 兩端放慢。 / Curve: 0 linear, 1 eased at both ends.
    progress = _smoothstep(linear) if p["curve"] == 1 else linear
    total = 0.0
    for path in _cached_paths(round_index, table, settings):
        if path[step] == fixture_index:
            total += 1 - progress
        if path[step + 1] == fixture_index:
            total += progress
    return min(1.0, total)
