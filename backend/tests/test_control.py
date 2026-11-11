import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from moon_observe_backend.control import PresetStore, make_server
from moon_observe_backend.effects import default_preset
from moon_observe_backend.output import stream


CONFIG = {
    "ip": "127.0.0.1", "source_ip": None, "universe": 0,
    "fps": 40, "rpm": 360, "pixels": 156, "unit_id": 1, "unit_count": 9,
}


class FakeClient:
    def __init__(self, *_args, **_kwargs):
        self.packets = []

    def set_simplified(self, _value):
        pass

    def set_net(self, _value):
        pass

    def set_subnet(self, _value):
        pass

    def send(self, payload):
        self.packets.append(payload)

    def close(self):
        pass


class ControlTests(unittest.TestCase):
    def test_api_persists_valid_preset_and_rejects_invalid_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preset.json"
            path.write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            store = PresetStore(path, 156)
            with make_server(store, CONFIG, 0) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}"
                    with urlopen(Request(base + "/api/state", headers={"Origin": "http://localhost:5173"})) as response:
                        initial = json.load(response)
                        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
                    self.assertEqual("center-out-sine", initial["preset"]["effectId"])
                    self.assertEqual(0, initial["revision"])
                    self.assertEqual({"channel": 469, "enabled": False, "value": 0, "units": [False] * 9}, initial["motor"])

                    motor_request = Request(
                        base + "/api/motor", b'{"enabled":true}',
                        {"Content-Type": "application/json"}, method="PUT",
                    )
                    with urlopen(motor_request) as response:
                        motor_state = json.load(response)
                    self.assertEqual({"channel": 469, "enabled": True, "value": 255, "units": [True] * 9}, motor_state["motor"])
                    self.assertEqual(255, store.motor_value())

                    bad_motor_request = Request(
                        base + "/api/motor", b'{"enabled":1}',
                        {"Content-Type": "application/json"}, method="PUT",
                    )
                    with self.assertRaises(HTTPError) as motor_error:
                        urlopen(bad_motor_request)
                    self.assertEqual(422, motor_error.exception.code)
                    self.assertEqual(255, store.motor_value())

                    stop_request = Request(
                        base + "/api/motor", b'{"enabled":false}',
                        {"Content-Type": "application/json"}, method="PUT",
                    )
                    with urlopen(stop_request) as response:
                        stopped = json.load(response)
                    self.assertEqual(0, stopped["motor"]["value"])
                    self.assertEqual(0, store.motor_value())

                    updated = default_preset("random-leds")
                    request = Request(
                        base + "/api/preset", json.dumps(updated).encode(),
                        {"Content-Type": "application/json"}, method="PUT",
                    )
                    with urlopen(request) as response:
                        result = json.load(response)
                    self.assertEqual(3, result["revision"])
                    self.assertEqual("random-leds", json.loads(path.read_text(encoding="utf-8"))["effectId"])

                    bad = {**updated, "effectId": "unknown"}
                    request = Request(
                        base + "/api/preset", json.dumps(bad).encode(),
                        {"Content-Type": "application/json"}, method="PUT",
                    )
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(request)
                    self.assertEqual(422, caught.exception.code)
                    self.assertEqual(3, store.state(CONFIG)["revision"])
                    self.assertEqual("random-leds", json.loads(path.read_text(encoding="utf-8"))["effectId"])
                    self.assertEqual(0, PresetStore(path, 156).motor_value())
                finally:
                    server.shutdown()
                    worker.join(timeout=2)

    def test_serves_frontend_and_api_on_one_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dist"
            assets = static / "assets"
            assets.mkdir(parents=True)
            (static / "index.html").write_text("<html>simulator</html>", encoding="utf-8")
            (assets / "app.js").write_text("window.ready=true", encoding="utf-8")
            path = root / "preset.json"
            path.write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            store = PresetStore(path, 156)
            with make_server(store, CONFIG, 0, frontend_dir=static) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}"
                    with urlopen(base + "/") as response:
                        self.assertIn(b"simulator", response.read())
                    with urlopen(base + "/assets/app.js") as response:
                        self.assertIn(b"window.ready", response.read())
                    with urlopen(base + "/api/state") as response:
                        self.assertEqual("center-out-sine", json.load(response)["preset"]["effectId"])
                    request = Request(base + "/api/motor", b'{"enabled":true}',
                                      {"Content-Type": "application/json"}, method="PUT")
                    with urlopen(request) as response:
                        self.assertEqual(255, json.load(response)["motor"]["value"])
                    with self.assertRaises(HTTPError) as missing:
                        urlopen(base + "/api/missing")
                    self.assertEqual(404, missing.exception.code)
                finally:
                    server.shutdown()
                    worker.join(timeout=2)

    def test_stream_switches_preset_at_frame_boundary_without_new_client(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preset.json"
            path.write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            store = PresetStore(path, 156)
            clients = []
            now = [0.0]
            changed = [False]

            def factory(*args, **kwargs):
                client = FakeClient(*args, **kwargs)
                clients.append(client)
                return client

            def sleep(seconds):
                now[0] += seconds
                if not changed[0]:
                    store.apply(default_preset("random-leds"), CONFIG)
                    changed[0] = True

            count = stream(
                CONFIG, store.snapshot(), seconds=0.075,
                client_factory=factory, preset_provider=store.snapshot,
                clock=lambda: now[0], sleep=sleep,
            )
            self.assertEqual(3, count)
            self.assertEqual(1, len(clients))
            self.assertNotEqual(clients[0].packets[0], clients[0].packets[1])


if __name__ == "__main__":
    unittest.main()


class MotorPerUnitTests(unittest.TestCase):
    """每台轉燈個別轉動／停止。 / Starting and stopping single units."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "preset.json"
        path.write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
        self.store = PresetStore(path, 156)

    def test_units_start_and_stop_on_their_own(self):
        store = self.store
        self.assertEqual((0,) * 9, store.motor_values(9))
        state = store.apply_motor({"enabled": True, "units": [2, 5]}, CONFIG)
        self.assertEqual([False, True, False, False, True, False, False, False, False], state["motor"]["units"])
        self.assertEqual((True, 255), (state["motor"]["enabled"], state["motor"]["value"]))
        self.assertEqual((0, 255, 0, 0, 255, 0, 0, 0, 0), store.motor_values(9))
        revision = state["revision"]
        self.assertEqual(revision, store.apply_motor({"enabled": True, "units": [5]}, CONFIG)["revision"])  # no change
        store.apply_motor({"enabled": False, "units": [2]}, CONFIG)
        self.assertEqual((0, 0, 0, 0, 255, 0, 0, 0, 0), store.motor_values(9))
        store.apply_motor({"enabled": True}, CONFIG)
        self.assertEqual((255,) * 9, store.motor_values(9))
        store.apply_motor({"enabled": False}, CONFIG)
        self.assertEqual((0,) * 9, store.motor_values(9))
        self.assertEqual(0, store.motor_value())

    def test_rejects_bad_unit_lists(self):
        for units in ([], [0], [10], ["1"], [True], 3, [1] * 10):
            with self.subTest(units=units), self.assertRaisesRegex(ValueError, "unit ids 1..9"):
                self.store.apply_motor({"enabled": True, "units": units}, CONFIG)
        with self.assertRaisesRegex(ValueError, "motor command"):
            self.store.apply_motor({"enabled": True, "unit": 1}, CONFIG)
        self.assertEqual((0,) * 9, self.store.motor_values(9))
