import hashlib
import json
import unittest
from pathlib import Path

from moon_observe_backend.fixture_effects import (
    FIXTURE_EFFECT_IDS,
    FixtureContext,
    default_fixture_preset,
    fixture_completion_frames,
    fixture_geometry,
    fixture_preset_to_json,
    render_fixture_frame,
    sample_fixture,
    validate_fixture_preset,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fixtures_v1.json"


def to_bytes(colors):
    return bytes(int(min(1.0, max(0.0, channel)) * 255 + 0.5) for color in colors for channel in color)


class FixtureParityTests(unittest.TestCase):
    """前端 src/fixtures 產生的向量（scripts/generateFixtureVectors.ts）與後端逐值一致。"""

    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.presets = [validate_fixture_preset(preset) for preset in cls.fixture["presets"]]

    def test_every_effect_is_covered(self):
        self.assertEqual(set(FIXTURE_EFFECT_IDS), {preset["effectId"] for preset in self.presets})

    def test_geometry_matches(self):
        heights = self.fixture["ballHeights"]
        expected = self.fixture["geometries"]["balls"]
        actual = fixture_geometry([{"z": z} for z in heights])
        for row, want in zip(actual, expected, strict=True):
            for value, target in zip(row, want, strict=True):
                self.assertAlmostEqual(target, value, places=12)

    def test_samples(self):
        self.assertGreater(len(self.fixture["samples"]), 1000)
        for sample in self.fixture["samples"]:
            data = sample["context"]
            context = FixtureContext(
                controller_frame=data["controllerFrame"], controller_fps=data["controllerFps"],
                fixture_index=data["fixtureIndex"], fixture_count=data["fixtureCount"],
                normalized_height=data["normalizedHeight"], normalized_x=data["normalizedX"],
                normalized_y=data["normalizedY"],
            )
            actual = sample_fixture(self.presets[sample["presetIndex"]], context, sample["raining"])
            for channel, value in zip(("r", "g", "b"), actual):
                self.assertAlmostEqual(sample["expected"][channel], value, places=9, msg=sample)

    def test_group_frames(self):
        geometries = {name: [tuple(row) for row in rows] for name, rows in self.fixture["geometries"].items()}
        weathers = {weather["name"]: weather for weather in self.fixture["weathers"]}
        palette = self.fixture["palette"]
        for case in self.fixture["groupFrames"]:
            preset = self.presets[case["presetIndex"]]
            geometry = geometries[case["geometry"]]
            weather = weathers[case["weather"]]
            cycle = fixture_completion_frames(preset, case["fps"], len(geometry))
            self.assertEqual(case["cycleFrames"], cycle, case)
            data = b"".join(
                to_bytes(render_fixture_frame(
                    preset, geometry, frame, fps=case["fps"], raining=weather["raining"],
                    color_override=palette if weather["palette"] else None,
                    frame_step=weather["frameStep"], cycle_frames=cycle,
                ))
                for frame in range(240)
            )
            self.assertEqual(case["sha256"], hashlib.sha256(data).hexdigest(), case)


class FixturePresetTests(unittest.TestCase):
    def test_defaults_round_trip(self):
        for effect_id in FIXTURE_EFFECT_IDS:
            preset = validate_fixture_preset(default_fixture_preset(effect_id))
            self.assertEqual(preset, validate_fixture_preset(fixture_preset_to_json(preset)))

    def test_validation_errors(self):
        base = default_fixture_preset("height-sweep")
        cases = {
            "schemaVersion": {**base, "schemaVersion": 2},
            "unknown fixture effectId": {**base, "effectId": "center-out-sine"},
            "parameters": {**base, "parameters": {"litCount": 3}},
            "width": {**base, "parameters": {"width": 2}},
            "direction": {**base, "parameters": {"direction": 0.5}},
            "axis": {**base, "color": {"mode": "gradient", "axis": "radius", "colors": [base["color"]["color"]] * 2}},
            "maxLevel": {**base, "maxLevel": 1.5},
            "optional: maxLevel": {**base, "unitVariation": {}},
        }
        for message, raw in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_fixture_preset(raw)

    def test_geometry_falls_back_to_order(self):
        geometry = fixture_geometry([{"z": 100, "x": 0}, {"z": 300}, {"z": 200, "x": 10}])
        self.assertEqual([0.0, 1.0, 0.5], [row[0] for row in geometry])   # z on every fixture
        self.assertEqual([0.0, 0.5, 1.0], [row[1] for row in geometry])   # x missing once -> order
        self.assertEqual([(0.5, 0.5, 0.5)], fixture_geometry([None]))

    def test_height_sweep_goes_up_then_down(self):
        preset = validate_fixture_preset({**default_fixture_preset("height-sweep"), "parameters": {"speedHz": 0.25}})
        geometry = fixture_geometry([{"z": z} for z in (0, 500, 1000)])

        def peak(frame):
            colors = render_fixture_frame(preset, geometry, frame)
            return max(range(3), key=lambda index: colors[index][0])

        self.assertEqual([0, 1, 2], [peak(frame) for frame in (20, 80, 140)])  # 160-frame cycle, bottom to top
        down = validate_fixture_preset({**default_fixture_preset("height-sweep"), "parameters": {"direction": 1}})
        self.assertEqual(2, max(range(3), key=lambda index: render_fixture_frame(down, geometry, 20)[index][0]))

    def test_firework_bursts_from_lowest_to_highest(self):
        preset = validate_fixture_preset(default_fixture_preset("fixture-firework"))
        geometry = fixture_geometry([{"z": z} for z in (0, 500, 1000)])
        self.assertEqual(200, fixture_completion_frames(preset, 40, 3))

        def levels(frame):
            return [color[0] for color in render_fixture_frame(preset, geometry, frame)]

        self.assertEqual([1.0, 0.0, 0.0], levels(0))
        self.assertEqual((1.0, 0.0), (levels(40)[1], levels(40)[2]))
        self.assertIn(round(levels(40)[0], 2), (0.0, 0.36))
        self.assertEqual(1.0, levels(80)[2])
        self.assertEqual([0.0, 0.0, 0.0], levels(190))
        self.assertEqual([1.0, 0.0, 0.0], levels(200))

    def test_max_level_and_off(self):
        solid = validate_fixture_preset({**default_fixture_preset("fixture-solid"), "maxLevel": 0.5})
        self.assertEqual([(0.5, 0.5, 0.5)], render_fixture_frame(solid, [(0.5, 0.5, 0.5)], 0))
        off = validate_fixture_preset(default_fixture_preset("off"))
        self.assertEqual([(0.0, 0.0, 0.0)], render_fixture_frame(off, [(0.5, 0.5, 0.5)], 0, raining=True))


if __name__ == "__main__":
    unittest.main()
