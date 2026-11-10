import hashlib
import json
import unittest
from pathlib import Path

from moon_observe_backend.config import fixture_poles
from moon_observe_backend.fixture_effects import (
    FIXTURE_EFFECT_IDS,
    FixtureContext,
    default_fixture_preset,
    fixture_completion_frames,
    fixture_geometry,
    fixture_preset_to_json,
    flow_distance,
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

    def test_pole_rise_matches_frontend_with_layout_poles(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))["poleRise"]
        poles = fixture_poles(data["positions"], data["poles"])
        self.assertEqual([(entry["rank"], entry["step"]) for entry in data["expectedPoles"]], poles)
        self.assertEqual(14, max(rank for rank, _ in poles) + 1)
        geometry = fixture_geometry(data["positions"])
        for case in data["cases"]:
            preset = validate_fixture_preset(case["preset"])
            frames = b"".join(to_bytes(render_fixture_frame(preset, geometry, frame, fps=40, poles=poles)) for frame in range(700))
            self.assertEqual(case["sha256"], hashlib.sha256(frames).hexdigest(), case["preset"]["parameters"])

    def test_horizontal_flow_moves_colours_along_the_direction(self):
        raw = default_fixture_preset("horizontal-flow")
        raw["parameters"]["depth"] = 0
        raw["color"] = {"mode": "gradient", "axis": "height", "colors": [{"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 0, "b": 1}]}
        preset = validate_fixture_preset(raw)
        self.assertEqual(200, fixture_completion_frames(preset, 40, 3))
        geometry = fixture_geometry([{"x": x, "y": 0, "z": 300} for x in (0, 500, 1000)])
        first = render_fixture_frame(preset, geometry, 0)
        self.assertEqual((1.0, 0.0, 0.0), first[0])
        self.assertEqual((0.0, 0.0, 1.0), first[1])
        self.assertAlmostEqual(1.0, render_fixture_frame(preset, geometry, 100)[0][2], places=12)

    def test_horizontal_flow_accelerates_smoothly(self):
        raw = default_fixture_preset("horizontal-flow")
        raw["parameters"].update(acceleration=3, rampSeconds=10)
        fast = validate_fixture_preset(raw)["parameters"]
        self.assertAlmostEqual(4.0, flow_distance(fast, 10), places=12)
        self.assertAlmostEqual(5.2, flow_distance(fast, 12), places=12)
        self.assertEqual(400, fixture_completion_frames(validate_fixture_preset(raw), 40, 3))

    def test_base_wave_keeps_base_and_adds_sine(self):
        preset = validate_fixture_preset(default_fixture_preset("base-wave"))
        self.assertEqual(134, fixture_completion_frames(preset, 40, 3))
        geometry = fixture_geometry([{"z": z} for z in (0, 500, 1000)])
        levels = [color[0] for frame in range(134) for color in render_fixture_frame(preset, geometry, frame)]
        self.assertGreaterEqual(min(levels), 0.3 - 1e-12)
        self.assertLessEqual(max(levels), 0.79 + 1e-12)
        first = render_fixture_frame(preset, geometry, 0)
        self.assertAlmostEqual(0.79, first[0][0], places=12)
        self.assertAlmostEqual(0.3, first[1][0], places=12)

    def test_pole_rise_lights_each_pole_bottom_up_then_fades(self):
        preset = validate_fixture_preset(default_fixture_preset("pole-rise"))
        self.assertEqual(320, fixture_completion_frames(preset, 40, 6))
        # Two poles: fixtures 0, 1 (low, high) on pole 0 and 2, 3 on pole 1.
        geometry = fixture_geometry([{"z": z} for z in (100, 300, 100, 300)])
        poles = [(0, 0), (0, 1), (1, 0), (1, 1)]

        def levels(frame):
            return [color[0] for color in render_fixture_frame(preset, geometry, frame, poles=poles)]

        # Step 0.2 s (8 frames), attack 0.1 s (4), pole stagger 0.3 s (12), decay 2.5 s (100).
        self.assertEqual([0.25, 0.0, 0.0, 0.0], levels(0))
        # Frame 8: the low ball is 5 frames into its decay ((1 - 5/100)^2); the one above it starts.
        self.assertAlmostEqual(0.9025, levels(8)[0], places=12)
        self.assertEqual([0.25, 0.0, 0.0], levels(8)[1:])
        self.assertEqual(0.25, levels(12)[2])
        self.assertTrue(0 < levels(60)[0] < levels(60)[1] < 1)
        self.assertEqual([0.0] * 4, levels(319))

    def test_rise_lights_from_lowest_and_stays_lit(self):
        preset = validate_fixture_preset(default_fixture_preset("fixture-rise"))
        geometry = fixture_geometry([{"z": z} for z in (1000, 0, 500)])
        self.assertIsNone(fixture_completion_frames(preset, 40, 3))

        def levels(target, frame):
            return [color[0] for color in render_fixture_frame(target, geometry, frame)]

        self.assertTrue(0 < levels(preset, 0)[1] < 0.01)
        self.assertEqual(0.0, levels(preset, 199)[2])
        self.assertEqual(0.0, levels(preset, 399)[0])
        self.assertEqual([1.0, 1.0, 1.0], levels(preset, 519))
        self.assertEqual([1.0, 1.0, 1.0], levels(preset, 40000))
        looping = validate_fixture_preset({**default_fixture_preset("fixture-rise"),
                                           "parameters": {**default_fixture_preset("fixture-rise")["parameters"], "repeat": 1}})
        cycle = (10 + 3 + 5 + 3 + 1) * 40
        self.assertEqual(cycle, fixture_completion_frames(looping, 40, 3))
        self.assertEqual([0.0, 0.0, 0.0], levels(looping, cycle - 1))

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
