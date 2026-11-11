import json
from pathlib import Path
import tempfile
import unittest

from moon_observe_backend.config import load_config
from moon_observe_backend.control import PresetStore
from moon_observe_backend.effects import default_preset
from moon_observe_backend.motor import MotorGate

CONFIG = {
    "ip": "127.0.0.1", "source_ip": None, "universe": 0,
    "fps": 40, "rpm": 360, "pixels": 156, "unit_id": 1, "unit_count": 3,
}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def frames(gate, clock, desired, seconds, step=0.025):
    """Run the gate for `seconds` of frames; returns the last output."""
    end = clock.now + seconds
    output = None
    while clock.now < end - 1e-9:
        output = gate.apply(desired)
        clock.now += step
    return output


class MotorGateTests(unittest.TestCase):
    """XIAO 緩停中收到的啟動會被忽略：停止後滿 restart_delay 才送 255。 / Starts wait out the XIAO soft stop."""

    def setUp(self):
        self.clock = Clock()
        self.gate = MotorGate(12.0, clock=self.clock)

    def test_first_start_waits_from_the_first_frame(self):
        self.assertEqual((0, 0, 0), self.gate.apply((255, 0, 0)))
        self.assertEqual((0, 0, 0), frames(self.gate, self.clock, (255, 0, 0), 11.9))
        self.assertEqual((255, 0, 0), frames(self.gate, self.clock, (255, 0, 0), 0.2))

    def test_restart_within_the_stop_is_held_then_sent(self):
        frames(self.gate, self.clock, (0, 0, 0), 20)
        self.assertEqual((255, 255, 0), self.gate.apply((255, 255, 0)))       # long stopped: at once
        frames(self.gate, self.clock, (255, 255, 0), 30)
        frames(self.gate, self.clock, (0, 0, 0), 5)                            # 全部緩停 for 5 s
        self.assertEqual((0, 0, 0), self.gate.apply((255, 255, 0)))          # 全部緩啟動: held
        self.assertEqual([7.0, 7.0, None], self.gate.waiting([True, True, False]))
        self.assertEqual((0, 0, 0), frames(self.gate, self.clock, (255, 255, 0), 6.9))
        self.assertEqual((255, 255, 0), frames(self.gate, self.clock, (255, 255, 0), 0.2))
        self.assertEqual([None, None, None], self.gate.waiting([True, True, False]))

    def test_units_are_independent(self):
        frames(self.gate, self.clock, (0, 0, 0), 20)
        frames(self.gate, self.clock, (255, 255, 255), 1)
        frames(self.gate, self.clock, (255, 0, 255), 3)                       # unit 2 stops
        self.assertEqual((255, 0, 255), self.gate.apply((255, 255, 255)))     # 1, 3 keep running; 2 held
        self.assertEqual((255, 255, 255), frames(self.gate, self.clock, (255, 255, 255), 9.1))

    def test_a_gap_past_the_wled_timeout_counts_as_a_stop(self):
        frames(self.gate, self.clock, (0, 0, 0), 20)
        frames(self.gate, self.clock, (255, 0, 0), 1)
        self.clock.now += 0.9                                                  # short stall: keeps running
        self.assertEqual((255, 0, 0), self.gate.apply((255, 0, 0)))
        self.clock.now += 1.0                                                  # Art-Net paused 1 s: WLED stopped it
        self.assertEqual([12.0, None, None], self.gate.waiting([True, False, False]))
        self.assertEqual((0, 0, 0), self.gate.apply((255, 0, 0)))
        self.assertEqual((255, 0, 0), frames(self.gate, self.clock, (255, 0, 0), 12.1))

    def test_a_start_due_now_is_not_reported_as_held(self):
        frames(self.gate, self.clock, (0, 0, 0), 20)
        self.assertEqual([None, None, None], self.gate.waiting([True, True, False]))  # sent with the next frame

    def test_no_output_loop_reports_nothing(self):
        self.assertEqual([None, None], self.gate.waiting([True, False]))


