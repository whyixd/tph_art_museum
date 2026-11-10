"""Shows: effects placed on a music track's timeline, played by the audio clock (M12).

演出（Show）＝一首曲目＋一串段落。每段以「第幾小節第幾拍」或秒數指定開始位置，到下一段開始為止；
轉場可用拍數指定長度。演出編譯成與串接（sequence）相同的影格表，沿用同一套渲染與轉場；
播放時影格由音樂時間決定（音訊時鐘＋延遲補償），不是輸出迴圈自己的時鐘。
A show is one track plus sections. Each section starts at a bar/beat of the track (or a time in seconds) and
lasts until the next one; transitions can be measured in beats. A show compiles to the same frame schedule as a
sequence, so rendering and transitions are shared; while it plays, the frame comes from the music time (the
audio clock plus the calibration offset), not from the output loop's own clock.
"""

from __future__ import annotations

from bisect import bisect_right
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
import time
from typing import Any, Callable, Mapping

from .effects import preset_to_json, validate_preset
from .fixture_effects import fixture_preset_to_json, validate_fixture_preset
from .sequence import CLOCKS, FIXTURE_GROUPS, _rounded_frames

SHOW_SCHEMA_VERSION = 1
SHOWS_FILE_SCHEMA_VERSION = 1
MAX_SHOWS = 32
MAX_SECTIONS = 256
MAX_OVERRIDES = 64  # 每段最多幾組轉燈覆寫 / spinner overrides per section
MAX_OVERRIDE_UNIT = 64  # 與 config.MAX_UNITS 相同 / same as config.MAX_UNITS
MAX_TRANSITION_BEATS = 32
MAX_TRANSITION_SECONDS = 10
MAX_END_FADE_SECONDS = 10
SHOW_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
TRANSITIONS = ("cut", "fade-black", "crossfade", "unit-fade-black", "unit-breathe-black")


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number in [{low}, {high}]")
    return value


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _validate_start(raw: Any, label: str) -> dict[str, Any]:
    if isinstance(raw, dict) and set(raw) == {"seconds"}:
        return {"seconds": _number(raw["seconds"], f"{label} start seconds", 0, 3600)}
    if isinstance(raw, dict) and {"bar"} <= set(raw) <= {"bar", "beat"}:
        start = {"bar": _integer(raw["bar"], f"{label} start bar", 0, 9999)}
        if "beat" in raw:
            start["beat"] = _integer(raw["beat"], f"{label} start beat", 1, 16)
        return start
    raise ValueError(f"{label} start must be {{seconds}} or {{bar, beat?}}")


