import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from moon_observe_backend.config import load_config
from moon_observe_backend.dmx import build_universes, color_levels, fixture_bytes, patch_report
from moon_observe_backend.effects import default_preset
from moon_observe_backend.output import main

BALLS = '''
[[dmx.profiles]]
name = "ball-rgbw"
channels = ["r", "g", "b", "w"]

[[dmx.nodes]]
name = "ball-node"
ip = "192.0.2.200"
universe = 100

[[dmx.patch]]
group = "ball"
first_id = 1
count = 37
profile = "ball-rgbw"
node = "ball-node"
address = 1
'''
WASHERS = '''
[[dmx.profiles]]
name = "wash-rgbw"
channels = ["r", "g", "b", "w"]

[[dmx.nodes]]
name = "wash"
ip = "192.0.2.201"
universe = 0

[[dmx.patch]]
group = "wash"
first_id = 1
count = 3
profile = "wash-rgbw"
node = "wash"
address = 1
'''


def write_config(root: Path, dmx: str) -> Path:
    (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
    path = root / "config.toml"
    path.write_text('[artnet]\nip = "192.0.2.20"\nuniverse = 0\n[render]\npreset = "preset.json"\n' + dmx,
                    encoding="utf-8")
    return path


def load_dmx(dmx: str):
    with tempfile.TemporaryDirectory() as directory:
        return load_config(write_config(Path(directory), dmx))[0]["dmx"]


BACKEND = Path(__file__).resolve().parents[1]


def load_with_layout(layout) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        return load_config(write_config(root, 'layout = "layout.json"\n' + BALLS))[0]["dmx"]


class LayoutTests(unittest.TestCase):
    def test_example_layout_gives_ball_heights(self):
        layout = json.loads((BACKEND / "layout.example.json").read_text(encoding="utf-8"))
        dmx = load_with_layout(layout)
        self.assertEqual({"x": 961.0, "y": 202.0, "z": 340.0}, dmx["positions"]["ball"][0])
        heights = [row[0] for row in dmx["geometry"]["ball"]]
        self.assertEqual(0.0, heights[10])        # ball 11, H245 (lowest; ball 17 too)
        self.assertEqual(1.0, heights[35])        # ball 36, H1030
        self.assertAlmostEqual((340 - 245) / (1030 - 245), heights[0])
        xs = [row[1] for row in dmx["geometry"]["ball"]]
        self.assertEqual((0.0, 1.0), (xs[3], xs[36]))  # ball 4 is leftmost (x 926), ball 37 rightmost (x 3551)
        ys = [row[2] for row in dmx["geometry"]["ball"]]
        self.assertEqual((0.0, 1.0), (ys[21], ys[6]))  # ball 22 nearest the front wall, ball 7 deepest
        # 範例 layout 有 25 顆洗牆燈，這份設定沒接：忽略。 / The layout's 25 washers are not patched here: ignored.
        self.assertEqual([], dmx["geometry"]["wash"])

    def test_example_layout_wall_washers_along_the_back_wall(self):
        layout = json.loads((BACKEND / "layout.example.json").read_text(encoding="utf-8"))
        washers = WASHERS.replace("count = 3", "count = 25")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
            dmx = load_config(write_config(root, 'layout = "layout.json"\n' + BALLS + washers))[0]["dmx"]
        positions = dmx["positions"]["wash"]
        self.assertEqual(25, len(positions))
        self.assertEqual({"x": 75.0, "y": 933.0, "z": 20.0}, positions[0])
        self.assertEqual([150.0] * 24, [b["x"] - a["x"] for a, b in zip(positions, positions[1:])])
        geometry = dmx["geometry"]["wash"]
        self.assertEqual((0.5, 0.0, 0.5), geometry[0])   # same height and depth: 0.5; x from left to right
        self.assertEqual((0.5, 1.0, 0.5), geometry[24])

    def test_spinner_positions_by_unit(self):
        layout = json.loads((BACKEND / "layout.example.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
            config = load_config(write_config(root, 'unit_count = 24\nlayout = "layout.json"\n'))[0]  # no [dmx]
        self.assertIsNone(config["dmx"])
        self.assertEqual(24, len(config["spinner_positions"]))
        self.assertEqual({"x": 961.0, "y": 118.0, "z": 565.0}, config["spinner_positions"][0])
        self.assertEqual({"x": 3370.0, "y": 349.0, "z": 885.0}, config["spinner_positions"][23])
        # 中心擴散的水平流動：與前端 fixtureGeometry 算出的位置相同。 / Flow positions match the frontend's fixtureGeometry.
        flow = json.loads((BACKEND / "fixtures" / "effects_v2.json").read_text(encoding="utf-8"))["flowPositions"]["positions"]
        self.assertEqual([(entry["x"], entry["y"]) for entry in flow], config["spinner_flow"])
        self.assertEqual(15, len(config["poles"]))
        self.assertEqual({"x": 961.0, "y": 169.0, "z": 1000.0}, config["poles"][0])

    def test_layout_ids_beyond_the_patch_are_ignored(self):
        dmx = load_with_layout({"schemaVersion": 1, "fixtures": {"ball": {"38": {"z": 1}, "1": {"z": 2}}}})
        self.assertEqual({"z": 2.0}, dmx["positions"]["ball"][0])
        self.assertEqual(37, len(dmx["positions"]["ball"]))

    def test_without_layout_every_axis_follows_order(self):
        dmx = load_dmx(BALLS)
        self.assertEqual([None] * 37, dmx["positions"]["ball"])
        self.assertEqual((0.5, 0.5, 0.5), dmx["geometry"]["ball"][18])

    def test_layout_errors(self):
        cases = {
            "schemaVersion 1": {"schemaVersion": 2, "fixtures": {}},
            "not units 1..64": {"schemaVersion": 1, "spinners": {"0": {"x": 1}}},
            "spinner 2 must have": {"schemaVersion": 1, "spinners": {"2": {}}},
            "pole 1 needs x, y and z": {"schemaVersion": 1, "poles": [{"x": 1, "y": 2}]},
            "at most 200": {"schemaVersion": 1, "poles": [{"x": 1, "y": 1, "z": 1}] * 201},
            "keyed by group": {"schemaVersion": 1, "fixtures": {"spinner": {}}},
            "ids x, 01 are not fixture ids": {"schemaVersion": 1, "fixtures": {"ball": {"x": {"z": 1}, "01": {"z": 1}}}},
            "some of x, y, z": {"schemaVersion": 1, "fixtures": {"ball": {"1": {"h": 1}}}},
            r"ball 1\.z": {"schemaVersion": 1, "fixtures": {"ball": {"1": {"z": "high"}}}},
        }
        for message, layout in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                load_with_layout(layout)


class ColorTests(unittest.TestCase):
    def test_white_extraction(self):
        self.assertEqual({"r": 0.0, "g": 0.0, "b": 0.0, "w": 1.0}, color_levels((1.0, 1.0, 1.0), "min"))
        levels = color_levels((1.0, 0.5, 0.25), "min")
        self.assertEqual({"r": 0.75, "g": 0.25, "b": 0.0, "w": 0.25}, levels)
        self.assertEqual({"r": 1.0, "g": 0.5, "b": 0.25, "w": 0.0}, color_levels((1.0, 0.5, 0.25), "none"))
        self.assertEqual({"r": 0.0, "g": 0.0, "b": 0.5, "w": 0.0}, color_levels((-1.0, 0.0, 2.0), "min", 0.5))

    def test_profile_order_and_static_channels(self):
        profile = {"channels": ("dimmer", "w", "b", "g", "r", "strobe"), "white": "min", "brightness": 1.0,
                   "static": {"dimmer": 255, "strobe": 7}}
        # floor(x * 255 + 0.5): 0.75 -> 191, 0.25 -> 64.
        self.assertEqual(bytes([255, 64, 0, 64, 191, 7]), fixture_bytes((1.0, 0.5, 0.25), profile))


class PatchTests(unittest.TestCase):
    def test_thirty_seven_rgbw_balls_fill_one_universe(self):
        dmx = load_dmx(BALLS)
        balls = dmx["fixtures"]["ball"]
        self.assertEqual(list(range(1, 38)), [ball["id"] for ball in balls])
        self.assertEqual([1, 5, 9], [ball["address"] for ball in balls[:3]])
        self.assertEqual(145, balls[-1]["address"])
        self.assertEqual([], dmx["fixtures"]["wash"])
        colors = [(1.0, 0.0, 0.0)] * 36 + [(1.0, 1.0, 1.0)]
        [frame] = build_universes({"ball": colors}, dmx)
        self.assertEqual(512, len(frame))
        self.assertEqual(bytes([255, 0, 0, 0]), frame[0:4])
        self.assertEqual(bytes([0, 0, 0, 255]), frame[144:148])
        self.assertEqual(bytes(512 - 148), frame[148:])
        self.assertEqual([bytes(512)], build_universes({}, dmx))
        with self.assertRaisesRegex(ValueError, "37 colours"):
            build_universes({"ball": colors[:3]}, dmx)

    def test_wash_on_second_node_and_explicit_step(self):
        dmx = load_dmx(BALLS + '''
[[dmx.profiles]]
name = "wash"
channels = ["dimmer", "r", "g", "b", "strobe"]
static = { dimmer = 255, strobe = 0 }
brightness = 0.5

[[dmx.nodes]]
name = "wash-node"
ip = "192.0.2.201"
universe = 0

[[dmx.patch]]
group = "wash"
first_id = 1
count = 3
profile = "wash"
node = "wash-node"
address = 11
address_step = 10
''')
        self.assertEqual([11, 21, 31], [wash["address"] for wash in dmx["fixtures"]["wash"]])
        self.assertEqual("none", dmx["profiles"]["wash"]["white"])
        frames = build_universes({"wash": [(1.0, 1.0, 1.0)] * 3}, dmx)
        self.assertEqual(bytes(512), frames[0])
        self.assertEqual(bytes([255, 128, 128, 128, 0]), frames[1][20:25])
        lines = list(patch_report(dmx))
        self.assertIn("ball-node  192.0.2.200  universe 100", lines)
        self.assertIn("  ball  37  ch 145-148  ball-rgbw (r g b w)", lines)
        self.assertIn("  wash   2  ch  21- 25  wash (dimmer r g b strobe)", lines)

    def test_validation_errors(self):
        cases = {
            BALLS.replace("address = 1\n", "address = 1\naddress_step = 1\n"): "overlap ball 1",
            BALLS.replace("address = 1\n", "address = 400\n"): "exceed 512",
            BALLS.replace('universe = 100', 'universe = 0').replace("192.0.2.200", "192.0.2.20"): "spinner",
            BALLS.replace('"ball-rgbw"\nnode', '"rgb"\nnode'): "not a dmx profile",
            BALLS.replace('node = "ball-node"', 'node = "nope"'): "not a dmx node",
            BALLS.replace('group = "ball"', 'group = "spot"'): "group",
            BALLS.replace('["r", "g", "b", "w"]', '["r", "g", "w"]'): "include r, g and b",
            BALLS.replace('["r", "g", "b", "w"]', '["r", "g", "b", "w", "dimmer"]'): "static must give a value for exactly: dimmer",
            BALLS.replace('["r", "g", "b", "w"]', '["r", "g", "b"]\nwhite = "min"'): "needs a w channel",
            BALLS + '[[dmx.nodes]]\nname = "ball-node"\nip = "192.0.2.202"\nuniverse = 1\n': "name 'ball-node' repeats",
            BALLS + '[[dmx.nodes]]\nname = "other"\nip = "192.0.2.200"\nuniverse = 100\n': "repeats another node",
            BALLS + '[[dmx.patch]]\ngroup = "ball"\nfirst_id = 37\ncount = 1\nprofile = "ball-rgbw"\n'
                    'node = "ball-node"\naddress = 200\n': "ball id 37 repeats",
            BALLS.replace("[[dmx.profiles]]", "[dmx]\nfixtures = 1\n[[dmx.profiles]]"): "unknown dmx config field",
        }
        for body, message in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                load_dmx(body)

    def test_no_dmx_section(self):
        self.assertIsNone(load_dmx(""))

    def test_patch_report_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(Path(directory), BALLS)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["--config", str(path), "--patch-report"]))
        self.assertIn("  ball   1  ch   1-  4  ball-rgbw (r g b w)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
