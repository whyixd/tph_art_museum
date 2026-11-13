from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from moon_observe_backend.audio import CLICK_TRACK_ID
from moon_observe_backend.control import PresetStore, make_server
from moon_observe_backend.effects import default_preset
from moon_observe_backend.schedule import DEFAULT_SETTINGS, Scheduler, in_hours, show_for, validate_settings
from moon_observe_backend.show import ShowPlayer, ShowStore
from moon_observe_backend.wind import WindStandby

from test_show import CLICK, FakeAudio, show


def settings(**overrides):
    document = {**DEFAULT_SETTINGS, "enabled": True, "showMinutes": [], "showTimes": ["11:00", "15:00"], "showId": "demo"}
    document.update(overrides)
    return document


class World:
    """The scheduler's callbacks, recorded; the test moves the wall clock and ends shows."""

    def __init__(self, at="09:00", raw=None, path=None):
        self.time = datetime(2026, 11, 14, int(at[:2]), int(at[3:]))
        self.monotonic = 0.0
        self.events = []
        self.playing = None
        self.starting = False
        self.known = {"demo": False, "xmas": False, "looping": True}  # showId -> loop
        self.scheduler = Scheduler(
            path,
            set_motors=lambda enabled: self.events.append(("motors", enabled)),
            set_blackout=lambda groups, enabled: self.events.append(("blackout", tuple(groups), enabled)),
            set_standby=lambda enabled: self.events.append(("standby", enabled)),
            play_show=self.play, stop_show=self.stop, show_state=self.show_state,
            now=lambda: self.time, clock=lambda: self.monotonic, log=lambda line: None)
        if raw is not None:
            # 像手動啟用過的排程（已確認）。 / As a schedule enabled by hand (confirmed).
            self.scheduler.settings = validate_settings(raw)

    def play(self, show_id, rounds):
        if show_id not in self.known:
            raise ValueError("unknown showId")
        self.playing = {"showId": show_id, "loop": self.known[show_id], "durationSeconds": 60.0}
        self.rounds = rounds
        self.events.append(("play", show_id))

    def stop(self):
        self.playing = None
        self.events.append(("stop",))

    def show_state(self):
        return {"playing": self.playing, **({"starting": {}} if self.starting else {})}

    def advance(self, seconds):
        """Tick every half second up to ``seconds`` later; returns the events of that span."""
        del self.events[:]
        for _ in range(int(seconds * 2)):
            self.time += timedelta(seconds=0.5)
            self.monotonic += 0.5
            self.scheduler.tick()
        return list(self.events)

    def go(self, at):
        """Jump (without ticking) to just before ``at``, then tick across it."""
        self.time = self.time.replace(hour=int(at[:2]), minute=int(at[3:]), second=0) - timedelta(seconds=1)
        self.scheduler.tick()
        return self.advance(2)

    def phase(self):
        return self.scheduler.state()["status"]["phase"]


