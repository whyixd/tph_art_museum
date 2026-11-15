import json
import os
import re
import select
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from moon_observe_backend import modbus
from moon_observe_backend.anemometer import AnemometerProbe, PORT_PATTERN, decode
from moon_observe_backend.config import load_config
from moon_observe_backend.control import PresetStore, make_server
from test_output_dmx import load

ANY_PORT = re.compile(r".+")
# hsz 文件裡的要求：位址 1、從 reg 0 讀 4 個。 / The request from the hsz documents: address 1, 4 registers from 0.
REQUEST = bytes.fromhex("01 03 00 00 00 04 44 09")


def reply(registers, address=1):
    body = bytes((address, 3, 2 * len(registers))) + b"".join(value.to_bytes(2, "big") for value in registers)
    crc = modbus.crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def exception_reply(code, address=1):
    body = bytes((address, 0x83, code))
    crc = modbus.crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


class FakeAnemometer:
    """pty 另一端的假風速計：收到要求後依 ``chunks`` 延遲、分段回應。 / A fake sensor on the pty's other end."""

    def __init__(self, chunks):
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.chunks = chunks
        self.request = b""
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        deadline = time.monotonic() + 5
        while len(self.request) < len(REQUEST) and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                self.request += os.read(self.master, 64)
        for delay, data in self.chunks:
            time.sleep(delay)
            os.write(self.master, data)

    def close(self):
        self.thread.join(5)
        os.close(self.master)
        os.close(self.slave)


@unittest.skipUnless(hasattr(os, "openpty"), "needs a pty")
class SerialTest(unittest.TestCase):
    def exchange(self, chunks, timeout=1.0):
        device = FakeAnemometer(chunks)
        try:
            fd = modbus.open_port(device.path, 9600)
            try:
                result = modbus.read_holding_registers(fd, 1, 0, 4, timeout)
            finally:
                os.close(fd)
        finally:
            device.close()
        self.assertEqual(REQUEST, device.request)
        return result

    def test_reply_in_pieces_is_collected(self):
        frame = reply([53, 3, 2250, 8])
        result = self.exchange([(0.05, frame[:4]), (0.1, frame[4:9]), (0.1, frame[9:])])
        self.assertEqual((53, 3, 2250, 8), result.frame.registers)
        self.assertEqual(frame, result.received)
        self.assertGreater(result.round_trip_seconds, 0.2)

    def test_no_reply_waits_out_the_timeout(self):
        started = time.monotonic()
        result = self.exchange([], timeout=0.3)
        self.assertGreaterEqual(time.monotonic() - started, 0.29)
        self.assertEqual(b"", result.received)
        self.assertIsNone(result.frame)
        self.assertIsNone(result.round_trip_seconds)

    def test_leading_noise_is_skipped(self):
        result = self.exchange([(0.02, b"\x00\xff" + reply([10, 1, 900, 4]))])
        self.assertEqual(2, result.frame.offset)
        self.assertEqual((10, 1, 900, 4), result.frame.registers)

    def test_exception_reply(self):
        result = self.exchange([(0.02, exception_reply(2))])
        self.assertIsNone(result.frame.registers)
        self.assertEqual(2, result.frame.exception_code)

    def test_probe_reports_values(self):
        device = FakeAnemometer([(0.02, reply([53, 3, 2250, 10]))])
        lines = []
        try:
            result = AnemometerProbe(port_pattern=ANY_PORT, log=lines.append).probe({"port": device.path})
        finally:
            device.close()
        self.assertTrue(result["ok"])
        self.assertEqual("01 03 00 00 00 04 44 09", result["sent"])
        self.assertEqual({"speedMs": 5.3, "beaufort": 3, "directionDeg": 225.0, "directionCode": 10,
                          "codeMatches": True, "plausible": True}, result["values"])
        self.assertIsNone(result["detail"])
        self.assertIn("wind probe: ok", lines[0])

    def test_probe_classifies_failures(self):
        cases = [
            ([], "no-response", 0),
            ([(0.02, reply([53, 3, 2250, 10])[:6])], "incomplete", 6),
            ([(0.02, b"\x55" * 13)], "bad-frame", 13),
            ([(0.02, exception_reply(2))], "exception", 5),
        ]
        for chunks, kind, count in cases:
            with self.subTest(kind):
                device = FakeAnemometer(chunks)
                try:
                    result = AnemometerProbe(port_pattern=ANY_PORT, log=lambda line: None).probe(
                        {"port": device.path, "timeoutSeconds": 0.3})
                finally:
                    device.close()
                self.assertFalse(result["ok"])
                self.assertEqual(kind, result["kind"])
                self.assertEqual(count, result["bytes"])
        self.assertIn("暫存器位址不對", result["detail"])


