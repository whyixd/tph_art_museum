import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

WLED_DIR = Path(__file__).resolve().parents[1] / "wled"
_spec = importlib.util.spec_from_file_location("flash", WLED_DIR / "flash.py")
flash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flash)

VERSION = "v0.15.4"
MAC = "209ba98c63dc"
GOOD_CFG = {"eth": {"type": 13, "pin": flash.ETH_PINS}, "nw": {"name": "unit"}, "rev": [1, 0]}
ESPTOOL_FLASH_ID = (f"Chip is ESP32-D0WD-V3 (revision v3.1)\nMAC: 20:9b:a9:8c:63:dc\n"
                    "Detected flash size: 4MB\n")


def merged(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merged(target[key], value)
        else:
            target[key] = value


class FakeWled:
    """WLED's HTTP side: reboots into firmware with the usermod after a successful OTA."""

    def __init__(self, mac=MAC):
        self.info = {"ver": "0.15.4", "release": "ESP32_Ethernet", "mac": mac, "live": False, "uptime": 5000}
        self.cfg = json.loads(json.dumps(GOOD_CFG))
        self.presets = b'{"0":{}}'
        self.post_status = 200
        self.posts = []
        self.boot_at = None
        self.motor_after_boot = {"output": "stop", "reason": "no-signal", "pin": 13}
        self.saves_settings = True
        self.settings_status = 200

    def paths(self):
        return [post[0] for post in self.posts]

    def reboot_now(self):
        self.boot_at = time.monotonic()
        self.info["moonMotor"] = self.motor_after_boot

    def serve(self, path):
        if path == "/json/info":
            info = dict(self.info)
            if self.boot_at is not None:
                info["uptime"] = int(time.monotonic() - self.boot_at)
            return json.dumps(info).encode()
        if path == "/cfg.json":
            return json.dumps(self.cfg).encode()
        if path == "/presets.json":
            return self.presets
        return None

    def start(self, test):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = fake.serve(self.path)
                self.send_response(404 if body is None else 200)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.posts.append((self.path, self.headers["Content-Type"], body))
                if self.path != "/update":
                    status = fake.settings_status if self.path == "/json/cfg" else 200
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    if status == 200 and self.path == "/json/cfg" and fake.saves_settings:
                        merged(fake.cfg, json.loads(body))
                    if self.path == "/json/state" and json.loads(body).get("rb"):
                        fake.reboot_now()
                    return
                reply = b"<h2>Update failed!</h2>" if fake.post_status != 200 else b"ok"
                self.send_response(fake.post_status)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
                if fake.post_status == 200:
                    fake.reboot_now()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        test.addCleanup(server.server_close)
        test.addCleanup(server.shutdown)
        return f"127.0.0.1:{server.server_address[1]}"


class FakeEsptool:
    def __init__(self, partitions, flash_id=ESPTOOL_FLASH_ID):
        self.partitions = partitions
        self.flash_id = flash_id
        self.commands = []
        self.written = {}
        self.on_write = None

    def __call__(self, port, *command):
        self.commands.append(command)
        if command[0] == "flash_id":
            return self.flash_id
        if command[0] == "read_flash":
            data = self.partitions if command[1] == hex(flash.PARTITIONS_OFFSET) else b"\0" * int(command[2], 16)
            Path(command[3]).write_bytes(data)
            return ""
        if command[0] == "write_flash":
            pairs = zip(command[1::2], command[2::2])
            self.written = {offset: Path(path).read_bytes() for offset, path in pairs}
            if self.on_write:
                self.on_write()
            return ""
        raise AssertionError(f"unexpected esptool command: {command}")


class FlashTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.image_path = self.root / "WLED_v0.15.4_moon_eth.bin"
        self.image = b"\xe9" + b"x" * 300 + b"moonMotor ESP32_Ethernet" + b"y" * 300
        self.image_path.write_bytes(self.image)
        self.partitions = b"P" * flash.PARTITIONS_SIZE
        (self.root / "partitions_reference.bin").write_bytes(self.partitions)
        for patcher in (mock.patch.object(flash, "BACKUP_DIR", self.root / "backups"),
                        mock.patch.object(flash, "POLL_INTERVAL_S", 0.02),
                        mock.patch.object(flash, "SETTINGS_SAVE_TIMEOUT_S", 0.2),
                        mock.patch.object(flash, "REBOOT_TIMEOUT_S", 1)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, *argv, answer="yes"):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", return_value=answer), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = flash.main([*argv, "--bin", str(self.image_path)])
        return code, out.getvalue(), err.getvalue()

    def backups(self):
        return sorted((self.root / "backups").glob("*")) if (self.root / "backups").exists() else []


class ImageTests(FlashTestCase):
    def test_rejects_wrong_images(self):
        cases = {"missing": None, "not an app": b"\x00moonMotor ESP32_Ethernet",
                 "no usermod": b"\xe9 ESP32_Ethernet", "other release": b"\xe9 moonMotor ESP32",
                 "too big": b"\xe9moonMotor ESP32_Ethernet" + b"x" * flash.APP_SIZE}
        for name, data in cases.items():
            with self.subTest(name):
                path = self.root / "image.bin"
                path.unlink(missing_ok=True)
                if data is not None:
                    path.write_bytes(data)
                with self.assertRaises(flash.FlashError):
                    flash.check_image(path)


class OtaTests(FlashTestCase):
    def test_flashes_backs_up_and_verifies(self):
        fake = FakeWled()
        host = fake.start(self)
        code, out, err = self.run_main("ota", host)
        self.assertEqual(code, 0, err)
        self.assertIn(hashlib.sha256(self.image).hexdigest(), out)
        path, content_type, body = fake.posts[0]
        self.assertEqual(path, "/update")
        self.assertIn("multipart/form-data", content_type)
        self.assertIn(b'name="update"', body)
        self.assertIn(self.image, body)
        self.assertNotIn(b"skipValidation", body)
        folder = self.backups()[0]
        self.assertEqual((folder / "presets.json").read_bytes(), fake.presets)
        self.assertEqual(json.loads((folder / "cfg.json").read_text(encoding="utf-8")), GOOD_CFG)
        self.assertEqual(json.loads((folder / "info.json").read_text(encoding="utf-8"))["mac"], MAC)

    def test_precheck_refusals_write_nothing(self):
        cases = {
            "wrong eth type": lambda f: f.cfg["eth"].update(type=0),
            "wrong eth pins": lambda f: f.cfg["eth"].update(pin=[1]),
            "wrong version": lambda f: f.info.update(ver="0.15.3"),
            "wrong release": lambda f: f.info.update(release="ESP32"),
            "stream running": lambda f: f.info.update(live=True),
            "motor running": lambda f: f.info.update(moonMotor={"output": "run"}),
            "no mac": lambda f: f.info.update(mac=""),
        }
        for name, spoil in cases.items():
            with self.subTest(name):
                fake = FakeWled()
                spoil(fake)
                code, _out, err = self.run_main("ota", fake.start(self))
                self.assertEqual(code, 1)
                self.assertIn("refusing to flash", err)
                self.assertEqual(fake.posts, [])

    def test_unreachable_unit_is_an_error(self):
        code, _out, err = self.run_main("ota", "127.0.0.1:9")
        self.assertEqual(code, 1)
        self.assertIn("cannot read the unit", err)

    def test_one_bad_unit_stops_the_batch_before_any_write(self):
        good, bad = FakeWled(), FakeWled(mac="aaaaaaaaaaaa")
        bad.cfg["eth"]["type"] = 0
        code, _out, _err = self.run_main("ota", good.start(self), bad.start(self))
        self.assertEqual(code, 1)
        self.assertEqual(good.posts, [])

    def test_failure_stops_before_the_next_unit(self):
        first, second = FakeWled(), FakeWled(mac="aaaaaaaaaaaa")
        first.post_status = 500
        second_host = second.start(self)
        code, out, err = self.run_main("ota", first.start(self), second_host)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", err)
        self.assertIn("Update failed!", err)
        self.assertIn(f"Not flashed: {second_host}", out)
        self.assertEqual(second.posts, [])

    def test_units_flash_in_order(self):
        first, second = FakeWled(), FakeWled(mac="aaaaaaaaaaaa")
        code, _out, err = self.run_main("ota", first.start(self), second.start(self))
        self.assertEqual(code, 0, err)
        self.assertEqual(first.paths(), ["/update", "/json/cfg", "/json/state"])
        self.assertEqual(second.paths(), ["/update", "/json/cfg", "/json/state"])

    def test_same_unit_twice_is_refused(self):
        first, second = FakeWled(), FakeWled()
        code, _out, err = self.run_main("ota", first.start(self), second.start(self))
        self.assertEqual(code, 1)
        self.assertIn("same unit", err)
        self.assertEqual(first.posts + second.posts, [])

    def test_pin_or_ota_lock_is_explained(self):
        fake = FakeWled()
        fake.post_status = 401
        code, _out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("PIN or OTA lock", err)

    def test_check_and_declined_confirmation_write_nothing(self):
        fake = FakeWled()
        host = fake.start(self)
        self.assertEqual(self.run_main("ota", host, "--check")[0], 0)
        self.assertEqual(self.run_main("ota", host, answer="no")[0], 1)
        self.assertEqual(fake.posts, [])
        self.assertEqual(len(self.backups()), 2)

    def test_no_reboot_fails(self):
        fake = FakeWled()
        fake.reboot_now = lambda: None  # Accepts the upload, keeps running the old firmware.
        code, _out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("no reboot", err)

    def test_recent_boot_before_upload_is_not_taken_for_the_flash_reboot(self):
        fake = FakeWled()
        fake.boot_at = time.monotonic() - 3  # Rebooted just before the flash.
        fake.reboot_now = lambda: None
        code, _out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("no reboot", err)

    def test_dropped_connection_is_decided_by_the_reboot(self):
        fake = FakeWled()
        host = fake.start(self)
        real = flash._http.open

        def drop_upload(request, timeout=None):
            if isinstance(request, str) or not request.full_url.endswith("/update"):
                return real(request, timeout=timeout)
            fake.reboot_now()
            raise ConnectionResetError("closed")

        with mock.patch.object(flash._http, "open", drop_upload):
            code, out, err = self.run_main("ota", host)
        self.assertEqual(code, 0, err)
        self.assertIn("no HTTP response", out)

    def test_verification_catches_bad_firmware(self):
        cases = {
            "no usermod": (None, "no moonMotor"),
            "motor running": ({"output": "run", "pin": 13}, "motor output is run"),
            "pin taken": ({"output": "stop", "pin": -1}, "usermod pin is -1"),
        }
        for name, (motor, message) in cases.items():
            with self.subTest(name):
                fake = FakeWled()
                fake.motor_after_boot = motor
                code, _out, err = self.run_main("ota", fake.start(self))
                self.assertEqual(code, 1)
                self.assertIn(message, err)

    def test_lost_network_settings_fail_verification(self):
        fake = FakeWled()
        original = fake.reboot_now

        def reboot():
            original()
            fake.cfg["eth"]["type"] = 0

        fake.reboot_now = reboot
        code, _out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("eth.type is 0", err)

    def test_changed_settings_warn(self):
        fake = FakeWled()
        original = fake.reboot_now

        def reboot():
            original()
            fake.presets = b"{}"
            fake.cfg["nw"]["name"] = "other"
            fake.cfg["um"] = {"MoonMotor": {}}  # Expected on first run; must not warn.

        fake.reboot_now = reboot
        code, out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 0, err)
        self.assertIn("presets.json changed", out)
        self.assertIn("cfg.json sections changed: nw", out)
        self.assertNotIn("um", out.split("sections changed:")[1].splitlines()[0])