def _validate_transition(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("kind") not in TRANSITIONS:
        raise ValueError(f"{label} transition kind must be one of {', '.join(TRANSITIONS)}")
    if set(raw) == {"kind", "seconds"}:
        return {"kind": raw["kind"], "seconds": _number(raw["seconds"], f"{label} transition seconds", 0, MAX_TRANSITION_SECONDS)}
    if set(raw) == {"kind", "beats"}:
        return {"kind": raw["kind"], "beats": _number(raw["beats"], f"{label} transition beats", 0, MAX_TRANSITION_BEATS)}
    raise ValueError(f"{label} transition must be {{kind, seconds}} or {{kind, beats}}")


def _validate_overrides(raw: Any, label: str, pixels: int) -> list[dict[str, Any]]:
    """[{units: [unit numbers from 1], spinner: EffectPreset}]: some spinners of a section play another effect.

    A unit belongs to at most one override per section; units are stored sorted.
    段落中某幾台轉燈改用別的效果；同一段裡一台只能屬於一個覆寫。"""
    if not isinstance(raw, list) or len(raw) > MAX_OVERRIDES:
        raise ValueError(f"{label} overrides must be a list of at most {MAX_OVERRIDES}")
    seen: set[int] = set()
    result = []
    for index, entry in enumerate(raw):
        name = f"{label} override {index + 1}"
        if not isinstance(entry, dict) or set(entry) != {"units", "spinner"}:
            raise ValueError(f"{name} must contain units and spinner")
        units = entry["units"]
        if not isinstance(units, list) or not units or any(
                type(unit) is not int or not 1 <= unit <= MAX_OVERRIDE_UNIT for unit in units):
            raise ValueError(f"{name} units must list unit numbers 1..{MAX_OVERRIDE_UNIT}")
        if len(set(units)) != len(units) or seen & set(units):
            raise ValueError(f"{name}: a spinner can belong to only one override")
        seen |= set(units)
        result.append({"units": sorted(units), "spinner": preset_to_json(validate_preset(entry["spinner"], pixels))})
    return result


def validate_show(raw: Any, pixels: int = 156) -> dict[str, Any]:
    """Check the document's shape and presets and return it canonical; positions are checked by ``compile_show``."""
    fields = {"schemaVersion", "id", "title", "trackId", "ending", "sections"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("show must contain " + ", ".join(sorted(fields)))
    if type(raw["schemaVersion"]) is not int or raw["schemaVersion"] != SHOW_SCHEMA_VERSION:
        raise ValueError("unsupported show schemaVersion")
    if not isinstance(raw["id"], str) or not SHOW_ID.fullmatch(raw["id"]):
        raise ValueError("show id must be 1-40 characters of a-z, 0-9 and - (not starting with -)")
    title = raw["title"]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
        raise ValueError("show title must be 1-60 characters")
    if not isinstance(raw["trackId"], str) or not 1 <= len(raw["trackId"]) <= 40:
        raise ValueError("show trackId must be a track id")
    ending = raw["ending"]
    if ending == {"kind": "loop"}:
        canonical_ending: dict[str, Any] = {"kind": "loop"}
    elif isinstance(ending, dict) and set(ending) == {"kind", "seconds"} and ending["kind"] == "fade-black":
        canonical_ending = {"kind": "fade-black",
                            "seconds": _number(ending["seconds"], "ending fade seconds", 0, MAX_END_FADE_SECONDS)}
    else:
        raise ValueError("show ending must be {kind: loop} or {kind: fade-black, seconds}")
    sections = raw["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ValueError(f"show must have 1..{MAX_SECTIONS} sections")
    ids: set[int] = set()
    result: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        label = f"section {index + 1}"
        if not isinstance(section, dict) or not {"id", "start", "spinner", "transition"} <= set(section) \
                or set(section) - {"id", "start", "spinner", *FIXTURE_GROUPS, "overrides", "transition"}:
            raise ValueError(f"{label} must contain id, start, spinner and transition (optional: ball, wash, overrides)")
        section_id = section["id"]
        if type(section_id) is not int or section_id <= 0 or section_id in ids:
            raise ValueError("section ids must be distinct positive integers")
        ids.add(section_id)
        start = _validate_start(section["start"], label)
        if index == 0 and start != {"seconds": 0}:
            raise ValueError("the first section must start at {seconds: 0}")
        canonical: dict[str, Any] = {
            "id": section_id, "start": start, "spinner": preset_to_json(validate_preset(section["spinner"], pixels)),
        }
        for group in FIXTURE_GROUPS:
            if group in section:
                canonical[group] = fixture_preset_to_json(validate_fixture_preset(section[group]))
        if "overrides" in section:
            overrides = _validate_overrides(section["overrides"], label, pixels)
            if overrides:
                canonical["overrides"] = overrides
        canonical["transition"] = _validate_transition(section["transition"], label)
        result.append(canonical)
    return {
        "schemaVersion": SHOW_SCHEMA_VERSION, "id": raw["id"], "title": title.strip(), "trackId": raw["trackId"],
        "ending": canonical_ending, "sections": result,
    }


def beat_index(beat_map: Mapping[str, Any], bar: int, beat: int = 1) -> int:
    """Index into ``beats`` of a bar's beat; bar 1 starts at ``firstDownbeat``, bar 0 is the pickup."""
    return beat_map["firstDownbeat"] + (bar - 1) * beat_map["beatsPerBar"] + (beat - 1)


def start_seconds(start: Mapping[str, Any], beat_map: Mapping[str, Any]) -> float:
    if "seconds" in start:
        return float(start["seconds"])
    beat = start.get("beat", 1)
    if beat > beat_map["beatsPerBar"]:
        raise ValueError(f"beat {beat} is past the {beat_map['beatsPerBar']} beats of a bar")
    index = beat_index(beat_map, start["bar"], beat)
    if not 0 <= index < len(beat_map["beats"]):
        raise ValueError(f"bar {start['bar']} beat {beat} is outside the track's beats")
    return float(beat_map["beats"][index])


def beat_position(beats: list[float], seconds: float) -> float:
    """Fractional beat index at a time: linear between beats, the nearest interval extended outside them."""
    index = min(max(bisect_right(beats, seconds) - 1, 0), len(beats) - 2)
    return index + (seconds - beats[index]) / (beats[index + 1] - beats[index])


def beat_time(beats: list[float], position: float) -> float:
    index = min(max(math.floor(position), 0), len(beats) - 2)
    return beats[index] + (position - index) * (beats[index + 1] - beats[index])


def transition_seconds(transition: Mapping[str, Any], beat_map: Mapping[str, Any], at: float) -> float:
    """Length in seconds of a transition that starts at ``at``; a beat count follows the track's beats there."""
    if "seconds" in transition:
        return float(transition["seconds"])
    beats = beat_map["beats"]
    if len(beats) < 2:
        raise ValueError("transition beats need a track with beats")
    return beat_time(beats, beat_position(beats, at) + transition["beats"]) - at


def compile_show(document: Any, beat_map: Mapping[str, Any], fps: int, pixels: int = 156) -> tuple[dict[str, Any], ...]:
    """Frame schedule in the shape of ``compile_sequence``; the first entry also carries ``show``.

    The schedule spans ceil(track duration x fps) frames; each section runs from its start to the next one.
    """
    raw = validate_show(document, pixels)
    if raw["trackId"] != beat_map["id"]:
        raise ValueError("beat map does not belong to the show's track")
    duration = float(beat_map["durationSeconds"])
    total = max(1, math.ceil(duration * fps))
    starts = [start_seconds(section["start"], beat_map) for section in raw["sections"]]
    # 與前端 Math.round 相同（不用 Python 的銀行家捨入）。 / Same as the frontend's Math.round, not banker's rounding.
    frames = [_rounded_frames(seconds * fps) for seconds in starts]
    for index in range(1, len(frames)):
        if starts[index] >= duration or frames[index] >= total:
            raise ValueError(f"section {index + 1} starts at {starts[index]:.3f} s, past the track's end ({duration:.3f} s)")
        if frames[index] <= frames[index - 1]:
            raise ValueError(f"section {index + 1} must start at least one frame after section {index}")
    loop = raw["ending"]["kind"] == "loop"
    sections = raw["sections"]
    compiled: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        end = frames[index + 1] if index + 1 < len(frames) else total
        length = end - frames[index]
        transition = section["transition"]
        incoming = 0
        if index > 0 and transition["kind"] != "cut":
            incoming = min(length, max(0, _rounded_frames(transition_seconds(transition, beat_map, starts[index]) * fps)))
        following = sections[index + 1] if index + 1 < len(sections) else (sections[0] if loop else None)
        compiled.append({
            "id": section["id"], "preset": validate_preset(section["spinner"], pixels),
            **{group: validate_fixture_preset(section[group]) if group in section else None for group in FIXTURE_GROUPS},
            # 轉燈覆寫：燈台編號 → 該台改用的 preset。 / Overrides: unit number → the preset that unit plays.
            "overrides": {unit: preset for entry in section.get("overrides", [])
                          for preset in (validate_preset(entry["spinner"], pixels),) for unit in entry["units"]},
            "start": frames[index], "duration": length, "cycle": None, "clock": None,
            "group_cycles": {group: None for group in CLOCKS},
            "transition_frames": incoming,
            # 和串接一樣，記在前一段：離開這段時用哪種轉場。 / As in a sequence, stored on the outgoing step.
            "transition_kind": following["transition"]["kind"] if following is not None else "cut",
            "start_seconds": starts[index],
        })
    first = sections[0]["transition"]
    compiled[0]["ending"] = {"kind": "loop"} if loop else {"kind": "fade-black", "seconds": raw["ending"]["seconds"]}
    compiled[0]["end_fade_frames"] = 0 if loop else max(1, _rounded_frames(raw["ending"]["seconds"] * fps))
    compiled[0]["loop_transition_frames"] = (
        min(compiled[0]["duration"], max(0, _rounded_frames(transition_seconds(first, beat_map, 0.0) * fps)))
        if loop and first["kind"] != "cut" else 0
    )
    compiled[0]["show"] = {
        "id": raw["id"], "title": raw["title"], "trackId": raw["trackId"],
        "durationSeconds": duration, "totalFrames": total, "loop": loop,
    }
    return tuple(compiled)


def show_frame(compiled: tuple[dict[str, Any], ...], seconds: float, fps: int) -> int:
    """Schedule frame at a music time. A looping show restarts its frames with every round of the track, so
    rounding never accumulates; seconds keep counting past the end for the ending fade."""
    info = compiled[0]["show"]
    seconds = max(0.0, seconds)
    if not info["loop"]:
        return math.floor(seconds * fps)
    rounds = math.floor(seconds / info["durationSeconds"])
    within = seconds - rounds * info["durationSeconds"]
    return rounds * info["totalFrames"] + min(info["totalFrames"] - 1, math.floor(within * fps))


def show_finished(compiled: tuple[dict[str, Any], ...], frame: int) -> bool:
    info = compiled[0]["show"]
    return not info["loop"] and frame >= info["totalFrames"] + compiled[0]["end_fade_frames"]


class ShowStore:
    """Every show, saved together in one JSON file (``{schemaVersion, shows: [...]}``).

    A show whose track is gone (or whose positions no longer fit its beat map) stays listed with an error
    instead of stopping the service.
    """

    def __init__(self, path: str | Path, *, beat_maps: Mapping[str, Mapping[str, Any]], fps: int, pixels: int) -> None:
        self.path = Path(path)
        self.beat_maps = beat_maps
        self.fps = fps
        self.pixels = pixels
        self.lock = Lock()
        self.revision = 0
        self.shows: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "shows"} \
                    or raw["schemaVersion"] != SHOWS_FILE_SCHEMA_VERSION or not isinstance(raw["shows"], list):
                raise ValueError(f"{self.path.name} must be {{schemaVersion: 1, shows: [...]}}")
            for entry in raw["shows"]:
                show = validate_show(entry, pixels)
                if show["id"] in self.shows:
                    raise ValueError(f"{self.path.name}: duplicate show id {show['id']}")
                self.shows[show["id"]] = show

    def _compile(self, show: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        beat_map = self.beat_maps.get(show["trackId"])
        if beat_map is None:
            raise ValueError(f"找不到曲目 {show['trackId']} 的拍點檔")
        return compile_show(show, beat_map, self.fps, self.pixels)

    def _errors_unlocked(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        for show_id, show in self.shows.items():
            try:
                self._compile(show)
            except ValueError as failure:
                errors[show_id] = str(failure)
        return errors

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {"revision": self.revision, "shows": copy.deepcopy(list(self.shows.values())),
                    "errors": self._errors_unlocked()}

    def compiled(self, show_id: Any) -> tuple[dict[str, Any], ...]:
        with self.lock:
            show = self.shows.get(show_id) if isinstance(show_id, str) else None
            if show is None:
                raise ValueError("unknown showId")
            return self._compile(show)

    def save(self, show_id: str, raw: Any) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        """Validate, compile and save one show (the id in the path must match); returns (state, schedule)."""
        show = validate_show(raw, self.pixels)
        if show["id"] != show_id:
            raise ValueError("show id must match the URL")
        compiled = self._compile(show)
        with self.lock:
            if show_id not in self.shows and len(self.shows) >= MAX_SHOWS:
                raise ValueError(f"at most {MAX_SHOWS} shows")
            shows = {**self.shows, show_id: show}
            self._write(shows)
            self.shows = shows
            self.revision += 1
        return self.state(), compiled

    def delete(self, show_id: str) -> dict[str, Any]:
        with self.lock:
            if show_id not in self.shows:
                raise KeyError(show_id)
            shows = {key: value for key, value in self.shows.items() if key != show_id}
            self._write(shows)
            self.shows = shows
            self.revision += 1
        return self.state()

    def _write(self, shows: Mapping[str, Any]) -> None:
        document = {"schemaVersion": SHOWS_FILE_SCHEMA_VERSION, "shows": list(shows.values())}
        encoded = (json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".shows-", suffix=".tmp", dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


class _Active:
    def __init__(self, compiled: tuple[dict[str, Any], ...], start: float, started_at: float, audio: bool) -> None:
        self.compiled = compiled
        self.start = start
        self.started_at = started_at
        self.audio = audio
        # 最後一次讀到的音樂時間與當時時刻；音樂結束後由此推算淡出。 / Last music time read, to run the ending fade on.
        self.last: tuple[float, float] | None = None


class ShowPlayer:
    """Plays one show at a time: starts its track on the sound card and gives the output loop the frame.

    Without audio (no ``[audio]`` section or no PortAudio, e.g. a development machine) the show runs silently
    on the system clock, so it can still be previewed.
    """

    def __init__(self, store: ShowStore, audio: Any, fps: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.audio = audio
        self.fps = fps
        self.clock = clock
        self.lock = Lock()
        self.generation = 0
        self._active: _Active | None = None

    def play(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or "showId" not in raw or set(raw) - {"showId", "startSeconds"}:
            raise ValueError("show play request must be {showId, startSeconds?}")
        compiled = self.store.compiled(raw["showId"])
        info = compiled[0]["show"]
        start = float(_number(raw.get("startSeconds", 0), "startSeconds", 0, info["durationSeconds"]))
        with_audio = self.audio is not None and self.audio.available
        if with_audio:
            self.audio.play({"trackId": info["trackId"], "startSeconds": start, "loop": info["loop"]})
        with self.lock:
            self._active = _Active(compiled, start, self.clock(), with_audio)
            self.generation += 1
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            active, self._active = self._active, None
            if active is not None:
                self.generation += 1
        if active is not None and active.audio:
            self.audio.stop()
        return self.state()

    def replace(self, compiled: tuple[dict[str, Any], ...]) -> None:
        """A saved show replaces the playing one with the same id in place (same track and looping) or stops it."""
        info = compiled[0]["show"]
        with self.lock:
            active = self._active
            if active is None or active.compiled[0]["show"]["id"] != info["id"]:
                return
            current = active.compiled[0]["show"]
            if current["trackId"] == info["trackId"] and current["loop"] == info["loop"]:
                active.compiled = compiled
                self.generation += 1
                return
        self.stop()

    def _seconds(self, active: _Active, now: float) -> float:
        if not active.audio:
            return active.start + (now - active.started_at)
        current = self.audio.music_time()
        if current is not None and current[0]["id"] == active.compiled[0]["show"]["trackId"]:
            active.last = (current[1], now)
            return current[1]
        # 還沒出聲時停在起點；曲子結束後以最後的時間往後推，跑完結尾淡出。
        # Before the first sound, hold the start; after the track ends, extrapolate to run the ending fade.
        if active.last is None:
            return active.start
        return active.last[0] + (now - active.last[1])

    def current(self) -> tuple[tuple[dict[str, Any], ...], int, int] | None:
        """(schedule, frame, generation) for the output loop, or None when no show plays (standby output)."""
        with self.lock:
            active = self._active
            if active is None:
                return None
            frame = show_frame(active.compiled, self._seconds(active, self.clock()), self.fps)
            if not show_finished(active.compiled, frame):
                return active.compiled, frame, self.generation
            # 結尾淡出跑完：演出結束，回到待機輸出。 / Ending fade done: the show is over, back to standby output.
            self._active = None
            self.generation += 1
            return None

    def state(self) -> dict[str, Any]:
        with self.lock:
            active = self._active
            if active is None:
                return {"playing": None}
            info = active.compiled[0]["show"]
            seconds = self._seconds(active, self.clock())
            return {"playing": {
                "showId": info["id"], "trackId": info["trackId"], "silent": not active.audio,
                "positionSeconds": seconds % info["durationSeconds"] if info["loop"] else seconds,
                "durationSeconds": info["durationSeconds"], "loop": info["loop"],
            }}
