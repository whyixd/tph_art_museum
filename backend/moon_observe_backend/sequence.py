"""Shared sequence document, frame schedule, and atomic runtime storage."""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Any, Mapping

from .effects import Rgb, completion_frames as _completion_frames, preset_to_json, validate_preset
from .fixture_effects import fixture_completion_frames, fixture_preset_to_json, render_fixture_frame, validate_fixture_preset
from .renderer import render_frame

TRANSITION_KINDS = ("cut", "fade-black", "crossfade", "unit-fade-black", "unit-breathe-black")

# v2：每段同時指定轉燈（spinner，必填）、球燈（ball）、洗牆燈（wash）；省略 ball／wash = 全暗。v1 讀入時自動升級。
# v2: each step sets the spinner preset (required) and optional ball / wash fixture presets (omitted = off).
# v1 documents ({"preset": ...} per step) are upgraded on read.
SEQUENCE_SCHEMA_VERSION = 2
LEGACY_SEQUENCE_SCHEMA_VERSION = 1
MAX_STEPS = 64
FIXTURE_GROUPS = ("ball", "wash")
CLOCKS = ("spinner", *FIXTURE_GROUPS)


def _rounded_frames(value: float) -> int:
    return math.floor(value + 0.5)



def _fixture_count(fixture_counts: Mapping[str, int] | None, group: str) -> int:
    return max(1, int((fixture_counts or {}).get(group, 1)))


def _step_cycle(step: Mapping[str, Any], fps: int, unit_count: int, fixture_counts: Mapping[str, int] | None) -> int | None:
    """Frames of one cycle of the step's clock group (spinner unless playback.clock says otherwise)."""
    clock = step["playback"].get("clock", "spinner")
    if clock == "spinner":
        return _completion_frames(step["spinner"], fps, unit_count)
    return fixture_completion_frames(step[clock], fps, _fixture_count(fixture_counts, clock))


