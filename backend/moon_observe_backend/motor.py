"""Decide when each spinner's motor start is sent: after its soft stop, one control box at a time.

The XIAO motor board (``firmware/sketches/motor_control_io``) only starts on a HIGH-to-LOW edge of D8 while it
is fully off (POWER_OFF). A start (Art-Net ch469 = 255 → WLED holds IO13 LOW) that arrives during the soft
stop (PWM ramps down 1 step per 120 ms, ≈ 10 s from duty 82, at most ≈ 11.1 s from the allowed maximum 90) is
ignored and never retried: the motor stays off while the page shows it running. Stops always work.

So a unit is sent 255 only after it has been sent 0 for ``restart_delay`` seconds. A gap in the output of
``link_timeout`` or more (the WLED usermod's Art-Net timeout, 1 s by default) counts as a stop of every unit,
because WLED has released IO13 by then.

Starts are also spread out: at most one start group (a control box) is released per ``start_interval``, the
earliest-listed group first, so the relays and capacitor charging of many units do not hit the power supply
at the same moment. Units in no group are each their own group after the listed ones; without groups every
unit is in one group. Stops are never delayed.

XIAO 只在完全停止時、D8 由 HIGH 變 LOW 那一下才會啟動；緩停中（約 10 秒）收到的啟動會被忽略且不再補。
所以每台在送 0 滿 ``restart_delay`` 秒後才送 255；輸出中斷超過 WLED 的逾時（1 秒）也算全部停止。
啟動另外分區：每 ``start_interval`` 秒最多放行一組（一個控箱），依設定的順序；停止不延後。
"""

from __future__ import annotations

from threading import Lock
import time
from typing import Callable, Sequence

DEFAULT_RESTART_DELAY_SECONDS = 12.0
DEFAULT_START_INTERVAL_SECONDS = 1.0
WLED_TIMEOUT_SECONDS = 1.0


class MotorGate:
    def __init__(self, restart_delay: float = DEFAULT_RESTART_DELAY_SECONDS, *,
                 start_interval: float = 0.0, start_groups: Sequence[Sequence[int]] = (),
                 link_timeout: float = WLED_TIMEOUT_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.restart_delay = restart_delay
        self.start_interval = start_interval
        self.start_groups = [list(group) for group in start_groups]
        # 分區啟動（依控箱、每 start_interval 一組）或全部一起；可在執行中切換（set_staggered）。停止後的等待不受影響。
        # Staggered starts (one control box per start_interval) or all together; switchable at run time. The wait
        # after a stop applies either way.
        self.staggered = True
        self.link_timeout = link_timeout
        self.clock = clock
        self.lock = Lock()
        self._last_call: float | None = None  # None: the output loop has not run yet.
        self._sent: list[int] = []
        self._off_since: list[float] = []
        self._next_slot = 0.0  # The next start group may be released from this time.

    def _rank(self, index: int, count: int) -> int:
        """Start order of unit ``index + 1``: its group's position; lower starts first."""
        if not self.start_groups or not self.staggered:
            return 0
        for position, group in enumerate(self.start_groups):
            if index + 1 in group:
                return position
        return len(self.start_groups) + index

    def apply(self, desired: Sequence[int]) -> tuple[int, ...]:
        """Motor values to send this frame (index = unit - 1); called by the output loop every frame."""
        now = self.clock()
        count = len(desired)
        with self.lock:
            if self._last_call is None or now - self._last_call >= self.link_timeout or len(self._sent) != count:
                # 第一格，或輸出中斷到 WLED 逾時：每台都當作剛開始緩停。
                # First frame, or a gap long enough for WLED to time out: every unit counts as just stopping.
                self._sent = [0] * count
                self._off_since = [now] * count
                self._next_slot = now
            self._last_call = now
            for index, want in enumerate(desired):
                if want != 255 and self._sent[index] == 255:
                    self._sent[index] = 0
                    self._off_since[index] = now
            ready = [index for index, want in enumerate(desired)
                     if want == 255 and self._sent[index] == 0 and now - self._off_since[index] >= self.restart_delay]
            if ready and now >= self._next_slot:
                first = min(self._rank(index, count) for index in ready)
                for index in ready:
                    if self._rank(index, count) == first:
                        self._sent[index] = 255
                self._next_slot = now + (self.start_interval if self.staggered else 0.0)
            return tuple(self._sent)

    def set_staggered(self, staggered: bool) -> None:
        with self.lock:
            self.staggered = staggered
            if not staggered:
                # 排隊中的不必再等下一個時段。 / Held starts need not wait for the next slot.
                self._next_slot = 0.0

    def is_staggered(self) -> bool:
        with self.lock:
            return self.staggered

    def can_stagger(self) -> bool:
        """Whether staggering changes anything (groups and an interval are configured)."""
        return bool(self.start_groups) and self.start_interval > 0

    def waiting(self, desired: Sequence[bool]) -> list[float | None]:
        """Seconds until each wanted-but-held unit gets its start; None = not held (or no output loop).

        Plays the release rule forward from now, assuming nothing else changes.
        """
        now = self.clock()
        count = len(desired)
        with self.lock:
            if self._last_call is None:
                return [None] * count
            paused = now - self._last_call >= self.link_timeout or len(self._sent) != count
            if paused:
                # 輸出恢復時會重新計時。 / The output restarts the clock when it resumes.
                ready_at = {index: now + self.restart_delay for index in range(count) if desired[index]}
                slot = now
            else:
                ready_at = {index: self._off_since[index] + self.restart_delay
                            for index in range(count) if desired[index] and self._sent[index] == 0}
                slot = max(now, self._next_slot)
        result: list[float | None] = [None] * count
        while ready_at:
            moment = max(slot, min(ready_at.values()))
            candidates = [index for index, at in ready_at.items() if at <= moment]
            first = min(self._rank(index, count) for index in candidates)
            for index in candidates:
                if self._rank(index, count) == first:
                    remaining = round(moment - now, 1)
                    result[index] = remaining if remaining > 0 else None
                    del ready_at[index]
            slot = moment + (self.start_interval if self.staggered else 0.0)
        return result
