import json
import tempfile
import unittest
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from moon_observe_backend.control import PresetStore, make_server
from moon_observe_backend.output import stream
from moon_observe_backend.playback import PlaybackMonitor
from moon_observe_backend.wind import (
    GROUPS, WindStandby, default_preset, flow_rate, validate_preset, validate_target, wind_strength,
)
from test_output_dmx import ball_preset, load
from test_send import FakeClient


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def standby(clock, **target):
    wind = WindStandby(clock=clock)
    wind.apply_target({"speedMs": 1.0, "flowDeg": 0.0, "smoothingSeconds": 0.0, "gustiness": 0.0, **target})
    wind.set_standby({"enabled": True})
    return wind


def step(wind, clock, seconds, dt=0.025):
    frame = None
    for _ in range(round(seconds / dt)):
        clock.now += dt
        frame = wind.frame()
    return frame


class WindMappingTests(unittest.TestCase):
    def test_strength_and_rate(self):
        p = default_preset("spinner")["parameters"]
        self.assertEqual(0, wind_strength(1, p))
        self.assertEqual(1, wind_strength(12, p))
        self.assertAlmostEqual(0.5, wind_strength(6.5, p))
        self.assertAlmostEqual(0.15 * 0.6, flow_rate(0, p))
        self.assertAlmostEqual(0.15 * 3, flow_rate(20, p))

    def test_validation(self):
        for group in GROUPS:
            self.assertEqual(default_preset(group), validate_preset(group, default_preset(group)))
        bad = default_preset("spinner")
        bad["parameters"]["speedHz"] = 5
        with self.assertRaises(ValueError):
            validate_preset("spinner", bad)
        with self.assertRaises(ValueError):
            validate_preset("ball", default_preset("spinner"))       # sphereSpan is not a ball parameter
        with self.assertRaises(ValueError):
            validate_preset("spinner", {**default_preset("spinner"), "colors": [{"r": 1, "g": 1, "b": 1}]})
        with self.assertRaises(ValueError):
            validate_target({"speedMs": 3})
        self.assertEqual(10, validate_target({"speedMs": 3, "flowDeg": 370 - 360, "smoothingSeconds": 0, "gustiness": 0})["flowDeg"])


class WindFrameTests(unittest.TestCase):
    def test_out_of_standby_there_is_no_frame(self):
        self.assertIsNone(WindStandby().frame())

    def test_band_travels_downwind_and_faster_in_stronger_wind(self):
        def crest_position(wind, clock):
            frame = step(wind, clock, 0.025)
            geometry = [(0.5, x / 40, 0.5) for x in range(41)]
            levels = [max(color) for color in frame.fixture_colors("ball", geometry)]
            return levels.index(max(levels)) / 40

        def travelled(speed):
            clock = Clock()
            wind = standby(clock, speedMs=speed)
            first = crest_position(wind, clock)
            step(wind, clock, 1.0)
            return (crest_position(wind, clock) - first) % 1

        calm, strong = travelled(1.0), travelled(2.9)               # both below the sparkle threshold
        self.assertGreater(calm, 0.05)                               # moves toward +x (wind toward 0°)
        self.assertGreater(strong, calm * 1.3)

    def test_sparkles_only_at_or_above_the_threshold_and_sweep_to_the_end(self):
        def sweep_trace(speed, seconds):
            clock = Clock()
            wind = standby(clock, speedMs=speed)
            trace = []
            for _ in range(round(seconds / 0.025)):
                clock.now += 0.025
                wind.frame()
                trace.append(next(iter(wind.timings.values())).sweep)
            return trace

        self.assertTrue(all(value is None for value in sweep_trace(2.9, 5)))
        trace = sweep_trace(3.0, 5)
        self.assertEqual(0.0, trace[0])
        ended = trace.index(None)
        self.assertAlmostEqual(2.0, ended * 0.025, delta=0.06)       # sweepSeconds at the threshold
        self.assertGreater(trace[ended - 1], 0.97)                   # ran to the end
        self.assertTrue(all(value is None for value in trace[ended:ended + 200]))  # 6 s gap
        fast = sweep_trace(12.0, 2)
        self.assertAlmostEqual(0.6, fast.index(None) * 0.025, delta=0.06)  # sweepFastSeconds at fullMs

    def test_sweep_keeps_its_direction_and_finishes_when_the_wind_changes(self):
        clock = Clock()
        wind = standby(clock, speedMs=3.0)
        step(wind, clock, 0.5)
        wind.apply_target({"speedMs": 0.0, "flowDeg": 180.0, "smoothingSeconds": 0.0, "gustiness": 0.0})
        step(wind, clock, 0.5)
        timing = next(iter(wind.timings.values()))
        self.assertIsNotNone(timing.sweep)
        self.assertEqual(0.0, timing.sweep_deg)
        step(wind, clock, 1.5)
        self.assertIsNone(timing.sweep)
        step(wind, clock, 10)
        self.assertIsNone(timing.sweep)                              # calm: no new sweep

    def test_groups_with_the_same_timing_stay_in_step(self):
        clock = Clock()
        wind = standby(clock, speedMs=2.0)
        frame = step(wind, clock, 3.0)
        self.assertEqual(1, len(wind.timings))
        ball = frame.fixture_colors("ball", [(0.5, 0.3, 0.5)])[0]
        # The middle pixel is the top of the sphere (height 0), so it matches a ball at the same place.
        top = frame.spinner_pixels(1, 24, 129, (0.3, 0.5))[64]
        for a, b in zip(ball, top):
            self.assertAlmostEqual(a, b)

    def test_pixels_are_in_range_and_colour_follows_the_wind(self):
        def middle(speed):
            clock = Clock()
            return step(standby(clock, speedMs=speed), clock, 1.0).spinner_pixels(3, 24, 129, (0.4, 0.6))

        calm, strong = middle(0.5), middle(12.0)
        self.assertEqual(129, len(calm))
        for pixel in calm + strong:
            for channel in pixel:
                self.assertTrue(0 <= channel <= 1)
        self.assertGreater(sum(p[0] for p in calm), sum(p[1] for p in calm))      # calm: orange (red over green)
        self.assertGreater(sum(p[1] for p in strong), sum(p[0] for p in strong))  # strong: green

    def test_smoothing_and_turn_limit(self):
        clock = Clock()
        wind = standby(clock, speedMs=0.3)
        step(wind, clock, 0.1)                                       # settle on the starting wind
        wind.apply_target({"speedMs": 10.0, "flowDeg": 90.0, "smoothingSeconds": 2.0, "gustiness": 0.0})
        step(wind, clock, 2.0)
        state = wind.state()["wind"]
        self.assertAlmostEqual(0.3 + 9.7 * (1 - 2.718281828 ** -1), state["speedMs"], delta=0.2)
        self.assertLess(state["colorSpeedMs"], state["speedMs"])
        self.assertAlmostEqual(6.0, state["flowDeg"], delta=0.3)     # 3° per second

    def test_settings_persist_and_a_broken_file_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wind-standby.json"
            wind = WindStandby(path)
            presets = {group: default_preset(group) for group in GROUPS}
            presets["ball"]["parameters"]["heightSpan"] = 0.5
            wind.apply_presets(presets)
            wind.apply_target({"speedMs": 7.0, "flowDeg": 45.0, "smoothingSeconds": 1.0, "gustiness": 0.2})
            wind.set_standby({"enabled": True})
            again = WindStandby(path)
            self.assertEqual(0.5, again.state()["presets"]["ball"]["parameters"]["heightSpan"])
            self.assertEqual(7.0, again.state()["target"]["speedMs"])
            self.assertFalse(again.state()["standby"])              # standby itself is memory only
            path.write_text("{broken", encoding="utf-8")
            broken = WindStandby(path)
            self.assertIsNotNone(broken.warning)
            self.assertEqual(default_preset("ball"), broken.state()["presets"]["ball"])


