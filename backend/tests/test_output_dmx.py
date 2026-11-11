import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from moon_observe_backend.config import load_config
from moon_observe_backend.control import PresetStore, make_server
from moon_observe_backend.dmx import build_universes
from moon_observe_backend.effects import BLACK, default_preset, validate_preset
from moon_observe_backend.fixture_effects import default_fixture_preset, render_fixture_frame, validate_fixture_preset
from moon_observe_backend.output import fixture_counts, stream
from moon_observe_backend.playback import PlaybackMonitor
from moon_observe_backend.sequence import compile_sequence, render_sequence_fixtures
from moon_observe_backend.unit_colors import FixtureColorStore

from test_send import FakeClient

BACKEND = Path(__file__).resolve().parents[1]
DMX = '''
[[dmx.profiles]]
name = "ball-rgbw"
channels = ["r", "g", "b", "w"]

[[dmx.profiles]]
name = "wash-rgbd"
channels = ["dimmer", "r", "g", "b"]
static = { dimmer = 255 }
brightness = 0.5

[[dmx.nodes]]
name = "ball-node"
ip = "192.0.2.200"
universe = 100

[[dmx.nodes]]
name = "wash-node"
ip = "192.0.2.201"
universe = 0x0213

[[dmx.patch]]
group = "ball"
first_id = 1
count = 37
profile = "ball-rgbw"
node = "ball-node"
address = 1

[[dmx.patch]]
group = "wash"
first_id = 1
count = 3
profile = "wash-rgbd"
node = "wash-node"
address = 10
'''
RED = {"mode": "solid", "color": {"r": 1, "g": 0, "b": 0}}


