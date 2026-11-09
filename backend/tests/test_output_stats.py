import unittest

from moon_observe_backend.effects import default_preset, validate_preset
from moon_observe_backend.output import OutputStats, stream

from test_send import FakeClient

CONFIG = {
    "ip": "192.0.2.20", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360,
    "pixels": 129, "unit_id": 1, "unit_count": 1,
}


class OutputStatsTests(unittest.TestCase):
    def test_window_summary_and_log_line(self):
        stats = OutputStats(40, window=1.0)
        self.assertEqual({"active": False}, stats.snapshot())
        self.assertIsNone(stats.record(0.0, 0, 0.004))
        self.assertEqual({"active": True, "partial": True, "windowSeconds": 0.0, "frames": 1, "skippedFrames": 0,
                          "renderMs": {"avg": 4.0, "max": 4.0}, "budgetMs": 25.0}, stats.snapshot())
        self.assertIsNone(stats.record(0.5, 2, 0.030))
        line = stats.record(1.0, 0, 0.002)
        self.assertEqual("output: 3 frames in 1 s, 2 skipped, render 12.0 ms avg / 30.0 ms max (budget 25.0 ms)", line)
        self.assertEqual({"active": True, "windowSeconds": 1.0, "frames": 3, "skippedFrames": 2,
                          "renderMs": {"avg": 12.0, "max": 30.0}, "budgetMs": 25.0}, stats.snapshot())
        self.assertIsNone(stats.record(1.5, 0, 0.001))  # a new window starts; the snapshot keeps the last full one
        self.assertEqual(3, stats.snapshot()["frames"])

    def test_stream_counts_frames_it_falls_behind_on(self):
        # Every clock reading moves 0.1 s on (4 frames at 40 FPS), as if each frame took that long.
        now = [0.0]

        def clock():
            now[0] += 0.1
            return now[0]

        stats = OutputStats(40, window=100)
        sent = stream(CONFIG, validate_preset(default_preset("full-on"), 129), seconds=1.0,
                      client_factory=FakeClient, clock=clock, sleep=lambda _seconds: None, stats=stats)
        snapshot = stats.snapshot()
        self.assertEqual(sent, snapshot["frames"])
        self.assertGreater(snapshot["skippedFrames"], 0)
        self.assertGreaterEqual(snapshot["renderMs"]["max"], 99)


if __name__ == "__main__":
    unittest.main()
