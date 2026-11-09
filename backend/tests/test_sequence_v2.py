import json
from pathlib import Path
import tempfile
import unittest

from moon_observe_backend.effects import default_preset
from moon_observe_backend.fixture_effects import default_fixture_preset, fixture_geometry, render_fixture_frame, validate_fixture_preset
from moon_observe_backend.sequence import (
    SequenceStore, compile_sequence, render_sequence_fixtures, render_sequence_frame, validate_sequence,
)

UNIT = {"unitId": 1, "logicalPixelCount": 156, "frameOffset": 0}
BALLS = fixture_geometry([{"z": z} for z in (340, 430, 675, 890, 370)])


def fixture(effect_id, **parameters):
    preset = default_fixture_preset(effect_id)
    preset["parameters"].update(parameters)
    return preset


def v1():
    return {
        "schemaVersion": 1, "ending": {"kind": "loop"},
        "steps": [
            {"id": 1, "preset": default_preset("center-out-sine"), "playback": {"kind": "count", "count": 2},
             "transition": {"kind": "crossfade", "seconds": 0.5}},
            {"id": 2, "preset": default_preset("full-on"), "playback": {"kind": "time", "seconds": 3},
             "transition": {"kind": "unit-fade-black", "seconds": 2}},
        ],
    }


def v2():
    return {
        "schemaVersion": 2, "ending": {"kind": "loop"},
        "steps": [
            {"id": 1, "spinner": default_preset("full-on"), "ball": fixture("fixture-breathing", breatheHz=0.2),
             "playback": {"kind": "count", "count": 2, "clock": "ball"},
             "transition": {"kind": "crossfade", "seconds": 0.5}},
            {"id": 2, "spinner": default_preset("full-on"), "ball": fixture("fixture-solid"), "wash": fixture("fixture-solid"),
             "playback": {"kind": "time", "seconds": 3}, "transition": {"kind": "unit-fade-black", "seconds": 2}},
        ],
    }