class SettingsTests(unittest.TestCase):
    def test_validation(self):
        valid = validate_settings(settings(showTimes=["15:00", "11:00"], offGroups=["wash", "spinner"]))
        self.assertEqual(["11:00", "15:00"], valid["showTimes"])
        self.assertEqual(["spinner", "wash"], valid["offGroups"])
        for change in (
            {"enabled": 1}, {"hours": {"start": "10:00", "end": "10:00"}}, {"hours": {"start": "24:00", "end": "10:00"}},
            {"hours": {"start": "9:00", "end": "20:30"}}, {"offGroups": ["spinner", "spinner"]}, {"offGroups": ["lamp"]},
            {"showTimes": ["11:00", "11:00"]}, {"showTimes": ["11.00"]}, {"showId": ""}, {"schemaVersion": 2},
            {"dateShows": [{"from": "02-30", "to": "03-01", "showId": "demo"}]},
            {"dateShows": [{"from": "12-01", "to": "12-25"}]}, {"extra": True},
            {"showHours": {"start": "17:30"}}, {"showHours": {"start": "17:30", "end": "25:00"}},
            {"showMinutes": [60]}, {"showMinutes": [0, 0]}, {"showMinutes": [0.5]}, {"showRepeats": 0}, {"showRepeats": 51},
            {"showRepeats": 2.0},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_settings({**settings(), **change})
        with self.assertRaises(ValueError):
            validate_settings({key: value for key, value in settings().items() if key != "startMotors"})

    def test_hours_and_date_shows(self):
        day = settings()
        self.assertEqual([False, True, True, False],
                         [in_hours(day, datetime(2026, 1, 1, hour, minute)) for hour, minute in ((9, 59), (10, 0), (20, 29), (20, 30))])
        night = settings(hours={"start": "18:00", "end": "02:00"})
        self.assertEqual([True, True, False, False],
                         [in_hours(night, datetime(2026, 1, 1, hour, 0)) for hour in (18, 1, 2, 12)])
        rules = settings(dateShows=[{"from": "12-01", "to": "12-25", "showId": "xmas"},
                                    {"from": "12-28", "to": "01-03", "showId": "newyear"}])
        self.assertEqual(["demo", "xmas", "xmas", "demo", "newyear", "newyear", "demo"],
                         [show_for(rules, date(2026, month, day_)) for month, day_ in
                          ((11, 30), (12, 1), (12, 25), (12, 26), (12, 31), (1, 3), (1, 4))])


class SchedulerTests(unittest.TestCase):
    def test_disabled_does_nothing(self):
        world = World(raw=settings(enabled=False))
        self.assertEqual([], world.advance(5))
        self.assertEqual("disabled", world.phase())

    def test_a_day(self):
        world = World("09:00", settings())
        # 啟動時在營業時間外：馬達停、燈關。 / Started outside the hours: motors stop, lights off.
        self.assertEqual([("stop",), ("motors", False), ("standby", True), ("blackout", ("spinner", "ball", "wash"), True)],
                         world.advance(1))
        self.assertEqual("off", world.phase())
        self.assertEqual([], world.advance(60))
        # 10:00 開始：恢復燈光、啟動馬達、播待機效果。 / 10:00: lights back, motors start, standby effect.
        self.assertEqual([("blackout", ("spinner", "ball", "wash"), False), ("motors", True), ("standby", True)],
                         world.go("10:00"))
        self.assertEqual("standby", world.phase())
        self.assertEqual({"at": "2026-11-14T11:00", "showId": "demo"}, world.scheduler.state()["status"]["nextShow"])
        # 11:00 表演：播演出、離開待機。 / 11:00: the show plays and standby ends.
        self.assertEqual([("play", "demo"), ("standby", False)], world.go("11:00"))
        self.assertEqual("show", world.phase())
        self.assertEqual([], world.advance(30))
        # 演出結束：回到待機。 / The show ends: back to standby.
        world.playing = None
        self.assertEqual([("standby", True)], world.advance(1))
        self.assertEqual("standby", world.phase())
        # 20:30 結束：演出停、馬達停、燈關。 / 20:30: show and motors stop, lights off.
        self.assertEqual([("stop",), ("motors", False), ("standby", True), ("blackout", ("spinner", "ball", "wash"), True)],
                         world.go("20:30"))
        self.assertEqual("off", world.phase())
        self.assertEqual({"at": "2026-11-15T11:00", "showId": "demo"}, world.scheduler.state()["status"]["nextShow"])

    def test_closing_waits_for_a_scheduled_show_but_stops_one_started_by_hand(self):
        world = World("12:00", settings(showTimes=["20:29"]))
        world.advance(1)
        self.assertEqual([("play", "demo"), ("standby", False)], world.go("20:29"))
        # 排程開始的演出：關閉時間到了先讓它播完。 / A scheduled show finishes before closing.
        self.assertEqual([], world.go("20:30"))
        self.assertEqual([], world.advance(120))
        self.assertEqual("show", world.phase())
        world.playing = None
        self.assertEqual([("stop",), ("motors", False), ("standby", True), ("blackout", ("spinner", "ball", "wash"), True)],
                         world.advance(1))
        self.assertEqual("off", world.phase())
        # 手動開始的演出：關閉時間一到就停。 / A show started by hand stops at closing.
        manual = World("12:00", settings(showTimes=[]))
        manual.advance(1)
        manual.playing = {"showId": "demo", "loop": True, "durationSeconds": 60.0}
        events = manual.go("20:30")
        self.assertEqual(("stop",), events[0])
        self.assertIn(("motors", False), events)
        self.assertIsNone(manual.playing)

    def test_starting_inside_the_hours_does_not_replay_a_past_show(self):
        world = World("11:00", settings())
        world.time += timedelta(seconds=20)
        self.assertEqual([("motors", True), ("standby", True)], world.advance(5))
        self.assertEqual("standby", world.phase())

    def test_a_late_show_time_is_skipped(self):
        world = World("10:30", settings())
        world.advance(1)
        # 時鐘往前跳過表演時間 5 分鐘。 / The clock jumps 5 minutes past the show time.
        world.time = world.time.replace(hour=11, minute=5)
        self.assertEqual([], world.advance(2))

    def test_show_times_outside_the_hours_are_ignored(self):
        world = World("08:00", settings(showTimes=["09:00", "20:31"]))
        world.advance(1)
        self.assertEqual([], world.go("09:00"))
        self.assertIsNone(world.scheduler.state()["status"]["nextShow"])

    def test_selected_groups_and_motor_option(self):
        world = World("09:00", settings(offGroups=["spinner"], startMotors=False))
        self.assertEqual([("stop",), ("motors", False), ("standby", True), ("blackout", ("spinner",), True)], world.advance(1))
        self.assertEqual([("blackout", ("spinner",), False), ("standby", True)], world.go("10:00"))
        none = World("09:00", settings(offGroups=[]))
        self.assertEqual([("stop",), ("motors", False), ("standby", True)], none.advance(1))
        self.assertEqual([("motors", True), ("standby", True)], none.go("10:00"))

    def test_date_rule_picks_the_show(self):
        world = World("10:30", settings(dateShows=[{"from": "11-01", "to": "11-30", "showId": "xmas"}]))
        world.advance(1)
        self.assertEqual("xmas", world.scheduler.state()["status"]["todayShowId"])
        self.assertEqual(("play", "xmas"), world.go("11:00")[0])

    def test_missing_or_unknown_show_stays_in_standby(self):
        for show_id, reported in ((None, False), ("gone", True)):
            with self.subTest(show_id=show_id):
                world = World("10:30", settings(showId=show_id))
                world.advance(1)
                self.assertEqual([], world.go("11:00"))
                self.assertEqual("standby", world.phase())
                # 沒選演出不算錯誤；找不到的演出會回報。 / No show chosen is not an error; an unknown show is reported.
                error = world.scheduler.state()["status"]["error"]
                self.assertEqual(reported, error is not None and "11:00" in error)

    def test_show_period_on_the_hour_and_at_half_past_with_repeats(self):
        world = World("09:59", settings(showMinutes=[30, 0], showHours={"start": "17:30", "end": "20:30"},
                                        showTimes=[], showRepeats=6))
        world.advance(1)
        self.assertEqual([0, 30], world.scheduler.state()["settings"]["showMinutes"])
        # 10:00 開門：表演時段還沒到，只有待機。 / 10:00 opens: before the show period, standby only.
        self.assertEqual([("blackout", ("spinner", "ball", "wash"), False), ("motors", True), ("standby", True)],
                         world.go("10:00"))
        self.assertEqual([], world.go("17:00"))
        self.assertEqual({"at": "2026-11-14T17:30", "showId": "demo"}, world.scheduler.state()["status"]["nextShow"])
        # 17:30 第一場，連播 6 輪；播完回待機，等 18:00。 / 17:30: first show, 6 rounds; then standby until 18:00.
        self.assertEqual([("play", "demo"), ("standby", False)], world.go("17:30"))
        self.assertEqual(6, world.rounds)
        world.playing = None
        self.assertEqual([("standby", True)], world.advance(1))
        self.assertEqual({"at": "2026-11-14T18:00", "showId": "demo"}, world.scheduler.state()["status"]["nextShow"])
        self.assertEqual([("play", "demo"), ("standby", False)], world.go("18:00"))
        world.playing = None
        world.advance(1)
        # 20:30 最後一場照播（同時是關閉時間）；播完才關閉。 / 20:30: the last show plays (also closing time), then closes.
        self.assertEqual([("play", "demo"), ("standby", False)], world.go("20:30"))
        self.assertEqual("show", world.phase())
        self.assertEqual([], world.advance(300))
        world.playing = None
        self.assertEqual([("stop",), ("motors", False), ("standby", True), ("blackout", ("spinner", "ball", "wash"), True)],
                         world.advance(1))
        self.assertEqual({"at": "2026-11-15T17:30", "showId": "demo"}, world.scheduler.state()["status"]["nextShow"])

    def test_show_period_ending_before_closing_returns_to_standby(self):
        world = World("19:59", settings(showMinutes=[0, 30], showHours={"start": "17:30", "end": "20:00"}))
        world.advance(1)
        self.assertEqual([("play", "demo"), ("standby", False)], world.go("20:00"))
        world.playing = None
        self.assertEqual([("standby", True)], world.advance(1))
        self.assertEqual("standby", world.phase())
        self.assertNotIn(("play", "demo"), world.go("20:30"))

    def test_an_older_file_takes_defaults_for_new_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            old = {key: value for key, value in settings().items() if key not in ("showHours", "showMinutes", "showRepeats")}
            path.write_text(json.dumps(old), encoding="utf-8")
            world = World(path=path)
            self.assertIsNone(world.scheduler.warning)
            self.assertEqual(([0, 30], 6), (world.scheduler.settings["showMinutes"], world.scheduler.settings["showRepeats"]))

    def test_a_show_started_by_hand_returns_to_standby(self):
        world = World("10:30", settings())
        world.advance(1)
        world.starting = True  # 延後開始的演出也算。 / A delayed start counts too.
        self.assertEqual([], world.advance(2))
        self.assertEqual("show", world.phase())
        world.starting = False
        world.playing = {"showId": "demo", "loop": True, "durationSeconds": 60.0}
        # 手動開始的循環演出不會被排程停掉。 / A looping show started by hand is not stopped by the schedule.
        self.assertEqual([], world.advance(90))
        world.playing = None
        self.assertEqual([("standby", True)], world.advance(1))

    def test_manual_changes_are_kept_until_the_next_phase_change(self):
        world = World("10:30", settings())
        world.advance(1)
        self.assertEqual([], world.advance(600))

    def test_disabling_gives_back_the_lights_and_enabling_applies_at_once(self):
        world = World("09:00", settings())
        world.advance(1)
        del world.events[:]
        world.scheduler.apply(settings(enabled=False))
        self.assertEqual([("blackout", ("spinner", "ball", "wash"), False)], world.events)
        self.assertEqual("disabled", world.phase())
        del world.events[:]
        world.scheduler.apply(settings(offGroups=["ball"]))
        self.assertEqual([("stop",), ("motors", False), ("standby", True), ("blackout", ("ball",), True)], world.events)
        # 營業時間外改關閉的燈組：先恢復原本的，再關新的。 / Changing the groups while closed: old ones back, new ones off.
        del world.events[:]
        world.scheduler.apply(settings(offGroups=["wash"]))
        self.assertEqual(("blackout", ("ball",), False), world.events[0])
        self.assertEqual(("blackout", ("wash",), True), world.events[-1])
        # 改成現在在營業時間內：立刻開。 / Hours changed to include now: opens at once.
        del world.events[:]
        world.scheduler.apply(settings(offGroups=["wash"], hours={"start": "08:00", "end": "20:30"}))
        self.assertEqual([("blackout", ("wash",), False), ("motors", True), ("standby", True)], world.events)

    def test_settings_persist_and_a_broken_file_disables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            world = World(path=path)
            self.assertFalse(world.scheduler.state()["settings"]["enabled"])
            world.scheduler.apply(settings(showTimes=["15:00", "11:00"]))
            self.assertEqual(["11:00", "15:00"], json.loads(path.read_text(encoding="utf-8"))["showTimes"])
            again = World(path=path)
            self.assertTrue(again.scheduler.state()["settings"]["enabled"])
            self.assertIsNone(again.scheduler.warning)
            path.write_text("{", encoding="utf-8")
            broken = World(path=path)
            self.assertFalse(broken.scheduler.state()["settings"]["enabled"])
            self.assertIn("schedule.json", broken.scheduler.warning)

    def test_enabled_at_start_needs_one_confirmation_before_motors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text(json.dumps(settings()), encoding="utf-8")
            # 啟動時間內重啟：待機，但不啟動馬達。 / Restarted inside the hours: standby, but no motor start.
            world = World("10:30", path=path)
            self.assertFalse(world.scheduler.state()["status"]["motorsConfirmed"])
            self.assertEqual([("standby", True)], world.advance(1))
            # 表演照常，仍不啟動馬達。 / Shows still play, without motors.
            self.assertEqual([("play", "demo"), ("standby", False)], world.go("11:00"))
            for raw in ({"confirmed": False}, {"confirmed": 1}, {}, None):
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    world.scheduler.confirm(raw)
            del world.events[:]
            self.assertTrue(world.scheduler.confirm({"confirmed": True})["status"]["motorsConfirmed"])
            self.assertEqual([("motors", True)], world.events)
            # 只確認一次：再按不會重複，之後每天照排程。 / Once only; later days follow the schedule.
            world.scheduler.confirm({"confirmed": True})
            self.assertEqual([("motors", True)], world.events)
            world.playing = None
            world.go("20:30")
            world.time += timedelta(days=1)
            self.assertIn(("motors", True), world.go("10:00"))

    def test_unconfirmed_closing_still_stops_motors_and_confirming_while_closed_waits_for_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text(json.dumps(settings()), encoding="utf-8")
            world = World("09:00", path=path)
            self.assertIn(("motors", False), world.advance(1))
            self.assertEqual([("blackout", ("spinner", "ball", "wash"), False), ("standby", True)], world.go("10:00"))
            self.assertIn(("motors", False), world.go("20:30"))
            del world.events[:]
            world.scheduler.confirm({"confirmed": True})
            self.assertEqual([], world.events)
            world.time += timedelta(days=1)
            self.assertIn(("motors", True), world.go("10:00"))

    def test_enabling_by_hand_is_the_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text(json.dumps(settings(enabled=False)), encoding="utf-8")
            world = World("10:30", path=path)
            world.advance(1)
            world.scheduler.apply(settings())
            self.assertIn(("motors", True), world.events)
            self.assertTrue(world.scheduler.state()["status"]["motorsConfirmed"])
            # 重啟時已啟用、之後先停用再啟用：也算確認。 / Disabled then enabled again after a restart counts too.
            again = World("10:30", path=path)
            self.assertEqual([("standby", True)], again.advance(1))
            again.scheduler.apply(settings(enabled=False))
            del again.events[:]
            again.scheduler.apply(settings())
            self.assertIn(("motors", True), again.events)

    def test_thread_runs_and_stops(self):
        world = World("09:00", settings())
        world.scheduler.start()
        try:
            for _ in range(200):
                if world.phase() == "off":
                    break
                time.sleep(0.01)
        finally:
            world.scheduler.stop()
        self.assertEqual("off", world.phase())


class WiringTests(unittest.TestCase):
    """The real stores, wired as in ``output.main``, behind the API."""

    def test_schedule_api_drives_motors_lights_standby_and_shows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 156,
                      "unit_id": 1, "unit_count": 3}
            store = PresetStore(root / "preset.json", 156)
            audio = FakeAudio()
            show_store = ShowStore(root / "shows.json", beat_maps={CLICK_TRACK_ID: CLICK}, fps=40, pixels=156)
            show_store.save("demo", show(ending={"kind": "fade-black", "seconds": 1}))
            player = ShowPlayer(show_store, audio, 40)
            wind = WindStandby()
            clock = [datetime(2026, 11, 14, 9, 0)]

            def show_state():
                player.current()
                return player.state()

            scheduler = Scheduler(
                root / "schedule.json",
                set_motors=lambda enabled: store.apply_motor({"enabled": enabled}, config),
                set_blackout=lambda groups, enabled: store.apply_blackout({"enabled": enabled, "groups": list(groups)}, config),
                set_standby=lambda enabled: wind.set_standby({"enabled": enabled}),
                play_show=lambda show_id, rounds: player.play({"showId": show_id, "rounds": rounds}),
                stop_show=player.stop, show_state=show_state, now=lambda: clock[0], log=lambda line: None)
            with make_server(store, config, 0, show_store=show_store, show_player=player, wind_standby=wind,
                             scheduler=scheduler) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}"

                    def call(method, path, body=None):
                        data = None if body is None else json.dumps(body).encode()
                        request = Request(base + path, data, {"Content-Type": "application/json"}, method=method)
                        with urlopen(request) as response:
                            return json.load(response)

                    self.assertEqual("disabled", call("GET", "/api/schedule")["status"]["phase"])
                    self.assertTrue(call("PUT", "/api/schedule/confirm", {"confirmed": True})["status"]["motorsConfirmed"])
                    with self.assertRaises(HTTPError) as caught:
                        call("PUT", "/api/schedule/confirm", {"confirmed": False})
                    self.assertEqual(422, caught.exception.code)
                    with self.assertRaises(HTTPError) as caught:
                        call("PUT", "/api/schedule", {"enabled": True})
                    self.assertEqual(422, caught.exception.code)
                    # 09:00 啟用：營業時間外，洗牆燈以外都關。 / Enabled at 09:00: closed, everything but the wash is off.
                    call("PUT", "/api/motor", {"enabled": True})
                    result = call("PUT", "/api/schedule", settings(offGroups=["spinner", "ball"], showTimes=["11:00"]))
                    self.assertEqual("off", result["status"]["phase"])
                    state = call("GET", "/api/state")
                    self.assertFalse(state["motor"]["enabled"])
                    self.assertEqual({"spinner": True, "ball": True, "wash": False}, state["blackoutGroups"])
                    self.assertTrue(wind.active())
                    # 10:00：燈恢復、馬達全開、待機。 / 10:00: lights back, every motor on, standby.
                    clock[0] = datetime(2026, 11, 14, 10, 0)
                    scheduler.tick()
                    state = call("GET", "/api/state")
                    self.assertEqual([True, True, True], state["motor"]["units"])
                    self.assertEqual({"spinner": False, "ball": False, "wash": False}, state["blackoutGroups"])
                    self.assertEqual("standby", call("GET", "/api/schedule")["status"]["phase"])
                    # 11:00：演出開始，離開待機。 / 11:00: the show starts and standby ends.
                    clock[0] = datetime(2026, 11, 14, 10, 59, 59)
                    scheduler.tick()
                    clock[0] = datetime(2026, 11, 14, 11, 0, 0)
                    scheduler.tick()
                    playing = call("GET", "/api/shows")["playing"]
                    self.assertEqual(("demo", 1, 6), (playing["showId"], playing["round"], playing["rounds"]))
                    self.assertFalse(wind.active())
                    self.assertEqual("show", call("GET", "/api/schedule")["status"]["phase"])
                    # 演出停止（這裡用手動停止代替播完）：回到待機，馬達照轉。 / The show stops: standby again, motors keep running.
                    call("DELETE", "/api/show/play")
                    scheduler.tick()
                    self.assertTrue(wind.active())
                    self.assertTrue(call("GET", "/api/state")["motor"]["enabled"])
                    # 20:30：馬達停、燈關。 / 20:30: motors stop, lights off.
                    clock[0] = datetime(2026, 11, 14, 20, 30)
                    scheduler.tick()
                    state = call("GET", "/api/state")
                    self.assertFalse(state["motor"]["enabled"])
                    self.assertEqual({"spinner": True, "ball": True, "wash": False}, state["blackoutGroups"])
                    # 停用：燈恢復。 / Disabled: the lights come back.
                    call("PUT", "/api/schedule", settings(enabled=False))
                    self.assertEqual({"spinner": False, "ball": False, "wash": False},
                                     call("GET", "/api/state")["blackoutGroups"])
                finally:
                    server.shutdown()

    def test_without_a_scheduler_the_route_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 156,
                      "unit_id": 1, "unit_count": 3}
            with make_server(PresetStore(root / "preset.json", 156), config, 0) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/schedule")
                    self.assertEqual(404, caught.exception.code)
                finally:
                    server.shutdown()


if __name__ == "__main__":
    unittest.main()
