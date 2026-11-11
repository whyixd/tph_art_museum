import json
from pathlib import Path
import tempfile
import unittest

from moon_observe_backend.config import DEFAULT_HARDWARE, load_config
from moon_observe_backend.effects import default_preset, validate_preset
from moon_observe_backend.hardware import build_packets, led_bytes, packet_sizes, physical_order, universe_count
from moon_observe_backend.output import stream
from moon_observe_backend.renderer import render_frame, rgb_bytes

from test_send import FakeClient

BACKEND = Path(__file__).resolve().parents[1]
MUSEUM_HARDWARE = {**DEFAULT_HARDWARE, "pixel_rotation": 64}


def reference_payload(pixels, motor_value):
    """The Moon Observe reference: 468 RGB bytes, ch469 motor, ch470 padding."""
    payload = bytearray(rgb_bytes(pixels))
    payload.extend(bytes(470 - len(payload)))
    payload[468] = motor_value
    return bytes(payload)


def write_config(root: Path, body: str) -> Path:
    (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
    path = root / "config.toml"
    path.write_text('[render]\npreset = "preset.json"\n' + body, encoding="utf-8")
    return path


class PacketTests(unittest.TestCase):
    def test_defaults_match_reference_payload_byte_for_byte(self):
        preset = validate_preset(default_preset("color-breathing"))
        for frame in (0, 17, 333):
            pixels = render_frame(preset, {"unitId": 3, "logicalPixelCount": 156, "frameOffset": 0}, frame)
            for motor in (0, 255):
                packets = build_packets(pixels, hardware=DEFAULT_HARDWARE, motor_channel=469, motor_value=motor)
                self.assertEqual([reference_payload(pixels, motor)], packets)

    def test_color_order_and_brightness(self):
        pixels = [(1.0, 0.5, 0.0), (0.2, 0.4, 0.6)]
        self.assertEqual(bytes([128, 255, 0, 102, 51, 153]), led_bytes(pixels, "GRB"))
        # floor(x * brightness * 255 + 0.5): 0.5 * 0.4 * 255 = 51, 1 * 0.4 * 255 = 102.
        self.assertEqual(bytes([102, 51, 0]), led_bytes(pixels[:1], "RGB", 0.4))
        with self.assertRaises(ValueError):
            led_bytes(pixels, "RGBW")
        with self.assertRaises(ValueError):
            led_bytes(pixels, "RGB", 1.5)

    def test_split_into_consecutive_universes_without_splitting_pixels(self):
        hardware = {**DEFAULT_HARDWARE, "channels_per_universe": 510}
        pixels = [(index / 400, 0.0, 1.0) for index in range(400)]  # 1200 LED channels
        packets = build_packets(pixels, hardware=hardware, motor_channel=1201, motor_value=255)
        self.assertEqual([510, 510, 182], [len(packet) for packet in packets])
        self.assertEqual([510, 510, 182], packet_sizes(400, 1201, 510))
        self.assertEqual(3, universe_count(400, 1201, 510))
        stream_bytes = b"".join(packet[:510] for packet in packets[:2]) + packets[2][:181]
        self.assertEqual(led_bytes(pixels) + bytes([255]), stream_bytes)
        self.assertEqual(0, packets[2][181])  # even-length padding

    def test_motor_channel_may_sit_in_a_later_universe(self):
        hardware = {**DEFAULT_HARDWARE, "channels_per_universe": 300}
        packets = build_packets([(1.0, 1.0, 1.0)] * 100, hardware=hardware, motor_channel=512, motor_value=255)
        self.assertEqual([300, 212], [len(packet) for packet in packets])
        self.assertEqual(255, packets[1][211])
        self.assertEqual(bytes(210), packets[1][1:211])

    def test_rejects_overlap_and_bad_motor_value(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            build_packets([(0.0, 0.0, 0.0)] * 10, hardware=DEFAULT_HARDWARE, motor_channel=30, motor_value=0)
        with self.assertRaisesRegex(ValueError, "0 or 255"):
            build_packets([(0.0, 0.0, 0.0)] * 10, hardware=DEFAULT_HARDWARE, motor_channel=31, motor_value=1)


class MuseumSpinnerTests(unittest.TestCase):
    """美術館轉燈：3 條並聯、每條 129 顆，由頂部起往下繞垂直圓周一圈；ch469 馬達。

    Museum spinner: 3 strips in parallel, 129 LEDs each, starting at the top and running once around a
    vertical circle; motor on ch469. ``pixel_rotation = 64`` puts the effect centre at the top.
    """

    def test_physical_order_rotates_effect_pixels(self):
        self.assertEqual([2, 3, 4, 0, 1], physical_order([0, 1, 2, 3, 4], 2))
        self.assertEqual([0, 1, 2], physical_order([0, 1, 2], 0))
        for rotation in (-1, 3, 1.0):
            with self.subTest(rotation=rotation), self.assertRaises(ValueError):
                physical_order([0, 1, 2], rotation)

    def test_golden_payload(self):
        # Effect pixel v carries bytes (v, 128 - v, 255), so each physical slot shows which effect pixel it got.
        pixels = [(v / 255, (128 - v) / 255, 1.0) for v in range(129)]
        for motor in (0, 255):
            packets = build_packets(pixels, hardware=MUSEUM_HARDWARE, motor_channel=469, motor_value=motor)
            self.assertEqual([470], [len(packet) for packet in packets])
            payload = packets[0]
            self.assertEqual(bytes([64, 64, 255, 65, 63, 255]), payload[:6])        # LED 0 (top) = effect centre 64
            self.assertEqual(bytes([128, 0, 255, 0, 128, 255]), payload[192:198])   # LEDs 64, 65 (bottom) = effect ends
            self.assertEqual(bytes([63, 65, 255]), payload[384:387])                # LED 128, next to the top
            expected = bytes(byte for i in range(129) for byte in ((i + 64) % 129, 128 - (i + 64) % 129, 255))
            self.assertEqual(expected, payload[:387])
            self.assertEqual(bytes(81), payload[387:468])                           # ch388-468 unused
            self.assertEqual(bytes([motor, 0]), payload[468:])                      # ch469 motor, ch470 padding

    def test_effect_centre_is_at_the_top_and_ends_at_the_bottom(self):
        preset = validate_preset({
            "schemaVersion": 2, "effectId": "full-on", "parameters": {},
            "color": {"mode": "gradient", "axis": "radius",
                      "colors": [{"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 0, "b": 1}]},
        }, 129)
        pixels = render_frame(preset, {"unitId": 1, "logicalPixelCount": 129, "frameOffset": 0}, 0, unit_count=24)
        payload = build_packets(pixels, hardware=MUSEUM_HARDWARE, motor_channel=469, motor_value=0)[0]
        led = [tuple(payload[3 * i:3 * i + 3]) for i in range(129)]
        self.assertEqual((255, 0, 0), led[0])                       # top: radius 0
        self.assertEqual([(0, 0, 255)] * 2, [led[64], led[65]])     # bottom: radius 1 on both sides
        for i in range(1, 129):                                      # the two sides of the circle mirror each other
            self.assertEqual(led[i], led[129 - i])
        reds = [pixel[0] for pixel in led[:65]]
        self.assertEqual(sorted(reds, reverse=True), reds)          # top -> bottom fades red to blue

    def test_example_config_is_the_museum_spinner(self):
        config = load_config(BACKEND / "config.example.toml")[0]
        self.assertEqual(129, config["pixels"])
        self.assertEqual(24, config["unit_count"])
        self.assertEqual(64, config["hardware"]["pixel_rotation"])
        self.assertEqual(469, config["motor"]["channel"])
        # 啟動分區：12 個控箱、各 2 台，24 台都在且只出現一次。 / Start groups: 12 boxes of 2, every unit once.
        groups = config["motor"]["start_groups"]
        self.assertEqual(1.0, config["motor"]["start_interval_seconds"])
        self.assertEqual([f"控箱 {box}" for box in range(101, 113)], [group["name"] for group in groups])
        self.assertEqual(list(range(1, 25)), sorted(unit for group in groups for unit in group["units"]))
        self.assertEqual([18, 19], groups[8]["units"])   # 控箱 109 / 110 cross over (doc/新北美_ip控箱.pdf)
        self.assertEqual([17, 20], groups[9]["units"])
        self.assertEqual([470], config["packet_sizes"])
        self.assertEqual("2.0.0.10", config["source_ip"])  # bind to the Art-Net address (safety catch)
        # 現場 IP（doc/新北美_ip控箱.pdf）：unit n = 2.0.0.(200 + n)。 / Site IPs: unit n = 2.0.0.(200 + n).
        self.assertEqual([(unit, f"2.0.0.{200 + unit}", [0]) for unit in range(1, 25)],
                         [(t["unit_id"], t["ip"], t["universes"]) for t in config["targets"]])
        dmx = config["dmx"]
        self.assertEqual([f"2.0.0.{box}" for box in (105, 101, 106, 102, 107, 103, 108, 109, 110, 111, 112, 100)],
                         [node["ip"] for node in dmx["nodes"]])
        # 洗牆燈：25 顆 RGBW，獨立的 2.0.0.100（暫定），位址 1, 5, … 97。
        # Wall washers: 25 RGBW on their own 2.0.0.100 (provisional), addresses 1, 5, ... 97.
        washers = dmx["fixtures"]["wash"]
        self.assertEqual(list(range(1, 26)), [wash["id"] for wash in washers])
        self.assertEqual([4 * index + 1 for index in range(25)], [wash["address"] for wash in washers])
        self.assertEqual({"2.0.0.100"}, {dmx["nodes"][wash["node"]]["ip"] for wash in washers})
        self.assertEqual(("r", "g", "b", "w"), tuple(dmx["profiles"]["wash-rgbw"]["channels"]))
        balls = dmx["fixtures"]["ball"]
        self.assertEqual(list(range(1, 38)), [ball["id"] for ball in balls])
        self.assertEqual([4 * index + 1 for index in range(37)], [ball["address"] for ball in balls])
        node_of = {ball["id"]: dmx["nodes"][ball["node"]]["ip"] for ball in balls}
        self.assertEqual(["2.0.0.109"] * 4, [node_of[i] for i in (24, 27, 28, 29)])
        self.assertEqual(["2.0.0.110"] * 3, [node_of[i] for i in (25, 26, 30)])


class ConfigTests(unittest.TestCase):
    def load(self, body: str):
        with tempfile.TemporaryDirectory() as directory:
            return load_config(write_config(Path(directory), body))[0]

    def test_reference_config_keeps_reference_layout(self):
        config = self.load('[artnet]\nip = "192.0.2.20"\nuniverse = 4\n')
        self.assertEqual(DEFAULT_HARDWARE, config["hardware"])
        self.assertEqual({"channel": 469}, config["motor"])
        self.assertEqual([470], config["packet_sizes"])
        self.assertEqual([4], config["targets"][0]["universes"])

    def test_twenty_four_units_with_multi_universe_pixels(self):
        targets = "".join(
            f'[[artnet.targets]]\nunit_id = {unit}\nip = "2.0.0.{100 + unit}"\nuniverse = 0\n'
            for unit in range(1, 25)
        )
        config = self.load(
            'pixels = 312\nunit_count = 24\n'
            '[hardware]\ncolor_order = "GRB"\nbrightness = 0.5\n'
            '[artnet]\nsource_ip = "2.0.0.1"\n' + targets
        )
        self.assertEqual(24, config["unit_count"])
        self.assertEqual(937, config["motor"]["channel"])
        self.assertEqual([510, 428], config["packet_sizes"])
        self.assertEqual([[0, 1]] * 24, [target["universes"] for target in config["targets"]])

    def test_consecutive_universes_must_not_collide_on_one_ip(self):
        with self.assertRaisesRegex(ValueError, "consecutive"):
            self.load(
                'pixels = 312\nunit_count = 2\n[artnet]\n'
                '[[artnet.targets]]\nunit_id = 1\nip = "2.0.0.1"\nuniverse = 0\n'
                '[[artnet.targets]]\nunit_id = 2\nip = "2.0.0.1"\nuniverse = 1\n'
            )

    def test_validation_errors(self):
        cases = {
            'pixels = 0\n[artnet]\nip = "192.0.2.1"\n': "render.pixels",
            'unit_count = 65\n[artnet]\nip = "192.0.2.1"\n': "render.unit_count",
            '[artnet]\nip = "192.0.2.1"\n[hardware]\nchannels_per_universe = 512\n': "channels_per_universe",
            '[artnet]\nip = "192.0.2.1"\n[hardware]\nchannels_per_universe = 500\n': "multiple of 3",
            '[artnet]\nip = "192.0.2.1"\n[hardware]\ncolor_order = "RGBW"\n': "color_order",
            '[artnet]\nip = "192.0.2.1"\n[hardware]\nbrightness = 2\n': "brightness",
            '[artnet]\nip = "192.0.2.1"\n[hardware]\ngamma = 2\n': "unknown hardware",
            'pixels = 129\n[artnet]\nip = "192.0.2.1"\n[hardware]\npixel_rotation = 129\n': "pixel_rotation",
            '[artnet]\nip = "192.0.2.1"\n[hardware]\npixel_rotation = -1\n': "pixel_rotation",
            '[artnet]\nip = "192.0.2.1"\n[motor]\nchannel = 468\n': "motor.channel",
            '[artnet]\nip = "192.0.2.1"\n[motor]\nspeed = 1\n': "unknown motor",
            'pixels = 2000\n[artnet]\nip = "192.0.2.1"\n': "more than 8 universes",
            '[artnet]\nip = "192.0.2.1"\n[lights]\n': "sections",
        }
        for body, message in cases.items():
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, message):
                self.load(body)


class StreamTests(unittest.TestCase):
    def test_each_universe_gets_its_own_client_and_motor_stops_on_exit(self):
        clients = []
        now = [0.0]
        motor = [255]

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            clients.append(client)
            return client

        def sleep(seconds):
            now[0] += seconds

        hardware = {"color_order": "GRB", "brightness": 1.0, "channels_per_universe": 510}
        config = {
            "ip": "192.0.2.20", "source_ip": None, "universe": 16, "fps": 40, "rpm": 360,
            "pixels": 200, "unit_id": 1, "unit_count": 2, "hardware": hardware, "motor": {"channel": 601},
            "targets": [
                {"unit_id": 1, "ip": "192.0.2.21", "universe": 16, "universes": [16, 17]},
                {"unit_id": 2, "ip": "192.0.2.22", "universe": 16, "universes": [16, 17]},
            ],
        }
        preset = validate_preset(default_preset("full-on"), 200)
        sent = stream(config, preset, seconds=0.05, client_factory=factory,
                      motor_provider=lambda: motor[0], clock=lambda: now[0], sleep=sleep)
        self.assertEqual(2 * 2 * 2, sent)
        self.assertEqual([("192.0.2.21", 0, 510), ("192.0.2.21", 1, 92), ("192.0.2.22", 0, 510), ("192.0.2.22", 1, 92)],
                         [client.init[:3] for client in clients])
        self.assertEqual([(0, 1)] * 4, [(client.net, client.subnet) for client in clients])
        second = clients[1].packets
        self.assertEqual([255, 255, 0], [packet[600 - 510] for packet in second])  # last one is the exit stop
        self.assertTrue(all(client.closed for client in clients))


if __name__ == "__main__":
    unittest.main()
