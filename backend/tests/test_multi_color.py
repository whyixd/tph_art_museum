import json
import unittest
from pathlib import Path

from moon_observe_backend.effects import (
    EffectContext, default_preset, multi_color_at, preset_to_json, same_on_every_unit, sample_effect, validate_preset,
)
from moon_observe_backend.fixture_effects import (
    FixtureContext, default_fixture_preset, fixture_preset_to_json, sample_fixture, validate_fixture_preset,
)

# 前端 tests/multiColor.test.ts 產生的向量。 / Vectors written by the frontend's tests/multiColor.test.ts.
VECTORS = Path(__file__).resolve().parents[1] / "fixtures" / "multi_color_v1.json"
RED, GREEN, BLUE = {"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 1, "b": 0}, {"r": 0, "g": 0, "b": 1}


def multi(**patch):
    return {"mode": "multi", "colors": [RED, GREEN, BLUE], "order": "sequence", "per": "point",
            "stepSeconds": 0.5, "fadeSeconds": 0, **patch}


class MultiColorParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = json.loads(VECTORS.read_text(encoding="utf-8"))

    def test_spinner_samples_match_the_frontend(self):
        data = self.vectors
        presets = [validate_preset(preset, data["ledCount"]) for preset in data["spinnerPresets"]]
        self.assertGreater(len(data["spinnerSamples"]), 1000)
        for sample in data["spinnerSamples"]:
            context = EffectContext(
                controller_frame=sample["frame"] + sample["unitIndex"] * 3, controller_fps=data["controllerFps"],
                led_index=sample["ledIndex"], led_count=data["ledCount"], unit_index=sample["unitIndex"],
                unit_count=data["unitCount"], global_controller_frame=sample["frame"],
            )
            actual = sample_effect(presets[sample["presetIndex"]], context)
            for channel, value in zip(("r", "g", "b"), actual):
                self.assertAlmostEqual(sample["expected"][channel], value, places=10, msg=sample)

    def test_fixture_samples_match_the_frontend(self):
        data = self.vectors
        presets = [validate_fixture_preset(preset) for preset in data["fixturePresets"]]
        self.assertGreater(len(data["fixtureSamples"]), 100)
        for sample in data["fixtureSamples"]:
            index = sample["fixtureIndex"]
            context = FixtureContext(
                controller_frame=sample["frame"], controller_fps=data["controllerFps"], fixture_index=index,
                fixture_count=data["fixtureCount"], normalized_height=index / 36,
            )
            actual = sample_fixture(presets[sample["presetIndex"]], context)
            for channel, value in zip(("r", "g", "b"), actual):
                self.assertAlmostEqual(sample["expected"][channel], value, places=10, msg=sample)


class MultiColorTests(unittest.TestCase):
    def preset(self, **patch):
        return validate_preset({**default_preset("full-on"), "color": multi(**patch)})

    def test_sequence_shifts_one_colour_every_step(self):
        color = self.preset()["color"]
        red, green, blue = color["colors"]
        self.assertEqual([red, green, blue, red], [multi_color_at(color, point, point, 0, 40) for point in range(4)])
        self.assertEqual(red, multi_color_at(color, 0, 0, 19, 40))
        self.assertEqual(green, multi_color_at(color, 0, 0, 20, 40))
        fixed = self.preset(stepSeconds=0)["color"]
        self.assertEqual(blue, multi_color_at(fixed, 2, 2, 99999, 40))

    def test_fade_blends_from_the_previous_colour(self):
        color = self.preset(fadeSeconds=0.25)["color"]
        self.assertEqual((0.5, 0.5, 0.0), multi_color_at(color, 0, 0, 25, 40))
        self.assertEqual(color["colors"][1], multi_color_at(color, 0, 0, 30, 40))

    def test_ring_leds_share_a_colour_and_units_can_differ(self):
        point = self.preset(order="random")
        unit = self.preset(per="unit")

        def at(preset, led, unit_index):
            return sample_effect(preset, EffectContext(0, 40, led, 129, unit_index, 9))

        self.assertEqual(at(point, 5, 2), at(point, 123, 2))
        self.assertEqual({at(unit, led, 1) for led in range(129)}, {(0.0, 1.0, 0.0)})
        self.assertEqual([(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)], [at(unit, 0, index) for index in range(3)])

    def test_only_sequence_per_point_is_the_same_on_every_unit(self):
        self.assertTrue(same_on_every_unit(self.preset(), False))
        self.assertFalse(same_on_every_unit(self.preset(order="random"), False))
        self.assertFalse(same_on_every_unit(self.preset(per="unit"), False))

    def test_round_trips_and_rejects_bad_fields(self):
        raw = {**default_preset("full-on"), "color": multi(fadeSeconds=0.2)}
        self.assertEqual(raw["color"], preset_to_json(validate_preset(raw))["color"])
        fixture_color = {key: value for key, value in multi().items() if key != "per"}
        fixture = {**default_fixture_preset("fixture-solid"), "color": fixture_color}
        self.assertEqual(fixture_color, fixture_preset_to_json(validate_fixture_preset(fixture))["color"])
        for bad in (multi(order="shuffle"), multi(per="led"), multi(colors=[RED]), multi(stepSeconds=-1),
                    multi(fadeSeconds=61), {key: value for key, value in multi().items() if key != "per"}):
            with self.assertRaises(ValueError):
                validate_preset({**default_preset("full-on"), "color": bad})
        with self.assertRaises(ValueError):
            validate_fixture_preset({**default_fixture_preset("fixture-solid"), "color": multi()})


if __name__ == "__main__":
    unittest.main()