class WindOutputTests(unittest.TestCase):
    def test_standby_replaces_the_effect_on_every_group_but_not_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            config, preset = load(Path(directory))
            clock = Clock()
            wind = standby(clock)
            clients = []

            def factory(*args, **kwargs):
                clients.append(FakeClient(*args, **kwargs))
                return clients[-1]

            now = [0.0]

            def sleep(seconds):
                now[0] += seconds
                clock.now += seconds

            def run(**options):
                clients.clear()
                now[0] = 0.0
                stream(config, preset, seconds=0.05, client_factory=factory, clock=lambda: now[0], sleep=sleep,
                       fixture_preset_provider=lambda: {"ball": ball_preset(), "wash": None}, **options)
                return [client.packets[0] for client in clients]

            plain = run()
            in_standby = run(standby_provider=wind.frame)
            self.assertNotEqual(plain[0][:387], in_standby[0][:387])     # spinner
            self.assertNotEqual(plain[1], in_standby[1])                 # balls
            self.assertNotEqual(plain[2], in_standby[2])                 # washes: lit by wind-flow, no preset needed
            flashed = run(standby_provider=wind.frame, beat_flash_provider=lambda: 1.0)
            self.assertEqual(bytes([255]) * 387, flashed[0][:387])       # the beat flash stays above standby


class WindApiTests(unittest.TestCase):
    def test_wind_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = load(root)
            store = PresetStore(root / "preset.json", 129)
            monitor = PlaybackMonitor(config["fps"], config["unit_count"])
            monitor.report("single", store.snapshot(), 0, 0)
            wind = WindStandby(root / "wind-standby.json")
            with make_server(store, config, 0, playback_monitor=monitor, wind_standby=wind) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def call(method, path, body=None):
                    data = None if body is None else json.dumps(body).encode()
                    request = Request(base + path, data, {"Content-Type": "application/json"}, method=method)
                    with urlopen(request) as response:
                        return json.load(response)

                try:
                    state = call("GET", "/api/wind")
                    self.assertFalse(state["standby"])
                    self.assertEqual(set(GROUPS), set(state["presets"]))
                    self.assertNotIn("standby", call("GET", "/api/playback"))
                    self.assertTrue(call("PUT", "/api/wind/standby", {"enabled": True})["standby"])
                    self.assertTrue(call("GET", "/api/playback")["standby"])
                    presets = state["presets"]
                    presets["spinner"]["parameters"]["sphereSpan"] = 0.6
                    self.assertEqual(0.6, call("PUT", "/api/wind/presets", presets)["presets"]["spinner"]["parameters"]["sphereSpan"])
                    target = {"speedMs": 8, "flowDeg": 90, "smoothingSeconds": 0, "gustiness": 0}
                    self.assertEqual(8, call("PUT", "/api/wind/target", target)["target"]["speedMs"])
                    self.assertTrue((root / "wind-standby.json").exists())
                    self.assertFalse(call("PUT", "/api/wind/standby", {"enabled": False})["standby"])
                    for path, bad in (("/api/wind/standby", {"enabled": 1}), ("/api/wind/target", {"speedMs": 99}),
                                      ("/api/wind/presets", {"spinner": {}})):
                        with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                            call("PUT", path, bad)
                        self.assertEqual(422, caught.exception.code)
                finally:
                    server.shutdown()
                    worker.join()


if __name__ == "__main__":
    unittest.main()