def load(root: Path):
    (root / "preset.json").write_text(json.dumps(default_preset("full-on")), encoding="utf-8")
    (root / "layout.json").write_text((BACKEND / "layout.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    path = root / "config.toml"
    path.write_text('[artnet]\nip = "192.0.2.20"\nuniverse = 0\n[render]\npreset = "preset.json"\npixels = 129\n'
                    'unit_count = 24\nlayout = "layout.json"\n'
                    '[hardware]\npixel_rotation = 64\n[motor]\nchannel = 469\n' + DMX, encoding="utf-8")
    return load_config(path)


def ball_preset(effect_id="fixture-solid", **extra):
    return validate_fixture_preset({**default_fixture_preset(effect_id), "color": RED, **extra})


def run(config, preset, **providers):
    clients = []
    now = [0.0]

    def factory(*args, **kwargs):
        client = FakeClient(*args, **kwargs)
        clients.append(client)
        return client

    def sleep(seconds):
        now[0] += seconds

    sent = stream(config, preset, seconds=0.05, client_factory=factory, clock=lambda: now[0], sleep=sleep, **providers)
    return sent, clients


class OutputDmxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.config, cls.preset = load(Path(cls.directory.name))
        cls.dmx = cls.config["dmx"]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_single_effect_frames_and_blackout_on_exit(self):
        ball = ball_preset()
        sent, clients = run(self.config, self.preset, fixture_preset_provider=lambda: {"ball": ball, "wash": None})
        spinner, ball_node, wash_node = clients
        self.assertEqual(("192.0.2.200", 100 & 0x0F, 512), ball_node.init[:3])
        self.assertEqual((0, 100 >> 4), (ball_node.net, ball_node.subnet))
        self.assertEqual(("192.0.2.201", 0x0213 & 0x0F, 512), wash_node.init[:3])
        self.assertEqual((2, 1), (wash_node.net, wash_node.subnet))
        self.assertEqual(2 + 2 * 2, sent)  # 2 frames × (1 spinner + 2 DMX universes)
        expected = build_universes({"ball": [(1.0, 0.0, 0.0)] * 37, "wash": [BLACK] * 3}, self.dmx)
        self.assertEqual([expected[0]] * 2 + [bytes(512)] * 2, ball_node.packets)
        self.assertEqual(bytes([255, 0, 0, 0]), expected[0][:4])            # ball 1: R
        self.assertEqual(bytes([255, 0, 0, 0]), expected[0][144:148])       # ball 37 at ch145
        self.assertEqual(bytes([255, 0, 0, 0]), expected[1][9:13])          # wash dimmer stays 255 while dark
        self.assertEqual([expected[1]] * 2 + [bytes(512)] * 2, wash_node.packets)
        self.assertEqual([470, 470], [len(packet) for packet in spinner.packets])
        self.assertTrue(all(client.closed for client in clients))

    def test_no_fixture_presets_keeps_dmx_dark_but_sending(self):
        _, clients = run(self.config, self.preset)
        dark = build_universes({"ball": [BLACK] * 37, "wash": [BLACK] * 3}, self.dmx)
        self.assertEqual(dark[0], clients[1].packets[0])
        self.assertEqual(4, len(clients[1].packets))

    def test_fixture_colour_test_overrides_chosen_fixtures(self):
        store = FixtureColorStore(fixture_counts(self.config))
        colors = [None] * 37
        colors[4] = {"r": 1, "g": 1, "b": 1}
        store.apply({"group": "ball", "colors": colors, "seconds": None})
        _, clients = run(self.config, self.preset, fixture_preset_provider=lambda: {"ball": ball_preset(), "wash": None},
                         fixture_colors_provider=store.current)
        frame = clients[1].packets[0]
        self.assertEqual(bytes([0, 0, 0, 255]), frame[16:20])  # ball 5 white -> W only
        self.assertEqual(bytes([255, 0, 0, 0]), frame[12:16])  # ball 4 keeps playing red

    def test_sequence_mode_uses_the_step_presets(self):
        document = {"schemaVersion": 2, "ending": {"kind": "loop"}, "steps": [{
            "id": 1, "spinner": default_preset("full-on"),
            "ball": {**default_fixture_preset("height-sweep"), "color": RED},
            "playback": {"kind": "time", "seconds": 5}, "transition": {"kind": "cut", "seconds": 0},
        }]}
        schedule = compile_sequence(document, 40, 9, 129, fixture_counts(self.config))
        _, clients = run(self.config, self.preset, sequence_provider=lambda: ("sequence", schedule, 1),
                         fixture_preset_provider=lambda: {"ball": ball_preset(), "wash": None})
        for frame in (0, 1):
            balls = render_sequence_fixtures(schedule, "ball", self.dmx["geometry"]["ball"], frame, fps=40)
            expected = build_universes({"ball": balls, "wash": [BLACK] * 3}, self.dmx)
            self.assertEqual(expected[0], clients[1].packets[frame])
        self.assertNotEqual(clients[1].packets[0], clients[1].packets[1])  # it moves (not the single preset)

    def test_weather_applies_to_fixtures(self):
        palette = {"mode": "gradient", "axis": "radius", "colors": [{"r": 0, "g": 0, "b": 1}, {"r": 0, "g": 1, "b": 0}]}
        weather = {"category": "rain", "color": palette, "frameStep": 0, "generation": 1}
        ball = ball_preset()
        _, clients = run(self.config, self.preset, weather_provider=lambda: weather,
                         fixture_preset_provider=lambda: {"ball": ball, "wash": None})
        balls = render_fixture_frame(ball, self.dmx["geometry"]["ball"], 0, fps=40, raining=True, color_override=palette)
        self.assertEqual(build_universes({"ball": balls, "wash": [BLACK] * 3}, self.dmx)[0], clients[1].packets[0])

    def test_motor_stops_even_if_dmx_send_fails(self):
        class BrokenDmx(FakeClient):
            def send(self, payload):
                if len(payload) == 512:
                    raise OSError("network down")
                super().send(payload)

        clients = []

        def factory(*args, **kwargs):
            client = (BrokenDmx if args[2] == 512 else FakeClient)(*args, **kwargs)
            clients.append(client)
            return client

        with self.assertRaises(OSError):
            stream(self.config, self.preset, seconds=1, client_factory=factory, motor_provider=lambda: 255,
                   fixture_preset_provider=lambda: {"ball": ball_preset(), "wash": None},
                   clock=lambda: 0.0, sleep=lambda _seconds: None)
        spinner = clients[0]
        self.assertEqual([255, 0], [packet[468] for packet in spinner.packets])  # frame, then the exit stop
        self.assertTrue(all(client.closed for client in clients))


class BlackoutOutputTests(unittest.TestCase):
    """全部關燈：轉燈、球燈、洗牆燈送全黑，蓋過效果、測試與閃拍；馬達照舊。 / Blackout over everything; motors untouched."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.config, cls.preset = load(Path(cls.directory.name))
        cls.dmx = cls.config["dmx"]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_blackout_darkens_every_group_and_keeps_motors(self):
        tests = FixtureColorStore(fixture_counts(self.config))
        tests.apply({"group": "wash", "colors": [{"r": 1, "g": 1, "b": 1}] * 3, "seconds": None})
        blackout = [False]
        clients = []
        now = [0.0]

        def factory(*args, **kwargs):
            clients.append(FakeClient(*args, **kwargs))
            return clients[-1]

        def sleep(seconds):
            now[0] += seconds
            blackout[0] = True  # 第 2 格起關燈。 / Off from the second frame.

        stream(self.config, self.preset, seconds=0.05, client_factory=factory, clock=lambda: now[0], sleep=sleep,
               fixture_preset_provider=lambda: {"ball": ball_preset(), "wash": None},
               fixture_colors_provider=tests.current, motor_provider=lambda: 255,
               beat_flash_provider=lambda: 1.0, blackout_provider=lambda: blackout[0])
        spinner, ball_node, wash_node = clients
        self.assertNotEqual(bytes(387), spinner.packets[0][:387])        # frame 0: flash white
        self.assertEqual(bytes(387), spinner.packets[1][:387])           # frame 1: black
        self.assertEqual([255, 255, 0], [packet[468] for packet in spinner.packets])  # motor runs, soft stop on exit
        dark = build_universes({"ball": [BLACK] * 37, "wash": [BLACK] * 3}, self.dmx)
        self.assertNotEqual(dark[0], ball_node.packets[0])
        self.assertEqual(dark[0], ball_node.packets[1])
        self.assertEqual(dark[1], wash_node.packets[1])                  # over the wash colour test


class FixtureApiTests(unittest.TestCase):
    def test_blackout_api_and_playback_mirror(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = load(root)
            store = PresetStore(root / "preset.json", 129)
            monitor = PlaybackMonitor(config["fps"], config["unit_count"])
            monitor.report("single", store.snapshot(), 0, 0)
            with make_server(store, config, 0, playback_monitor=monitor) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def call(method, path, body=None):
                    data = None if body is None else json.dumps(body).encode()
                    request = Request(base + path, data, {"Content-Type": "application/json"}, method=method)
                    with urlopen(request) as response:
                        return json.load(response)

                try:
                    self.assertFalse(call("GET", "/api/state")["blackout"])
                    self.assertNotIn("blackout", call("GET", "/api/playback"))
                    store.apply_motor({"enabled": True, "units": [3]}, config)
                    state = call("PUT", "/api/blackout", {"enabled": True})
                    self.assertTrue(state["blackout"])
                    self.assertTrue(store.blackout_active())
                    self.assertEqual(255, store.motor_values(24)[2])  # motors untouched
                    playback = call("GET", "/api/playback")
                    self.assertTrue(playback["blackout"])
                    self.assertEqual([{"r": 0, "g": 0, "b": 0}] * 24, playback["unitColors"])
                    self.assertEqual({"ball": 37, "wash": 3}, {group: len(colors) for group, colors in playback["fixtureColors"].items()})
                    revision = state["revision"]
                    self.assertEqual(revision, call("PUT", "/api/blackout", {"enabled": True})["revision"])  # no change
                    self.assertFalse(call("PUT", "/api/blackout", {"enabled": False})["blackout"])
                    for bad in ({}, {"enabled": 1}, {"enabled": True, "units": [1]}):
                        with self.subTest(bad=bad), self.assertRaises(HTTPError) as caught:
                            call("PUT", "/api/blackout", bad)
                        self.assertEqual(422, caught.exception.code)
                finally:
                    server.shutdown()
                    worker.join(timeout=2)

    def test_presets_tests_and_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = load(root)
            store = PresetStore(root / "preset.json", 129)
            fixture_store = FixtureColorStore(fixture_counts(config))
            with make_server(store, config, 0, fixture_color_store=fixture_store) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def call(method, path, body=None):
                    data = None if body is None else json.dumps(body).encode()
                    request = Request(base + path, data, {"Content-Type": "application/json"}, method=method)
                    with urlopen(request) as response:
                        return json.load(response)

                try:
                    state = call("GET", "/api/state")
                    self.assertEqual({"ball": {"count": 37, "preset": None}, "wash": {"count": 3, "preset": None}},
                                     state["fixtures"])
                    ball = {**default_fixture_preset("fixture-breathing"), "color": RED}
                    state = call("PUT", "/api/preset", {"ball": ball})
                    self.assertEqual("fixture-breathing", state["fixtures"]["ball"]["preset"]["effectId"])
                    self.assertEqual("full-on", state["preset"]["effectId"])  # spinner untouched
                    saved = json.loads((root / "preset.json").read_text(encoding="utf-8"))
                    self.assertEqual({"spinner", "ball"}, set(saved))
                    # 舊格式只換轉燈，球燈保留。 / The old format replaces only the spinner; the ball preset stays.
                    state = call("PUT", "/api/preset", default_preset("center-out-sine"))
                    self.assertEqual("fixture-breathing", state["fixtures"]["ball"]["preset"]["effectId"])
                    self.assertEqual("fixture-breathing", PresetStore(root / "preset.json", 129).fixture_snapshot()["ball"]["effectId"])
                    state = call("PUT", "/api/preset", {"ball": None})
                    self.assertIsNone(state["fixtures"]["ball"]["preset"])
                    self.assertEqual("center-out-sine", json.loads((root / "preset.json").read_text(encoding="utf-8"))["effectId"])
                    for bad in ({"spinner": default_preset("full-on"), "lights": None}, {"wash": default_preset("full-on")}, {}):
                        with self.subTest(bad=bad), self.assertRaises(HTTPError) as caught:
                            call("PUT", "/api/preset", bad)
                        self.assertEqual(422, caught.exception.code)

                    colors = [None, {"r": 1, "g": 1, "b": 1}, None]
                    result = call("PUT", "/api/test/fixtures", {"group": "wash", "colors": colors, "seconds": 30})
                    self.assertTrue(result["wash"]["active"])
                    self.assertFalse(result["ball"]["active"])
                    self.assertEqual(result["wash"]["colors"], call("GET", "/api/test/fixtures")["wash"]["colors"])
                    with self.assertRaises(HTTPError) as caught:
                        call("PUT", "/api/test/fixtures", {"group": "ball", "colors": [None], "seconds": None})
                    self.assertIn("37 fixtures", json.load(caught.exception)["error"])
                    self.assertFalse(call("DELETE", "/api/test/fixtures")["wash"]["active"])

                    self.assertEqual({"active": False}, call("GET", "/api/output"))
                    patch = call("GET", "/api/patch")
                    self.assertEqual([{"unitId": 1, "ip": "192.0.2.20", "universes": [0],
                                       "position": {"x": 961.0, "y": 118.0, "z": 565.0}}], patch["spinners"])
                    self.assertEqual(24, len(patch["spinnerPositions"]))
                    self.assertEqual(15, len(patch["poles"]))
                    self.assertEqual({"x": 3370.0, "y": 349.0, "z": 885.0}, patch["spinnerPositions"][23])
                    self.assertEqual(["ball-node", "wash-node"], [node["name"] for node in patch["nodes"]])
                    first, last = patch["fixtures"]["ball"][0], patch["fixtures"]["ball"][-1]
                    self.assertEqual((1, 1, 4, {"x": 961.0, "y": 202.0, "z": 340.0}), (first["id"], first["address"], first["lastChannel"], first["position"]))
                    self.assertEqual((37, 145, 148, 100), (last["id"], last["address"], last["lastChannel"], last["universe"]))
                    self.assertEqual(["dimmer", "r", "g", "b"], patch["fixtures"]["wash"][0]["channels"])
                    self.assertEqual({"x": 75.0, "y": 933.0, "z": 20.0}, patch["fixtures"]["wash"][0]["position"])  # back wall
                finally:
                    server.shutdown()
                    worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