class StartGroupTests(unittest.TestCase):
    """全部緩啟動時每秒放行一個控箱。 / One control box is released per second."""

    def setUp(self):
        self.clock = Clock()
        # 6 台：控箱 A = 3、4，B = 1、2；5、6 沒有列入（各自一組，排最後）。
        self.gate = MotorGate(12.0, start_interval=1.0, start_groups=[[3, 4], [1, 2]], clock=self.clock)
        frames(self.gate, self.clock, (0,) * 6, 20)

    def started_at(self, desired, seconds):
        """Seconds from now at which each unit was first sent 255 (None = never)."""
        start = self.clock.now
        first: list = [None] * len(desired)
        while self.clock.now < start + seconds - 1e-9:
            for index, value in enumerate(self.gate.apply(desired)):
                if value == 255 and first[index] is None:
                    first[index] = round(self.clock.now - start, 3)
            self.clock.now += 0.025
        return first

    def test_all_start_one_group_per_second_in_order(self):
        everything = (255,) * 6
        self.gate.apply((0,) * 6)
        self.assertEqual([1.0, 1.0, None, None, 2.0, 3.0], self.gate.waiting([True] * 6))
        self.assertEqual([1.0, 1.0, 0.0, 0.0, 2.0, 3.0], self.started_at(everything, 5))

    def test_a_single_unit_starts_at_once(self):
        self.assertEqual([None, None, None, None, 0.0, None], self.started_at((0, 0, 0, 0, 255, 0), 1))

    def test_restart_after_stop_still_spreads_the_groups(self):
        everything = (255,) * 6
        self.started_at(everything, 5)
        frames(self.gate, self.clock, (0,) * 6, 5)                      # 全部緩停, 5 s later 全部緩啟動
        self.assertEqual([8.0, 8.0, 7.0, 7.0, 9.0, 10.0], self.gate.waiting([True] * 6))
        self.assertEqual([8.0, 8.0, 7.0, 7.0, 9.0, 10.0], self.started_at(everything, 12))

    def test_stops_are_not_delayed(self):
        everything = (255,) * 6
        self.started_at(everything, 5)
        self.assertEqual((0,) * 6, self.gate.apply((0,) * 6))


class MotorConfigTests(unittest.TestCase):
    def load(self, motor):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("full-on")), encoding="utf-8")
            (root / "config.toml").write_text('[artnet]\nip = "192.0.2.20"\nuniverse = 0\n[render]\npreset = "preset.json"\n'
                                              'pixels = 156\n[motor]\nchannel = 469\n' + motor, encoding="utf-8")
            return load_config(root / "config.toml")[0]["motor"]

    def test_restart_delay(self):
        self.assertEqual({"channel": 469}, self.load(""))
        self.assertEqual({"channel": 469, "restart_delay_seconds": 15.0}, self.load("restart_delay_seconds = 15\n"))
        for bad in ("-1", "121", '"12"'):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "restart_delay_seconds"):
                self.load(f"restart_delay_seconds = {bad}\n")

    def test_start_groups(self):
        motor = self.load('start_interval_seconds = 1\n[[motor.start_groups]]\nname = "控箱 101"\nunits = [3, 4]\n'
                          '[[motor.start_groups]]\nunits = [1]\n')
        self.assertEqual(1.0, motor["start_interval_seconds"])
        self.assertEqual([{"name": "控箱 101", "units": [3, 4]}, {"name": "2", "units": [1]}], motor["start_groups"])
        for bad, message in (("[[motor.start_groups]]\nunits = [10]\n", "units"),
                             ("[[motor.start_groups]]\nunits = []\n", "units"),
                             ("[[motor.start_groups]]\nunits = [1]\n[[motor.start_groups]]\nunits = [1]\n", "already"),
                             ("[[motor.start_groups]]\nunit = [1]\n", "requires units"),
                             ("start_interval_seconds = 11\n", "start_interval_seconds")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                self.load(bad)


class StoreMotorOutputTests(unittest.TestCase):
    def test_state_reports_waiting_and_output_is_gated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preset.json"
            path.write_text(json.dumps(default_preset("full-on")), encoding="utf-8")
            clock = Clock()
            store = PresetStore(path, 156, motor_gate=MotorGate(12.0, clock=clock))
            self.assertEqual([None] * 3, store.state(CONFIG)["motor"]["waiting"])
            store.apply_motor({"enabled": True, "units": [2]}, CONFIG)
            self.assertEqual((0, 255, 0), store.motor_values(3))                 # what was asked
            self.assertEqual((0, 0, 0), store.motor_output(3))                   # what is sent
            for _ in range(160):                                                 # 4 s of frames
                clock.now += 0.025
                store.motor_output(3)
            self.assertEqual([None, 8.0, None], store.state(CONFIG)["motor"]["waiting"])
            self.assertNotIn("waiting", PresetStore(path, 156).state(CONFIG)["motor"])


if __name__ == "__main__":
    unittest.main()
