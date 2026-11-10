import unittest

from moon_observe_backend.config import pillar_ranks


class PillarRankTests(unittest.TestCase):
    def test_spinners_without_a_nearby_pole_come_last(self):
        poles = [{"x": 0.0, "y": 0.0, "z": 800.0}]
        spinners = [{"x": 500.0, "y": 0.0, "z": 100.0}, {"x": 50.0, "y": 0.0, "z": 600.0}, None,
                    {"x": 0.0, "y": 50.0, "z": 300.0}]
        self.assertEqual([2, 1, 3, 0], pillar_ranks(spinners, poles))


if __name__ == "__main__":
    unittest.main()
