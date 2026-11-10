"""Art-Net source address missing (e.g. eth0 unplugged): pause output, keep the page, resume when it is back.

來源位址不在本機時暫停 Art-Net、網頁照常，位址回來後自動繼續。
"""
import errno
from pathlib import Path
import tempfile
import unittest

from moon_observe_backend.output import (
    OutputStats, SOURCE_RETRY_SECONDS, SourceAddressLost, source_available, stream, stream_when_source_available,
)

from test_output_dmx import load
from test_send import FakeClient


class Harness:
    """Fake clients and clock; ``answers`` are the successive source_available results."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.checks = 0
        self.clients = []
        self.now = [0.0]
        self.waits = []
        self.lines = []

    def available(self, ip):
        assert ip == "2.0.0.10"
        self.checks += 1
        return self.answers.pop(0) if self.answers else True

    def factory(self, *args, **kwargs):
        client = FakeClient(*args, **kwargs)
        self.clients.append(client)
        return client

    def sleep(self, seconds):
        # 至少前進 1 ms：假時鐘的浮點誤差會讓極小的 sleep 加不上去而卡住。
        # At least 1 ms: with float rounding a tiny sleep could leave the fake clock unchanged forever.
        self.now[0] += max(seconds, 0.001)

    def run(self, config, preset, stats=None, seconds=0.1):
        return stream_when_source_available(
            config, preset, stats=stats, available=self.available, wait=self.waits.append, log=self.lines.append,
            seconds=seconds, client_factory=self.factory, clock=lambda: self.now[0], sleep=self.sleep,
        )


class SourceWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        config, cls.preset = load(Path(cls.directory.name))
        cls.config = {**config, "source_ip": "2.0.0.10"}
        # 轉燈 client（沒有 targets 時只有一台）＋ 2 個 DMX node。 / Spinner clients (one without targets) + 2 DMX nodes.
        cls.per_round = len(config.get("targets") or [None]) + len(config["dmx"]["nodes"])

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_probe(self):
        self.assertTrue(source_available("127.0.0.1"))
        self.assertFalse(source_available("192.0.2.123"))  # TEST-NET-1: never an address of the test host

    def test_waits_for_the_address_then_sends(self):
        harness = Harness([False, False, False])
        stats = OutputStats(40)
        seen = []
        original = stats.set_link
        stats.set_link = lambda state, ip: (seen.append((state, ip, stats.snapshot().get("artnet"))), original(state, ip))
        sent = harness.run(self.config, self.preset, stats)
        self.assertEqual([SOURCE_RETRY_SECONDS] * 3, harness.waits)
        self.assertEqual(["waiting", "sending"], [state for state, _, _ in seen])
        self.assertEqual(2, len(harness.lines))
        self.assertIn("Art-Net paused: 2.0.0.10", harness.lines[0])
        self.assertIn("again; sending", harness.lines[1])
        self.assertEqual(4 * self.per_round, sent)                      # 4 frames in 0.1 s at 40 FPS
        self.assertEqual(self.per_round, len(harness.clients))        # one set of clients, made once the address is up
        self.assertEqual(("2.0.0.10", 0), harness.clients[0].init[3]["source_address"])
        self.assertEqual({"state": "sending", "sourceIp": "2.0.0.10"}, stats.snapshot()["artnet"])
        dmx = harness.clients[-1]
        self.assertEqual([bytes(512)] * 2, dmx.packets[-2:])          # normal exit: DMX blackout

    def test_address_lost_mid_stream_pauses_and_resumes(self):
        # 位址在第 3 格前消失，等兩次後回來。 / The address leaves before frame 3 and is back after two waits.
        harness = Harness([True, True, True, True, False, False, False, True])
        stats = OutputStats(40)
        harness.run(self.config, self.preset, stats)
        self.assertEqual(2 * self.per_round, len(harness.clients))   # clients rebuilt after the pause
        first = harness.clients[:self.per_round]
        self.assertTrue(all(client.closed for client in first))
        self.assertEqual([3] * self.per_round, [len(client.packets) for client in first])  # no blackout / motor stop
        self.assertEqual([SOURCE_RETRY_SECONDS] * 2, harness.waits)
        self.assertIn("left this host", harness.lines[0])
        self.assertIn("Art-Net paused", harness.lines[1])
        self.assertIn("again; sending", harness.lines[2])
        self.assertEqual("sending", stats.snapshot()["artnet"]["state"])

    def test_waiting_clears_frame_statistics(self):
        stats = OutputStats(40, window=1.0)
        stats.record(0.0, 0, 0.001)
        self.assertTrue(stats.snapshot()["active"])
        stats.set_link("waiting", "2.0.0.10")
        self.assertEqual({"active": False, "artnet": {"state": "waiting", "sourceIp": "2.0.0.10"}}, stats.snapshot())

    def test_bind_failure_while_building_clients(self):
        made = []

        def factory(*args, **kwargs):
            if len(made) == 2:
                raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
            made.append(FakeClient(*args, **kwargs))
            return made[-1]

        with self.assertRaises(SourceAddressLost):
            stream(self.config, self.preset, seconds=0.1, client_factory=factory)
        self.assertTrue(all(client.closed and not client.packets for client in made))

    def test_without_source_ip_streams_directly(self):
        harness = Harness([False])
        stats = OutputStats(40)
        harness.run({**self.config, "source_ip": None}, self.preset, stats)
        self.assertEqual(0, harness.checks)
        self.assertEqual({"state": "sending", "sourceIp": None}, stats.snapshot()["artnet"])


if __name__ == "__main__":
    unittest.main()