class ModbusTest(unittest.TestCase):
    def test_request_matches_the_known_packet(self):
        self.assertEqual(REQUEST, modbus.build_read_request(1, 0, 4))
        self.assertEqual(13, modbus.response_length(4))

    def test_find_frame(self):
        frame = reply([1, 2, 3, 4])
        self.assertEqual((1, 2, 3, 4), modbus.find_frame(frame, 1, 4).registers)
        self.assertIsNone(modbus.find_frame(frame[:-1], 1, 4))
        self.assertIsNone(modbus.find_frame(frame[:-1] + b"\x00", 1, 4))  # CRC 錯 / bad CRC
        self.assertIsNone(modbus.find_frame(reply([1, 2, 3, 4], address=2), 1, 4))  # 別的位址 / another address

    def test_decode_checks_the_direction_code(self):
        self.assertTrue(decode((0, 0, 3595, 0))["codeMatches"])  # 359.5° ≈ N
        self.assertTrue(decode((0, 0, 100, 15))["codeMatches"])  # 10° 與 NNW 只差一格 / one sector off
        self.assertFalse(decode((0, 0, 1800, 0))["plausible"])  # 180° 卻是 N / 180° but N
        self.assertFalse(decode((700, 0, 0, 0))["plausible"])  # 70 m/s


class ProbeTest(unittest.TestCase):
    def test_port_pattern(self):
        for port in ("/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_B0029TYY-if00-port0", "/dev/ttyUSB0",
                     "/dev/ttyACM1", "/dev/cu.usbserial-A10K"):
            self.assertTrue(AnemometerProbe()._request({"port": port}))
        for port in ("/etc/passwd", "/dev/serial/by-id/..", "/dev/serial/by-id/../../sda", "/dev/sda", "/dev/ttyUSB0x"):
            with self.subTest(port), self.assertRaises(ValueError):
                AnemometerProbe()._request({"port": port})
        self.assertIsNotNone(PORT_PATTERN.fullmatch("/dev/ttyAMA0"))

    def test_request_validation(self):
        probe = AnemometerProbe({"port": "/dev/ttyUSB0", "baudrate": 4800, "address": 2, "timeout_seconds": 3.0})
        self.assertEqual(("/dev/ttyUSB0", 4800, 2, 3.0), probe._request({}))
        for bad in ({"baudrate": 9601}, {"address": 0}, {"timeoutSeconds": 20}, {"extra": 1}, {"port": ""}):
            with self.subTest(bad), self.assertRaises(ValueError):
                probe._request(bad)

    def test_missing_port(self):
        result = AnemometerProbe(log=lambda line: None).probe({"port": "/dev/ttyUSB250"})
        self.assertEqual("no-port", result["kind"])
        self.assertIn("沒插好", result["message"])

    def test_one_read_at_a_time(self):
        probe = AnemometerProbe(port_pattern=ANY_PORT, log=lambda line: None)
        probe._lock.acquire()
        with self.assertRaises(ValueError):
            probe.probe({"port": "/dev/null"})

    def test_defaults_pick_a_detected_port(self):
        probe = AnemometerProbe()
        probe.ports = lambda: ["/dev/serial/by-id/usb-a", "/dev/ttyUSB0"]
        self.assertEqual("/dev/serial/by-id/usb-a", probe.state()["defaults"]["port"])
        self.assertFalse(probe.state()["configured"])


class ConfigAndApiTest(unittest.TestCase):
    def test_config_section(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            load(root)
            path = root / "config.toml"
            base = path.read_text(encoding="utf-8")
            self.assertIsNone(load_config(path)[0]["wind"])
            path.write_text(base + '[wind]\nport = "/dev/ttyUSB0"\nbaudrate = 4800\naddress = 3\ntimeout_seconds = 1.5\n',
                            encoding="utf-8")
            self.assertEqual({"port": "/dev/ttyUSB0", "baudrate": 4800, "address": 3, "timeout_seconds": 1.5},
                             load_config(path)[0]["wind"])
            for extra in ('baudrate = 1000\n', 'poll_seconds = 5\n', 'address = 300\n'):
                path.write_text(base + '[wind]\n' + extra, encoding="utf-8")
                with self.subTest(extra), self.assertRaises(ValueError):
                    load_config(path)

    def test_api(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = load(Path(directory))
            store = PresetStore(Path(directory) / "preset.json", 129)
            probe = AnemometerProbe({"port": "/dev/ttyUSB250"}, log=lambda line: None)
            with make_server(store, config, 0, anemometer=probe) as server:
                Thread(target=server.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def call(method, body=None):
                    data = None if body is None else json.dumps(body).encode()
                    request = Request(base + "/api/wind/probe", data=data, method=method,
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        return json.load(response)

                state = call("GET")
                self.assertEqual("/dev/ttyUSB250", state["defaults"]["port"])
                self.assertTrue(state["configured"])
                self.assertEqual("no-port", call("PUT", {})["kind"])
                with self.assertRaises(HTTPError) as caught:
                    call("PUT", {"port": "/etc/passwd"})
                self.assertEqual(422, caught.exception.code)
                server.shutdown()

            with make_server(store, config, 0) as server:
                Thread(target=server.serve_forever, daemon=True).start()
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/wind/probe")
                self.assertEqual(404, caught.exception.code)
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
