"""Daily schedule (M13): opening hours, standby effect, show times.

By the host's local time (the Pi must be on NTP, time zone Asia/Taipei) the installation is in one of three phases:

- ``off`` (outside the opening hours): every motor stops, the chosen light groups go dark (blackout), a show stops.
- ``standby`` (inside the hours): the standby effect (wind-flow, ``wind.WindStandby``) plays and, with
  ``startMotors``, every motor runs.
- ``show`` (inside the hours, from a show time until the show ends): the show for that date plays
  ``showRepeats`` times in a row, then back to standby. Show times are ``showMinutes`` of every hour (0 and 30 =
  on the hour and at half past) inside the show period ``showHours`` (both ends included, e.g. 17:30-20:30 =
  17:30, 18:00 ... 20:30), plus the listed ``showTimes``. A show time at the closing time still plays, and closing
  waits for a show the schedule started to finish.

After the backend starts with the schedule already enabled (a reboot, a service restart) the schedule does not
start motors until someone confirms once (``confirm``, the button on the page); lights and shows run as usual
and closing still stops the motors. Enabling the schedule by hand counts as that confirmation.

The scheduler acts when the phase changes (and once when it is enabled or the backend starts), so manual changes
in between are kept until the next change. A show started by hand inside the hours also returns to standby when
it ends. Disabled, it touches nothing (and gives back the lights it turned off).

依主機本地時間分三種狀態：營業時間外（馬達全停、選擇的燈組關閉、演出停止）、待機（播待機效果，馬達轉動）、
演出（表演時間到演出結束，之後回到待機；每次連播 showRepeats 輪）。表演時間 = 表演時段 showHours 內（含頭尾）每小時的 showMinutes 分（0、30 = 整點與半點），加上 showTimes。
剛好在關閉時間的那一場照播；排程開始的演出還沒播完時，等它播完才關閉。只在狀態改變時動作，中間的手動操作會保留到下次改變。
後端啟動時排程已經啟用（重開機、重啟服務）：排程不會啟動馬達，要有人確認一次（confirm）；燈光與演出照常，關閉時間仍會停馬達。
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Mapping, Sequence

SCHEDULE_SCHEMA_VERSION = 1
GROUPS = ("spinner", "ball", "wash")
MAX_SHOW_TIMES = 96
MAX_DATE_SHOWS = 32
MAX_SHOW_REPEATS = 50
TICK_SECONDS = 0.5
# 表演時間過了這麼久才發現（時鐘跳動、服務剛啟動）就不補播。 / A show time noticed later than this is skipped.
SHOW_GRACE_SECONDS = 60

DEFAULT_SETTINGS: dict[str, Any] = {
    "schemaVersion": SCHEDULE_SCHEMA_VERSION,
    # 預設停用：升級後不會自己啟動馬達。 / Off by default: an upgrade never starts motors by itself.
    "enabled": False,
    "hours": {"start": "10:00", "end": "20:30"},
    "offGroups": list(GROUPS),
    "startMotors": True,
    # 表演時段（含頭尾）：這段時間內每小時的 showMinutes 分開始表演。 / Show period (both ends included).
    "showHours": {"start": "17:30", "end": "20:30"},
    # 表演時段內每小時的第幾分開始表演：整點與 30 分。 / Minutes of every hour inside the show period: hour and half past.
    "showMinutes": [0, 30],
    # 另外指定的時間。 / Extra times.
    "showTimes": [],
    # 每次表演連播幾輪。 / How many times the show plays in a row.
    "showRepeats": 6,
    "showId": None,
    "dateShows": [],
}


def _minutes(value: Any, label: str) -> int:
    """``"HH:MM"`` (00:00..23:59) as minutes of the day."""
    if (not isinstance(value, str) or len(value) != 5 or value[2] != ":"
            or not (value[:2] + value[3:]).isascii() or not (value[:2] + value[3:]).isdigit()):
        raise ValueError(f"{label} must be \"HH:MM\"")
    hour, minute = int(value[:2]), int(value[3:])
    if hour > 23 or minute > 59:
        raise ValueError(f"{label} must be 00:00..23:59")
    return hour * 60 + minute


def _month_day(value: Any, label: str) -> tuple[int, int]:
    """``"MM-DD"`` as (month, day); 02-29 is allowed."""
    if (not isinstance(value, str) or len(value) != 5 or value[2] != "-"
            or not (value[:2] + value[3:]).isascii() or not (value[:2] + value[3:]).isdigit()):
        raise ValueError(f"{label} must be \"MM-DD\"")
    month, day = int(value[:2]), int(value[3:])
    try:
        date(2000, month, day)
    except ValueError:
        raise ValueError(f"{label} is not a date") from None
    return month, day


def _show_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError(f"{label} must be a show id")
    return value


def validate_settings(raw: Any) -> dict[str, Any]:
    """Normalized schedule settings; unknown fields are refused."""
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_SETTINGS):
        raise ValueError("schedule must be {schemaVersion, enabled, hours, offGroups, startMotors, showHours, showMinutes, "
                         "showTimes, showRepeats, showId, dateShows}")
    if raw["schemaVersion"] != SCHEDULE_SCHEMA_VERSION:
        raise ValueError("unsupported schedule schemaVersion")
    for name in ("enabled", "startMotors"):
        if type(raw[name]) is not bool:
            raise ValueError(f"{name} must be true or false")
    hours = raw["hours"]
    if not isinstance(hours, dict) or set(hours) != {"start", "end"}:
        raise ValueError("hours must be {start, end}")
    if _minutes(hours["start"], "hours.start") == _minutes(hours["end"], "hours.end"):
        raise ValueError("hours.start and hours.end must differ")
    groups = raw["offGroups"]
    if not isinstance(groups, list) or len(set(groups)) != len(groups) or any(group not in GROUPS for group in groups):
        raise ValueError("offGroups must list spinner, ball and/or wash")
    times = raw["showTimes"]
    if not isinstance(times, list) or len(times) > MAX_SHOW_TIMES:
        raise ValueError(f"showTimes must list at most {MAX_SHOW_TIMES} times")
    minutes = [_minutes(value, "showTimes") for value in times]
    if len(set(minutes)) != len(minutes):
        raise ValueError("showTimes must not repeat")
    show_hours = raw["showHours"]
    if not isinstance(show_hours, dict) or set(show_hours) != {"start", "end"}:
        raise ValueError("showHours must be {start, end}")
    _minutes(show_hours["start"], "showHours.start")
    _minutes(show_hours["end"], "showHours.end")
    every = raw["showMinutes"]
    if (not isinstance(every, list) or len(set(every)) != len(every)
            or any(type(minute) is not int or not 0 <= minute <= 59 for minute in every)):
        raise ValueError("showMinutes must list minutes 0..59 without repeats")
    repeats = raw["showRepeats"]
    if type(repeats) is not int or not 1 <= repeats <= MAX_SHOW_REPEATS:
        raise ValueError(f"showRepeats must be an integer 1..{MAX_SHOW_REPEATS}")
    rules = raw["dateShows"]
    if not isinstance(rules, list) or len(rules) > MAX_DATE_SHOWS:
        raise ValueError(f"dateShows must list at most {MAX_DATE_SHOWS} rules")
    date_shows = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"from", "to", "showId"}:
            raise ValueError("each dateShows rule must be {from, to, showId}")
        _month_day(rule["from"], "dateShows.from")
        _month_day(rule["to"], "dateShows.to")
        date_shows.append({"from": rule["from"], "to": rule["to"], "showId": _show_id(rule["showId"], "dateShows.showId")})
    return {
        "schemaVersion": SCHEDULE_SCHEMA_VERSION,
        "enabled": raw["enabled"],
        "hours": {"start": hours["start"], "end": hours["end"]},
        "offGroups": [group for group in GROUPS if group in groups],
        "startMotors": raw["startMotors"],
        "showHours": {"start": show_hours["start"], "end": show_hours["end"]},
        "showMinutes": sorted(every),
        "showTimes": sorted(times),
        "showRepeats": repeats,
        "showId": None if raw["showId"] is None else _show_id(raw["showId"], "showId"),
        "dateShows": date_shows,
    }


def in_hours(settings: Mapping[str, Any], moment: datetime) -> bool:
    """Start inclusive, end exclusive; an end before the start runs past midnight."""
    start = _minutes(settings["hours"]["start"], "hours.start")
    end = _minutes(settings["hours"]["end"], "hours.end")
    minute = moment.hour * 60 + moment.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def show_for(settings: Mapping[str, Any], day: date) -> str | None:
    """The show of a date: the first matching ``dateShows`` rule (both ends included; may wrap the new year),
    else the default ``showId``."""
    key = (day.month, day.day)
    for rule in settings["dateShows"]:
        first, last = _month_day(rule["from"], "from"), _month_day(rule["to"], "to")
        if first <= key <= last if first <= last else key >= first or key <= last:
            return rule["showId"]
    return settings["showId"]


def _show_moments(settings: Mapping[str, Any], day: date) -> list[datetime]:
    """That day's show times: ``showMinutes`` of every hour inside the show period (both ends included) plus
    ``showTimes``, kept when inside the opening hours or exactly at the closing time."""
    first = _minutes(settings["showHours"]["start"], "showHours.start")
    last = _minutes(settings["showHours"]["end"], "showHours.end")
    closing = _minutes(settings["hours"]["end"], "hours.end")
    minutes = {_minutes(value, "showTimes") for value in settings["showTimes"]}
    minutes.update(value for value in (hour * 60 + minute for hour in range(24) for minute in settings["showMinutes"])
                   if (first <= value <= last if first <= last else value >= first or value <= last))
    moments = [datetime(day.year, day.month, day.day, value // 60, value % 60) for value in sorted(minutes)]
    return [moment for moment, value in zip(moments, sorted(minutes)) if value == closing or in_hours(settings, moment)]


def _log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class Scheduler:
    """Applies the schedule through callbacks; ``tick`` runs twice a second in its own thread (``start``).

    ``show_state`` returns ``ShowPlayer.state()`` (after letting a finished show end); ``play_show(show_id, rounds)``
    plays the show that many times and raises ValueError for an unknown or broken show. ``now`` is the local wall clock (naive datetime).
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        set_motors: Callable[[bool], Any],
        set_blackout: Callable[[Sequence[str], bool], Any],
        set_standby: Callable[[bool], Any],
        play_show: Callable[[str, int], Any],
        stop_show: Callable[[], Any],
        show_state: Callable[[], Mapping[str, Any]],
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = _log,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.set_motors = set_motors
        self.set_blackout = set_blackout
        self.set_standby = set_standby
        self.play_show = play_show
        self.stop_show = stop_show
        self.show_state = show_state
        self.now = now
        self.clock = clock
        self.log = log
        self.lock = RLock()
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.warning: str | None = None
        if self.path is not None and self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                # 舊檔沒有的欄位用預設值。 / Fields an older file lacks take their defaults.
                self.settings = validate_settings({**DEFAULT_SETTINGS, **saved} if isinstance(saved, dict) else saved)
            except (OSError, ValueError) as error:
                # 檔案壞掉時停用排程，不擋服務啟動。 / A broken file disables the schedule; the service still starts.
                self.warning = f"schedule ignored ({self.path.name}): {error}"
        # 排程可以啟動馬達：後端啟動時排程已啟用就要先由人確認一次；手動啟用排程也算確認。
        # Whether the schedule may start motors: needs one confirmation when the backend starts with it enabled.
        self._confirmed = not self.settings["enabled"]
        # None = 停用或還沒套用；"off"／"standby"／"show"。 / None: disabled or not applied yet.
        self._phase: str | None = None
        # 排程關掉的燈組（離開 off 或停用時恢復）。 / Groups this schedule turned off (given back on leaving "off").
        self._dark: tuple[str, ...] = ()
        # 排程開始、還在播的演出（關閉時間會等它播完）。 / The show this schedule started and is still playing.
        self._scheduled: str | None = None
        self._last: datetime | None = None
        # 最近一次表演時間沒播成的原因。 / Why the latest show time did not play.
        self._error: str | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    # -- phase changes ---------------------------------------------------------------------------------------

    def _release_dark(self) -> None:
        if self._dark:
            self.set_blackout(self._dark, False)
            self._dark = ()

    def _close(self) -> None:
        self.stop_show()
        self._scheduled = None
        self.set_motors(False)
        # 沒被關掉的燈組在營業時間外播待機效果。 / Groups left on show the standby effect outside the hours.
        self.set_standby(True)
        groups = tuple(self.settings["offGroups"])
        if groups:
            self.set_blackout(groups, True)
        self._dark = groups
        self._phase = "off"
        self.log(f"schedule: closed (motors stopped, off: {', '.join(groups) or 'none'})")

    def _open(self) -> None:
        self._release_dark()
        motors = "untouched"
        if self.settings["startMotors"]:
            motors = "started" if self._confirmed else "waiting for confirmation"
            if self._confirmed:
                self.set_motors(True)
        self.log(f"schedule: open (motors {motors})")

    def _due(self, last: datetime | None, now: datetime) -> bool:
        """Whether a show time was passed since the previous tick (and is still fresh)."""
        if last is None or last >= now:
            return False
        return any(last < moment <= now and (now - moment).total_seconds() <= SHOW_GRACE_SECONDS
                   for moment in _show_moments(self.settings, now.date()))

    def _start_show(self, now: datetime) -> bool:
        show_id = show_for(self.settings, now.date())
        if show_id is None:
            # 沒有選演出：不播，不算錯誤。 / No show chosen: nothing plays; not an error.
            return False
        try:
            self.play_show(show_id, self.settings["showRepeats"])
        except ValueError as error:
            self._error = f"{now:%H:%M} 演出 {show_id} 無法播放 / cannot play: {error}"
            self.log(f"schedule: {self._error}")
            return False
        self.set_standby(False)
        self._scheduled = show_id
        self._error = None
        self.log(f"schedule: show {show_id} started ({self.settings['showRepeats']} round(s))")
        return True

    def tick(self) -> None:
        with self.lock:
            now = self.now()
            last, self._last = self._last, now
            if not self.settings["enabled"]:
                # 之後手動啟用就是確認。 / Enabling by hand later is the confirmation.
                self._confirmed = True
                if self._phase is not None:
                    self._release_dark()
                    self._phase = None
                    self._scheduled = None
                    self.log("schedule: disabled")
                return
            state = self.show_state()
            playing = state.get("playing")
            if self._scheduled is not None and (playing is None or playing["showId"] != self._scheduled):
                self._scheduled = None
            if not in_hours(self.settings, now):
                if self._phase in ("standby", "show"):
                    # 剛好在關閉時間的那一場照播；排程開始的演出播完才關閉。
                    # A show time at the closing time still plays; a show the schedule started finishes first.
                    if self._due(last, now) and self._start_show(now):
                        self._phase = "show"
                    if self._scheduled is not None:
                        return
                if self._phase != "off":
                    self._close()
                return
            if self._phase in (None, "off"):
                self._open()
                self._phase = "open"
            busy = playing is not None or "starting" in state
            if self._due(last, now) and self._start_show(now):
                busy = True
            if busy:
                self._phase = "show"
            elif self._phase != "standby":
                # 開門、或演出（排程或手動開始的）結束：回到待機效果。 / Opening, or a show ended: back to standby.
                self.set_standby(True)
                self._phase = "standby"

    # -- settings and state ----------------------------------------------------------------------------------

    def _save(self, settings: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        encoded = (json.dumps(settings, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".schedule-", suffix=".tmp", dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def apply(self, raw: Any) -> dict[str, Any]:
        """Validate, save and apply new settings at once."""
        settings = validate_settings(raw)
        with self.lock:
            self._save(settings)
            self.settings = settings
            self.warning = None
            self._error = None
            if self._phase == "off":
                # 重新套用（關閉的燈組可能改了）。 / Apply "off" again: the groups may have changed.
                self._release_dark()
                self._phase = None
            self.tick()
            return self.state()

    def confirm(self, raw: Any) -> dict[str, Any]:
        """``{"confirmed": true}``: after a backend start, lets the schedule start motors (once per start). Inside
        the hours the motors start now."""
        if not isinstance(raw, dict) or set(raw) != {"confirmed"} or raw["confirmed"] is not True:
            raise ValueError("schedule confirmation must be {\"confirmed\": true}")
        with self.lock:
            if not self._confirmed:
                self._confirmed = True
                self.log("schedule: motor start confirmed")
                if self.settings["enabled"] and self.settings["startMotors"] and self._phase in ("standby", "show"):
                    self.set_motors(True)
            return self.state()

    def _next_show(self, now: datetime) -> dict[str, Any] | None:
        for day in (now.date(), now.date() + timedelta(days=1)):
            later = [moment for moment in _show_moments(self.settings, day) if moment > now]
            if later:
                moment = min(later)
                return {"at": moment.isoformat(timespec="minutes"), "showId": show_for(self.settings, moment.date())}
        return None

    def state(self) -> dict[str, Any]:
        with self.lock:
            now = self.now()
            return {
                "settings": copy.deepcopy(self.settings),
                "status": {
                    # disabled／off／standby／show
                    "phase": (self._phase or "disabled") if self.settings["enabled"] else "disabled",
                    "serverTime": now.isoformat(timespec="seconds"),
                    "inHours": in_hours(self.settings, now),
                    "todayShowId": show_for(self.settings, now.date()),
                    "nextShow": self._next_show(now) if self.settings["enabled"] else None,
                    # false：後端重啟後還沒確認，排程不會啟動馬達。 / false: not confirmed since the backend started.
                    "motorsConfirmed": self._confirmed,
                    "error": self._error,
                    "warning": self.warning,
                },
            }

    # -- thread ----------------------------------------------------------------------------------------------

    def start(self) -> None:
        def run() -> None:
            while True:
                try:
                    self.tick()
                except Exception as error:  # 排程不能因為一次錯誤就停掉。 / One failure must not end the schedule.
                    self.log(f"schedule: error: {error!r}")
                if self._stop.wait(TICK_SECONDS):
                    return
        self._stop.clear()
        self._thread = Thread(target=run, name="schedule", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