def validate_sequence(
    raw: Any, pixels: int = 156, fps: int = 40, unit_count: int = 9,
    fixture_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Validate a v1 or v2 sequence and return canonical v2.

    ``fixture_counts`` ({"ball": 37, ...}) is only needed for count-based steps clocked by fixture-chase.
    """
    if not isinstance(raw, dict) or not {"schemaVersion", "steps"} <= set(raw) or set(raw) - {"schemaVersion", "steps", "ending"}:
        raise ValueError("sequence must contain schemaVersion and steps")
    version = raw["schemaVersion"]
    if type(version) is not int or version not in (LEGACY_SEQUENCE_SCHEMA_VERSION, SEQUENCE_SCHEMA_VERSION):
        raise ValueError("unsupported sequence schemaVersion")
    ending = raw.get("ending", {"kind": "hold"})
    if not isinstance(ending, dict):
        raise ValueError("invalid sequence ending")
    if ending.get("kind") in ("hold", "loop") and set(ending) == {"kind"}:
        pass
    elif ending.get("kind") == "fade-black" and set(ending) == {"kind", "seconds"}:
        seconds = ending["seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 / fps <= seconds <= 10:
            raise ValueError("ending fade seconds must be finite and within 1 frame..10")
    else:
        raise ValueError("invalid sequence ending")
    steps = raw["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"sequence must have 1..{MAX_STEPS} steps")
    ids: set[int] = set()
    result: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if version == LEGACY_SEQUENCE_SCHEMA_VERSION:
            if not isinstance(step, dict) or set(step) != {"id", "preset", "playback", "transition"}:
                raise ValueError(f"step {index + 1} must contain id, preset, playback and transition")
            step = {"id": step["id"], "spinner": step["preset"], "playback": step["playback"], "transition": step["transition"]}
        elif not isinstance(step, dict) or not {"id", "spinner", "playback", "transition"} <= set(step) \
                or set(step) - {"id", "spinner", *FIXTURE_GROUPS, "playback", "transition"}:
            raise ValueError(f"step {index + 1} must contain id, spinner, playback and transition (optional: ball, wash)")
        step_id = step["id"]
        if type(step_id) is not int or step_id <= 0 or step_id in ids:
            raise ValueError("sequence step ids must be distinct positive integers")
        ids.add(step_id)
        normalized = {"spinner": validate_preset(step["spinner"], pixels)}
        for group in FIXTURE_GROUPS:
            if group in step:
                normalized[group] = validate_fixture_preset(step[group])
        playback = step["playback"]
        if not isinstance(playback, dict):
            raise ValueError("invalid sequence playback")
        if playback.get("kind") == "count" and {"kind", "count"} <= set(playback) <= {"kind", "count", "clock"}:
            count = playback["count"]
            if type(count) is not int or not 1 <= count <= 100:
                raise ValueError("playback count must be 1..100")
            clock = playback.get("clock", "spinner")
            if clock not in CLOCKS:
                raise ValueError("playback clock must be spinner, ball or wash")
            if clock not in normalized:
                raise ValueError(f"playback clock {clock} needs a {clock} preset in the step")
            if _step_cycle({**normalized, "playback": playback}, fps, unit_count, fixture_counts) is None:
                raise ValueError("playback count requires an effect completion boundary")
        elif playback.get("kind") == "time" and set(playback) == {"kind", "seconds"}:
            seconds = playback["seconds"]
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 3600:
                raise ValueError("playback seconds must be finite and within 1 frame..3600")
        else:
            raise ValueError("invalid sequence playback")
        transition = step["transition"]
        if not isinstance(transition, dict) or set(transition) != {"kind", "seconds"}:
            raise ValueError("invalid sequence transition")
        if transition["kind"] not in TRANSITION_KINDS:
            raise ValueError("unknown sequence transition")
        seconds = transition["seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 10:
            raise ValueError("transition seconds must be finite and within 0..10")
        canonical: dict[str, Any] = {"id": step_id, "spinner": preset_to_json(normalized["spinner"])}
        for group in FIXTURE_GROUPS:
            if group in normalized:
                canonical[group] = fixture_preset_to_json(normalized[group])
        canonical["playback"] = copy.deepcopy(playback)
        canonical["transition"] = copy.deepcopy(transition)
        result.append(canonical)
    return {"schemaVersion": SEQUENCE_SCHEMA_VERSION, "ending": copy.deepcopy(ending), "steps": result}


def compile_sequence(
    document: Mapping[str, Any], fps: int, unit_count: int, pixels: int = 156,
    fixture_counts: Mapping[str, int] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Frame schedule. ``preset`` is the spinner preset; ``ball`` / ``wash`` are fixture presets or None (off).

    ``cycle`` is the clock group's cycle (count steps); only that group wraps its frames per repeat,
    the other groups run freely through the step.
    """
    raw = validate_sequence(document, pixels, fps, unit_count, fixture_counts)
    compiled: list[dict[str, Any]] = []
    start = 0
    for index, step in enumerate(raw["steps"]):
        preset = validate_preset(step["spinner"], pixels)
        fixtures = {group: validate_fixture_preset(step[group]) if group in step else None for group in FIXTURE_GROUPS}
        playback = step["playback"]
        clock = playback.get("clock", "spinner") if playback["kind"] == "count" else None
        cycle = _step_cycle({"spinner": preset, **fixtures, "playback": playback}, fps, unit_count, fixture_counts) \
            if playback["kind"] == "count" else None
        duration = cycle * playback["count"] if cycle is not None else max(1, _rounded_frames(playback["seconds"] * fps))
        previous = raw["steps"][index - 1] if index else None
        transition_frames = 0 if previous is None or previous["transition"]["kind"] == "cut" else min(
            duration, max(0, _rounded_frames(previous["transition"]["seconds"] * fps))
        )
        compiled.append({
            "id": step["id"], "preset": preset, **fixtures, "start": start, "duration": duration, "cycle": cycle,
            "clock": clock,
            "group_cycles": {group: cycle if clock == group else None for group in CLOCKS},
            "transition_frames": transition_frames,
            "transition_kind": step["transition"]["kind"],
        })
        start += duration
    ending = raw["ending"]
    last_transition = raw["steps"][-1]["transition"]
    compiled[0]["ending"] = ending
    compiled[0]["end_fade_frames"] = max(1, _rounded_frames(ending["seconds"] * fps)) if ending["kind"] == "fade-black" else 0
    compiled[0]["loop_transition_frames"] = (
        min(compiled[0]["duration"], max(0, _rounded_frames(last_transition["seconds"] * fps)))
        if ending["kind"] == "loop" and last_transition["kind"] != "cut" else 0
    )
    return tuple(compiled)


# (elapsed, frames of the fade-out half, breathe) on the outgoing layer of unit-fade-black / unit-breathe-black.
UnitFade = tuple[int, int, bool] | None
BREATH_DEPTH = 0.85


def unit_fade_weight(fade: tuple[int, int, bool], unit_index: int, unit_count: int) -> float:
    """Per-unit weight of unit-fade-black / unit-breathe-black, identical to the frontend's unitFadeWeight.

    unit-fade-black: each unit fades over a third of the first half (the whole half for one unit); start
    times spread evenly from unit 1 to the last, which reaches black at the midpoint.
    unit-breathe-black: each unit takes half of the first half, spread the same way; its first 2/3 is one
    breath (down to 1 - BREATH_DEPTH and back), the last 1/3 fades out.
    """
    elapsed, frames, breathe = fade
    fade_frames = frames / min(2 if breathe else 3, max(1, unit_count))
    start = 0 if unit_count <= 1 else (frames - fade_frames) * unit_index / (unit_count - 1)
    progress = min(1.0, max(0.0, (elapsed + 1 - start) / fade_frames))
    if not breathe:
        return 1 - progress
    if progress < 2 / 3:
        return 1 - BREATH_DEPTH * (0.5 - 0.5 * math.cos(2 * math.pi * progress * 1.5))
    return min(1.0, (1 - progress) * 3)


def _layer_details(
    compiled: tuple[dict[str, Any], ...], frame: int,
) -> tuple[tuple[Mapping[str, Any], int, int | None, int, float, UnitFade], ...]:
    """Spinner layers as (preset, elapsed frames in step, wrap cycle, source frame, weight, unit fade).

    unit fade is (elapsed, frames of the fade-out half) on the outgoing layer of a
    unit-fade-black transition, else None.
    """
    return tuple(
        (step["preset"], elapsed, step["group_cycles"]["spinner"], source, weight, fade)
        for step, elapsed, source, weight, fade in _step_layers(compiled, frame)
    )


def _step_layers(
    compiled: tuple[dict[str, Any], ...], frame: int,
) -> tuple[tuple[Mapping[str, Any], int, int, float, UnitFade], ...]:
    """Layers as (compiled step, elapsed frames in step, source frame, weight, unit fade)."""
    total = compiled[-1]["start"] + compiled[-1]["duration"]
    frame = max(0, frame)
    loop = compiled[0]["ending"]["kind"] == "loop"
    cycle_start = frame // total * total if loop else 0
    cycle_frame = frame - cycle_start if loop else frame
    complete = not loop and frame >= total
    if complete:
        last = compiled[-1]
        fade_frames = compiled[0]["end_fade_frames"]
        weight = max(0.0, 1 - (frame - total + 1) / fade_frames) if fade_frames else 1.0
        return ((last, last["duration"] - 1, total - 1, weight, None),)

    index = next(i for i, step in enumerate(compiled) if cycle_frame < step["start"] + step["duration"])
    step = compiled[index]
    elapsed = cycle_frame - step["start"]
    source_frame = cycle_start + step["start"] + elapsed
    current = (step, elapsed, source_frame, 1.0, None)
    frames = compiled[0]["loop_transition_frames"] if index == 0 and cycle_start > 0 else step["transition_frames"]
    if elapsed >= frames:
        return (current,)

    previous = compiled[index - 1] if index else compiled[-1]
    unit_fade: UnitFade = None
    if previous["transition_kind"] == "crossfade":
        current_weight = (elapsed + 1) / (frames + 1)
        outgoing_weight = 1 - current_weight
    else:
        midpoint = (frames - 1) // 2
        current_weight = (elapsed - midpoint) / (frames - 1 - midpoint) if elapsed > midpoint else 0.0
        if previous["transition_kind"] in ("unit-fade-black", "unit-breathe-black"):
            # 前半逐台淡出（各台權重見 unit_fade_weight），後半同 fade-black 一起淡入。
            # First half fades units out one by one (see unit_fade_weight); the second half fades in like fade-black.
            outgoing_weight = 1.0 if elapsed <= midpoint else 0.0
            unit_fade = (elapsed, midpoint + 1, previous["transition_kind"] == "unit-breathe-black")
        else:
            outgoing_weight = 1 - (elapsed + 1) / (midpoint + 1) if elapsed < midpoint else 0.0
    prior_elapsed = previous["duration"] - 1
    prior_source = cycle_start + previous["start"] + prior_elapsed - (total if index == 0 else 0)
    outgoing = (previous, prior_elapsed, prior_source, outgoing_weight, unit_fade)
    return (outgoing, (step, elapsed, source_frame, current_weight, None))


def sequence_position(compiled: tuple[dict[str, Any], ...], frame: int) -> dict[str, Any]:
    """Playback position at a frame: active step, repeat, transition and ending state."""
    total = compiled[-1]["start"] + compiled[-1]["duration"]
    frame = max(0, frame)
    ending = compiled[0]["ending"]["kind"]
    loop = ending == "loop"
    iteration = frame // total if loop else 0
    ended = not loop and frame >= total
    cycle_frame = frame - iteration * total if loop else min(frame, total - 1)
    index = next(i for i, step in enumerate(compiled) if cycle_frame < step["start"] + step["duration"])
    step = compiled[index]
    elapsed = cycle_frame - step["start"]
    cycle = step["cycle"]
    frames = compiled[0]["loop_transition_frames"] if index == 0 and iteration > 0 else step["transition_frames"]
    previous = index - 1 if index else len(compiled) - 1
    fade_frames = compiled[0]["end_fade_frames"]
    return {
        "frame": frame,
        "sequenceFrame": total if ended else cycle_frame,
        "totalFrames": total,
        "ending": ending,
        "loopIteration": iteration + 1,
        "ended": ended,
        "endFade": ({"elapsed": min(frame - total + 1, fade_frames), "frames": fade_frames}
                    if ended and fade_frames else None),
        "step": {
            "index": index, "id": step["id"], "effectId": step["preset"]["effectId"],
            "elapsed": elapsed, "duration": step["duration"],
            "repeat": {"current": elapsed // cycle + 1, "total": step["duration"] // cycle} if cycle else None,
        },
        "transition": (None if ended or elapsed >= frames else {
            "kind": compiled[previous]["transition_kind"], "fromIndex": previous,
            "elapsed": elapsed, "frames": frames,
        }),
    }


def sequence_layers(compiled: tuple[dict[str, Any], ...], frame: int) -> tuple[tuple[Mapping[str, Any], int, int, float], ...]:
    return tuple(
        (preset, elapsed if cycle is None else elapsed % cycle, source_frame, weight)
        for preset, elapsed, cycle, source_frame, weight, _ in _layer_details(compiled, frame)
    )


def render_sequence_frame(
    compiled: tuple[dict[str, Any], ...],
    unit: Mapping[str, Any],
    frame: int,
    *,
    unit_count: int,
    fps: int,
    rpm: int,
    rain: bool = False,
    color_override: Mapping[str, Any] | None = None,
    shared: dict[Any, list[Rgb]] | None = None,
) -> list[tuple[float, float, float]]:
    layers = _layer_details(compiled, frame)
    count = int(unit["logicalPixelCount"])
    result = [[0.0, 0.0, 0.0] for _ in range(count)]
    unit_index = int(unit["unitId"]) - 1
    for preset, elapsed, cycle, source_frame, weight, unit_fade in layers:
        if unit_fade is not None:
            weight *= unit_fade_weight(unit_fade, unit_index, unit_count)
        if weight <= 0:
            continue
        colors = render_frame(
            preset, unit, elapsed, unit_count=unit_count, fps=fps, rpm=rpm,
            phase_frame=source_frame, raining=rain, cycle_frames=cycle,
            color_override=color_override, shared=shared,
        )
        for target, color in zip(result, colors):
            for channel in range(3):
                target[channel] += color[channel] * weight
    return [tuple(color) for color in result]


def render_sequence_fixtures(
    compiled: tuple[dict[str, Any], ...],
    group: str,
    geometry: list[tuple[float, float, float]],
    frame: int,
    *,
    fps: int,
    rain: bool = False,
    color_override: Mapping[str, Any] | None = None,
    frame_step: int = 0,
) -> list[Rgb]:
    """One DMX group's colours; a step without that group's preset is dark.

    unit-fade-black fades the fixtures out one after another in id (DMX chain) order.
    """
    if group not in FIXTURE_GROUPS:
        raise ValueError(f"group must be one of {', '.join(FIXTURE_GROUPS)}")
    count = len(geometry)
    result = [[0.0, 0.0, 0.0] for _ in range(count)]
    for step, elapsed, _, weight, unit_fade in _step_layers(compiled, frame):
        preset = step[group]
        if preset is None or weight <= 0:
            continue
        colors = render_fixture_frame(
            preset, geometry, elapsed, fps=fps, raining=rain, color_override=color_override,
            frame_step=frame_step, cycle_frames=step["group_cycles"][group],
        )
        for index, (target, color) in enumerate(zip(result, colors)):
            scale = weight * (unit_fade_weight(unit_fade, index, count) if unit_fade is not None else 1.0)
            for channel in range(3):
                target[channel] += color[channel] * scale
    return [tuple(color) for color in result]  # type: ignore[misc]


class SequenceStore:
    def __init__(self, path: str | Path, pixels: int, fps: int, unit_count: int, fallback_preset: Mapping[str, Any],
                 fixture_counts: Mapping[str, int] | None = None,
                 fallback_fixtures: Mapping[str, Mapping[str, Any] | None] | None = None) -> None:
        """Without a sequence file, one 8 s step of ``fallback_preset`` (plus ``fallback_fixtures``) is written."""
        self.path = Path(path)
        self.pixels = pixels
        self.fixture_counts = dict(fixture_counts or {})
        self.fps = fps
        self.unit_count = unit_count
        self.lock = Lock()
        self.revision = 0
        self.generation = 0
        self.mode = "sequence"
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.raw = validate_sequence(raw, pixels, fps, unit_count, self.fixture_counts)
        else:
            self.raw = validate_sequence({
                "schemaVersion": SEQUENCE_SCHEMA_VERSION,
                "ending": {"kind": "hold"},
                "steps": [{
                    "id": 1, "spinner": fallback_preset,
                    **{group: preset for group, preset in (fallback_fixtures or {}).items() if preset is not None},
                    "playback": {"kind": "time", "seconds": 8},
                    "transition": {"kind": "cut", "seconds": 0.5},
                }],
            }, pixels, fps, unit_count, self.fixture_counts)
            self._write(self.raw)
        self.compiled = compile_sequence(self.raw, fps, unit_count, pixels, self.fixture_counts)

    def _write(self, document: Mapping[str, Any]) -> None:
        encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".sequence-", suffix=".tmp", dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {"revision": self.revision, "sequence": copy.deepcopy(self.raw), "playbackMode": self.mode}

    def snapshot(self) -> tuple[str, tuple[dict[str, Any], ...], int]:
        with self.lock:
            return self.mode, self.compiled, self.generation

    def apply(self, raw: Any) -> dict[str, Any]:
        document = validate_sequence(raw, self.pixels, self.fps, self.unit_count, self.fixture_counts)
        compiled = compile_sequence(document, self.fps, self.unit_count, self.pixels, self.fixture_counts)
        with self.lock:
            self._write(document)
            self.raw = document
            self.compiled = compiled
            self.revision += 1
            self.generation += 1
            self.mode = "sequence"
            return self.state_unlocked()

    def use_single_preset(self) -> None:
        with self.lock:
            self.mode = "single"
            self.generation += 1

    def restart(self, raw: Any) -> dict[str, Any]:
        """Replay the chosen source from frame 0 without rewriting any file."""
        if not isinstance(raw, dict) or set(raw) != {"source"} or raw["source"] not in ("sequence", "single"):
            raise ValueError("restart must be {\"source\": \"sequence\"|\"single\"}")
        with self.lock:
            self.mode = raw["source"]
            self.generation += 1
            return self.state_unlocked()

    def state_unlocked(self) -> dict[str, Any]:
        return {"revision": self.revision, "sequence": copy.deepcopy(self.raw), "playbackMode": self.mode}
