import unittest

from moon_observe_backend.config import DEFAULT_HARDWARE
from moon_observe_backend.effects import (
    EFFECT_IDS, UNIT_INDEPENDENT_EFFECTS, default_preset, same_on_every_unit, validate_preset,
)
from moon_observe_backend.hardware import build_packets
from moon_observe_backend.output import stream
from moon_observe_backend.renderer import render_frame

from test_send import FakeClient

UNITS = 24
PIXELS = 129
FRAMES = (0, 1, 7, 40, 133, 401, 2650)
COLORS = (
    None,
    {"mode": "gradient", "axis": "strip", "colors": [{"r": 1, "g": 0, "b": 0}, {"r": 0, "g": 0, "b": 1}]},
    {"mode": "gradient", "axis": "radius", "colors": [{"r": 0, "g": 1, "b": 0}, {"r": 1, "g": 1, "b": 1}],
     "axisChange": {"to": "strip", "atSeconds": 1, "blendSeconds": 0.5, "repeat": True}},
)


def preset_for(effect_id, color=None):
    raw = default_preset(effect_id, PIXELS)
    if color is not None:
        raw["color"] = color
    return validate_preset(raw, PIXELS)


def unit(index):
    return {"unitId": index + 1, "logicalPixelCount": PIXELS, "frameOffset": 0}


class UnitSharingTests(unittest.TestCase):
    """輸出迴圈只算一次的效果，逐台渲染必須完全相同。 / Effects rendered once must really be identical on every unit."""

    def test_listed_effects_render_the_same_on_every_unit(self):
        for effect_id in UNIT_INDEPENDENT_EFFECTS:
            for color in COLORS:
                preset = preset_for(effect_id, color)
                self.assertTrue(same_on_every_unit(preset, False), effect_id)
                for frame in FRAMES:
                    first = render_frame(preset, unit(0), frame, unit_count=UNITS)
                    for index in range(1, UNITS):
                        self.assertEqual(first, render_frame(preset, unit(index), frame, unit_count=UNITS),
                                         f"{effect_id} {color and color['axis']} frame {frame} unit {index + 1}")

    def test_other_effects_differ_between_units(self):
        for effect_id in sorted(set(EFFECT_IDS) - set(UNIT_INDEPENDENT_EFFECTS)):
            preset = preset_for(effect_id)
            self.assertFalse(same_on_every_unit(preset, False), effect_id)
            # 某個時間點至少有兩台不同。 / At some frame at least two units differ.
            differs = any(
                len({tuple(render_frame(preset, {**unit(i), "virtualPhaseRadians": i * 0.7}, frame, unit_count=UNITS))
                     for i in range(UNITS)}) > 1
                for frame in (*FRAMES, 60, 200, 800)
            )
            self.assertTrue(differs, effect_id)

    def test_exclusions(self):
        base = preset_for("full-on")
        self.assertFalse(same_on_every_unit(base, True))  # rain lines are seeded per unit
        unit_axis = preset_for("full-on", {"mode": "gradient", "axis": "unit", "colors": COLORS[1]["colors"]})
        self.assertFalse(same_on_every_unit(unit_axis, False))
        varied = validate_preset({**default_preset("center-out-sine"), "unitVariation": {
            "parameter": "speedHz", "from": 0.2, "to": 1, "distribution": "linear"}}, PIXELS)
        self.assertFalse(same_on_every_unit(varied, False))

    def test_output_loop_packets_match_unshared_renders(self):
        targets = [{"unit_id": index + 1, "ip": "192.0.2.20", "universe": index, "universes": [index]}
                   for index in range(UNITS)]
        config = {"ip": "192.0.2.20", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360,
                  "pixels": PIXELS, "unit_id": 1, "unit_count": UNITS, "targets": targets,
                  "hardware": DEFAULT_HARDWARE, "motor": {"channel": 469}}
        for effect_id in ("center-out-sine", "unit-chase"):
            clients = []
            now = [0.0]

            def factory(*args, **kwargs):
                client = FakeClient(*args, **kwargs)
                clients.append(client)
                return client

            def sleep(seconds):
                now[0] += seconds

            preset = preset_for(effect_id)
            stream(config, preset, seconds=0.1, client_factory=factory, clock=lambda: now[0], sleep=sleep)
            for index, client in enumerate(clients):
                for frame, packet in enumerate(client.packets):
                    expected = build_packets(render_frame(preset, unit(index), frame, unit_count=UNITS),
                                             hardware=DEFAULT_HARDWARE, motor_channel=469, motor_value=0)[0]
                    self.assertEqual(expected, packet, f"{effect_id} unit {index + 1} frame {frame}")


if __name__ == "__main__":
    unittest.main()