class SequenceV2Tests(unittest.TestCase):
    def test_v1_is_upgraded_and_renders_the_same(self):
        document = validate_sequence(v1())
        self.assertEqual(2, document["schemaVersion"])
        self.assertEqual(["id", "spinner", "playback", "transition"], list(document["steps"][0]))
        self.assertEqual(document, validate_sequence(document))  # canonical v2 is a fixed point
        old, new = compile_sequence(v1(), 40, 9), compile_sequence(document, 40, 9)
        for frame in (0, 150, 229, 230, 245, 349, 350, 400, 430):  # 2 × 115 + 120 frames, looping
            self.assertEqual(render_sequence_frame(old, UNIT, frame, unit_count=9, fps=40, rpm=360),
                             render_sequence_frame(new, UNIT, frame, unit_count=9, fps=40, rpm=360))
        # v1 steps have no ball/wash: both groups stay dark.
        self.assertEqual([(0.0, 0.0, 0.0)] * 5, render_sequence_fixtures(new, "ball", BALLS, 10, fps=40))

    def test_canonical_v2_keeps_fixture_presets(self):
        document = validate_sequence(v2())
        self.assertEqual(validate_fixture_preset(v2()["steps"][0]["ball"])["parameters"],
                         document["steps"][0]["ball"]["parameters"])
        self.assertNotIn("wash", document["steps"][0])
        self.assertEqual({"kind": "count", "count": 2, "clock": "ball"}, document["steps"][0]["playback"])
        self.assertEqual(document, validate_sequence(json.loads(json.dumps(document))))

    def test_clock_group_sets_duration_and_only_it_wraps(self):
        compiled = compile_sequence(v2(), 40, 9)
        self.assertEqual(400, compiled[0]["duration"])  # breathing 0.2 Hz = 200 frames, twice
        self.assertEqual({"spinner": None, "ball": 200, "wash": None}, compiled[0]["group_cycles"])
        self.assertEqual("ball", compiled[0]["clock"])
        self.assertIsNone(compiled[1]["clock"])
        # Ball frame 250 of the step plays as frame 50 of its cycle.
        breathing = validate_fixture_preset(v2()["steps"][0]["ball"])
        self.assertEqual(render_fixture_frame(breathing, BALLS, 50, fps=40),
                         render_sequence_fixtures(compiled, "ball", BALLS, 250, fps=40))

    def test_crossfade_and_unit_fade_for_fixtures(self):
        compiled = compile_sequence(v2(), 40, 9)
        # Step 2 starts at 400 with the 0.5 s (20 frame) crossfade from step 1.
        solid = render_sequence_fixtures(compiled, "wash", fixture_geometry([{"z": 1}]), 409, fps=40)
        self.assertAlmostEqual(10 / 21, solid[0][0])   # wash only in step 2: just the incoming weight
        # Loop back to step 1 at 520 through unit-fade-black over 80 frames: balls go dark in chain order.
        fading = render_sequence_fixtures(compiled, "ball", BALLS, 520 + 20, fps=40)
        levels = [color[0] for color in fading]
        self.assertEqual(sorted(levels), levels)        # fixture 1 fades first
        self.assertEqual(0.0, levels[0])
        self.assertGreater(levels[-1], 0)

    def test_weather_passes_through(self):
        compiled = compile_sequence(v2(), 40, 9)
        palette = {"mode": "gradient", "axis": "radius", "colors": [{"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 0, "b": 1}]}
        solid = validate_fixture_preset(fixture("fixture-solid"))
        self.assertEqual(
            render_fixture_frame(solid, BALLS, 100, fps=40, raining=True, color_override=palette, frame_step=6),
            render_sequence_fixtures(compiled, "ball", BALLS, 500, fps=40, rain=True, color_override=palette, frame_step=6),
        )

    def test_validation_errors(self):
        def broken(change):
            raw = v2()
            change(raw["steps"][0])
            return raw

        cases = {
            "needs a wash preset": broken(lambda step: step["playback"].update(clock="wash")),
            "clock must be": broken(lambda step: step["playback"].update(clock="moon")),
            "completion boundary": broken(lambda step: step.update(ball=fixture("fixture-twinkle"))),
            "optional: ball, wash": broken(lambda step: step.update(lights={})),
            "unknown fixture effectId": broken(lambda step: step.update(ball=default_preset("full-on") | {"schemaVersion": 1})),
            "spinner": broken(lambda step: step.pop("spinner")),
        }
        legacy = v1()
        legacy["steps"][0]["ball"] = fixture("fixture-solid")
        cases["id, preset, playback and transition"] = legacy
        mixed = v2()
        mixed["schemaVersion"] = 3
        cases["schemaVersion"] = mixed
        for message, raw in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_sequence(raw)

    def test_chase_clock_uses_fixture_count(self):
        raw = v2()
        raw["steps"][0]["ball"] = fixture("fixture-chase", stepSeconds=0.25)
        self.assertEqual(2 * 10 * 37, compile_sequence(raw, 40, 9, fixture_counts={"ball": 37})[0]["duration"])
        self.assertEqual(2 * 10, compile_sequence(raw, 40, 9)[0]["duration"])  # unknown count -> 1

    def test_fallback_step_includes_fixture_presets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sequence.json"
            ball = fixture("height-sweep")
            store = SequenceStore(path, 156, 40, 9, default_preset("full-on"), {"ball": 37}, {"ball": ball, "wash": None})
            step = store.state()["sequence"]["steps"][0]
            self.assertEqual(["id", "spinner", "ball", "playback", "transition"], list(step))
            self.assertEqual(validate_sequence(json.loads(path.read_text(encoding="utf-8"))), store.state()["sequence"])

    def test_store_reads_v1_file_as_v2_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sequence.json"
            path.write_text(json.dumps(v1()), encoding="utf-8")
            before = path.read_bytes()
            store = SequenceStore(path, 156, 40, 9, default_preset("full-on"), {"ball": 37})
            self.assertEqual(2, store.state()["sequence"]["schemaVersion"])
            self.assertEqual(before, path.read_bytes())
            store.apply(v2())
            self.assertEqual(validate_sequence(v2()), json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