class SettingsTests(FlashTestCase):
    def config_fake(self, *settings):
        """A unit already running the usermod."""
        fake = FakeWled()
        fake.info["moonMotor"] = dict(fake.motor_after_boot)
        for patch in settings:
            merged(fake.cfg, patch)
        return fake

    def test_repo_settings_are_the_site_values(self):
        settings = flash.load_settings()
        live, led = settings["if"]["live"], settings["hw"]["led"]
        self.assertEqual((live["en"], live["port"], live["dmx"]["uni"]), (True, 6454, 0))
        self.assertEqual((live["dmx"]["addr"], live["dmx"]["mode"]), (1, 4))
        self.assertEqual((led["total"], led["maxpwr"], len(led["ins"])), (129, 0, 1))
        self.assertEqual({key: led["ins"][0][key] for key in ("start", "len", "pin", "maxpwr", "ledma")},
                         {"start": 0, "len": 129, "pin": [16], "maxpwr": 0, "ledma": 30})
        # 燈條是 WS281x、色序 RGB（order 1；2026-11-11 現場確認，GRB 時紅綠對調）。
        # The strip is WS281x in RGB order (order 1; confirmed on site 2026-11-11, GRB swapped red and green).
        self.assertEqual((22, 1), (led["ins"][0]["type"], led["ins"][0]["order"]))

    def test_flash_writes_settings_then_reboots_and_verifies(self):
        fake = FakeWled()
        code, out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.paths(), ["/update", "/json/cfg", "/json/state"])
        self.assertEqual(json.loads(fake.posts[1][2]), flash.load_settings())
        self.assertEqual(json.loads(fake.posts[2][2]), {"rb": True})
        self.assertEqual(fake.cfg["if"]["live"]["port"], 6454)
        self.assertIn("rebooting to apply settings", out)

    def run_config(self, *argv, answer="yes"):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", return_value=answer), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = flash.main(["config", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_config_only_never_uploads_firmware(self):
        fake = self.config_fake()
        code, _out, err = self.run_config(fake.start(self))
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.paths(), ["/json/cfg", "/json/state"])
        self.assertEqual(len(self.backups()), 1)

    def test_matching_settings_are_not_rewritten(self):
        fake = self.config_fake(flash.load_settings())
        code, out, err = self.run_config(fake.start(self))
        self.assertEqual(code, 0, err)
        self.assertIn("already match", out)
        self.assertEqual(fake.posts, [])

    def test_check_lists_changes_and_writes_nothing(self):
        fake = self.config_fake({"if": {"live": {"port": 5568}}})
        code, out, err = self.run_config(fake.start(self), "--check")
        self.assertEqual(code, 0, err)
        self.assertIn("/if/live/port is 5568, wanted 6454", out)
        self.assertEqual(fake.posts, [])

    def test_declined_confirmation_writes_nothing(self):
        fake = self.config_fake()
        self.assertEqual(self.run_config(fake.start(self), answer="n")[0], 1)
        self.assertEqual(fake.posts, [])

    def test_stock_firmware_is_refused(self):
        fake = FakeWled()
        code, _out, err = self.run_config(fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("flash it first", err)
        self.assertEqual(fake.posts, [])

    def test_unsaved_settings_do_not_reboot(self):
        fake = self.config_fake()
        fake.saves_settings = False
        code, _out, err = self.run_config(fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("not saved", err)
        self.assertEqual(fake.paths(), ["/json/cfg"])

    def test_pin_is_explained(self):
        fake = self.config_fake()
        fake.settings_status = 401
        code, _out, err = self.run_config(fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("settings PIN", err)

    def test_extra_led_output_is_a_difference(self):
        wanted = flash.load_settings()["hw"]["led"]["ins"]
        problems = flash.settings_problems([*wanted, {"len": 30}], wanted, "/ins")
        self.assertEqual(problems, ["/ins has 2 entries, wanted 1"])

    def test_settings_lost_after_the_reboot_fail(self):
        fake = self.config_fake()
        original = fake.reboot_now

        def reboot():
            original()
            fake.cfg["if"]["live"]["port"] = 5568

        fake.reboot_now = reboot
        code, _out, err = self.run_config(fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("/if/live/port is 5568", err)

    def test_bad_settings_file_stops_before_any_write(self):
        bad = self.root / "bad.json"
        bad.write_text("{", encoding="utf-8")
        fake = FakeWled()
        with mock.patch.object(flash, "SETTINGS_PATH", bad):
            code, _out, err = self.run_main("ota", fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("Cannot read", err)
        self.assertEqual(fake.posts, [])


class UsbTests(FlashTestCase):
    def setUp(self):
        super().setUp()
        self.esptool = FakeEsptool(self.partitions)
        patcher = mock.patch.object(flash, "run_esptool", self.esptool)
        patcher.start()
        self.addCleanup(patcher.stop)

    def names(self):
        return [command[0] for command in self.esptool.commands]

    def test_writes_only_app_and_otadata_after_full_backup(self):
        fake = FakeWled()
        self.esptool.on_write = fake.reboot_now
        code, _out, err = self.run_main("usb", "--port", "COM9", "--ip", fake.start(self))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names(), ["flash_id", "read_flash", "read_flash", "write_flash"])
        self.assertEqual(fake.paths(), ["/json/cfg", "/json/state"])
        self.assertEqual(self.esptool.written, {hex(flash.OTADATA_OFFSET): b"\xff" * flash.OTADATA_SIZE,
                                                hex(flash.APP_OFFSET): self.image})
        folder = self.backups()[0]
        self.assertEqual((folder / "full_flash.bin").stat().st_size, flash.FLASH_SIZE)
        self.assertEqual((folder / "partitions.bin").read_bytes(), self.partitions)
        self.assertTrue((folder / "cfg.json").exists())

    def test_no_backup_skips_only_the_whole_flash_read(self):
        fake = FakeWled()
        self.esptool.on_write = fake.reboot_now
        code, out, err = self.run_main("usb", "--port", "COM9", "--ip", fake.start(self), "--no-backup")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names(), ["flash_id", "read_flash", "write_flash"])
        self.assertIn("no full_flash.bin", out)
        folder = self.backups()[0]
        self.assertFalse((folder / "full_flash.bin").exists())
        self.assertEqual((folder / "partitions.bin").read_bytes(), self.partitions)
        self.assertTrue((folder / "cfg.json").exists())

    def test_different_partition_table_writes_nothing(self):
        self.esptool.partitions = b"Q" * flash.PARTITIONS_SIZE
        code, _out, err = self.run_main("usb", "--port", "COM9", "--no-ip")
        self.assertEqual(code, 1)
        self.assertIn("partition table differs", err)
        self.assertNotIn("write_flash", self.names())
        self.assertEqual(self.names().count("read_flash"), 1)

    def test_short_backup_writes_nothing(self):
        real = self.esptool

        def short_read(port, *command):
            output = real(port, *command)
            if command[0] == "read_flash" and command[2] == hex(flash.FLASH_SIZE):
                Path(command[3]).write_bytes(b"\0" * 10)
            return output

        with mock.patch.object(flash, "run_esptool", short_read):
            code, _out, err = self.run_main("usb", "--port", "COM9", "--no-ip")
        self.assertEqual(code, 1)
        self.assertIn("expected", err)
        self.assertNotIn("write_flash", self.names())

    def test_ip_of_another_unit_writes_nothing(self):
        fake = FakeWled(mac="aaaaaaaaaaaa")
        code, _out, err = self.run_main("usb", "--port", "COM9", "--ip", fake.start(self))
        self.assertEqual(code, 1)
        self.assertIn("different units", err)
        self.assertEqual(self.names(), ["flash_id"])

    def test_wrong_chip_or_flash_size_writes_nothing(self):
        for output in ("Chip is ESP32-S3\nMAC: 20:9b:a9:8c:63:dc\nDetected flash size: 4MB\n",
                       "Chip is ESP32-D0WD\nMAC: 20:9b:a9:8c:63:dc\nDetected flash size: 8MB\n"):
            with self.subTest(output):
                self.esptool.flash_id = output
                self.esptool.commands.clear()
                self.assertEqual(self.run_main("usb", "--port", "COM9", "--no-ip")[0], 1)
                self.assertEqual(self.names(), ["flash_id"])

    def test_check_backs_up_without_writing(self):
        code, out, _err = self.run_main("usb", "--port", "COM9", "--no-ip", "--check")
        self.assertEqual(code, 0)
        self.assertIn("nothing was written", out)
        self.assertNotIn("write_flash", self.names())

    def test_declined_confirmation_writes_nothing(self):
        code, _out, err = self.run_main("usb", "--port", "COM9", "--no-ip", answer="n")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", err)
        self.assertNotIn("write_flash", self.names())

    def test_no_ip_says_firmware_is_unverified(self):
        code, out, _err = self.run_main("usb", "--port", "COM9", "--no-ip")
        self.assertEqual(code, 0)
        self.assertIn("not verified", out)
        self.assertIn("unit_settings.json is not written", out)


class CliTests(FlashTestCase):
    def test_usb_requires_a_choice_about_the_network(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            flash.parse_args(["usb", "--port", "COM9"])

    def test_interactive_answers_become_arguments(self):
        with mock.patch("builtins.input", side_effect=["u", "COM3", ""]):
            self.assertEqual(flash.ask_args(), ["usb", "--port", "COM3", "--no-ip"])
        with mock.patch("builtins.input", side_effect=["o", "10.0.0.1 10.0.0.2"]):
            self.assertEqual(flash.ask_args(), ["ota", "10.0.0.1", "10.0.0.2"])

    def test_esptool_connect_failure_mentions_download_mode(self):
        failed = mock.Mock(returncode=2, stdout="A fatal error occurred: Failed to connect to ESP32", stderr="")
        with mock.patch.object(flash.subprocess, "run", return_value=failed), \
                self.assertRaisesRegex(flash.FlashError, "download mode"):
            flash.run_esptool("COM9", "flash_id")


if __name__ == "__main__":
    unittest.main()
