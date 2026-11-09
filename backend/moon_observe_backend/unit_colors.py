"""Manual per-unit solid colour test; overrides the rendered output while active, memory only."""

from __future__ import annotations

import math
from threading import Lock
import time
from typing import Any, Callable

from .effects import Rgb, _rgb

MAX_SECONDS = 86400


class UnitColorStore:
    def __init__(self, unit_count: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.unit_count = unit_count
        self.clock = clock
        self.lock = Lock()
        # 每台一個 Rgb 或 None（照常播放）；None 表示沒有測試。
        # One Rgb or None (plays normally) per unit; None when no test is active.
        self.colors: tuple[Rgb | None, ...] | None = None
        self.expires: float | None = None  # None: until ended manually.

    def _expire_unlocked(self) -> None:
        if self.colors is not None and self.expires is not None and self.clock() >= self.expires:
            self.colors = None
            self.expires = None

    def current(self) -> tuple[Rgb | None, ...] | None:
        """Called by the output loop every frame."""
        with self.lock:
            self._expire_unlocked()
            return self.colors

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._expire_unlocked()
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        if self.colors is None:
            return {"active": False, "colors": None, "remainingSeconds": None}
        return {
            "active": True,
            "colors": [None if color is None else dict(zip("rgb", color)) for color in self.colors],
            "remainingSeconds": None if self.expires is None else max(0.0, self.expires - self.clock()),
        }

    def apply(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != {"colors", "seconds"}:
            raise ValueError("test must contain colors and seconds")
        colors, seconds = raw["colors"], raw["seconds"]
        if not isinstance(colors, list) or len(colors) != self.unit_count:
            raise ValueError(f"colors must list {self.unit_count} units")
        parsed = tuple(None if color is None else _rgb(color, f"colors[{index}]") for index, color in enumerate(colors))
        if all(color is None for color in parsed):
            raise ValueError("at least one unit needs a color")
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds)
                                    or not 1 <= seconds <= MAX_SECONDS):
            raise ValueError(f"seconds must be null or a number in 1..{MAX_SECONDS}")
        with self.lock:
            self.colors = parsed
            self.expires = None if seconds is None else self.clock() + seconds
            return self._snapshot_unlocked()

    def clear(self) -> dict[str, Any]:
        with self.lock:
            self.colors = None
            self.expires = None
            return self._snapshot_unlocked()


class FixtureColorStore:
    """Manual solid colours for ball / wash fixtures, per group (on-site checks and finding a fixture).

    ``PUT /api/test/fixtures`` body: {"group": "ball"|"wash", "colors": [{r,g,b}|null × count], "seconds": n|null};
    null plays normally. Each group expires on its own; memory only.
    球燈／洗牆燈單色測試（現場對燈）；各群組各自計時，只在記憶體。
    """

    def __init__(self, counts: dict[str, int], *, clock: Callable[[], float] = time.monotonic) -> None:
        self.groups = {group: UnitColorStore(count, clock=clock) for group, count in counts.items() if count > 0}

    def current(self) -> dict[str, tuple[Rgb | None, ...]]:
        """Active tests by group (called by the output loop every frame)."""
        result = {}
        for group, store in self.groups.items():
            colors = store.current()
            if colors is not None:
                result[group] = colors
        return result

    def snapshot(self) -> dict[str, Any]:
        return {group: store.snapshot() for group, store in self.groups.items()}

    def apply(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != {"group", "colors", "seconds"}:
            raise ValueError("fixture test must contain group, colors and seconds")
        if raw["group"] not in self.groups:
            raise ValueError(f"group must be one of {', '.join(self.groups) or '(no fixtures configured)'}")
        try:
            self.groups[raw["group"]].apply({"colors": raw["colors"], "seconds": raw["seconds"]})
        except ValueError as error:
            raise ValueError(str(error).replace("units", "fixtures").replace("unit ", "fixture ")) from None
        return self.snapshot()

    def clear(self) -> dict[str, Any]:
        for store in self.groups.values():
            store.clear()
        return self.snapshot()
