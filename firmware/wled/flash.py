#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["esptool==4.8.1"]
# ///
"""Flash the Moon Observe WLED build onto GL-C-616WL units, over OTA or USB.

    uv run flash.py ota 192.168.1.50 192.168.1.51
    uv run flash.py usb --port COM3 --ip 192.168.1.50
    uv run flash.py usb --port COM3 --no-ip     # recovery, when the unit is off the network
    uv run flash.py config 192.168.1.50         # write unit_settings.json only

A flash also writes unit_settings.json when the unit is on the network. --check stops
after the checks and backups, before anything is written. Without arguments the
script asks what to do (this is what flash.bat runs). See README.md.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
BACKUP_DIR = HERE / "backups"
SETTINGS_PATH = HERE / "unit_settings.json"

OTADATA_OFFSET = 0xE000
OTADATA_SIZE = 0x2000
PARTITIONS_OFFSET = 0x8000
PARTITIONS_SIZE = 0xC00
APP_OFFSET = 0x10000
APP_SIZE = 0x180000
FLASH_SIZE = 0x400000
BAUD = "460800"

RELEASE = "ESP32_Ethernet"
ETH_TYPE = 13
ETH_PINS = [21, 19, 22, 25, 26, 27, 5, 23, 33, 0]
MOTOR_PIN = 13
# These cfg.json keys are expected to change when the usermod first runs.
CFG_KEYS_MAY_CHANGE = {"um", "rev", "vid"}

HTTP_TIMEOUT_S = 5
UPLOAD_TIMEOUT_S = 300
REBOOT_TIMEOUT_S = 90
SETTINGS_SAVE_TIMEOUT_S = 15
POLL_INTERVAL_S = 2

# LAN units must never be reached through a proxy from the environment.
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FlashError(Exception):
    pass


@dataclass
class Unit:
    host: str
    info: dict
    cfg: dict
    presets: bytes | None

    @property
    def mac(self) -> str:
        return str(self.info.get("mac", "")).lower()


def fetch(host: str, path: str, timeout: float = HTTP_TIMEOUT_S) -> bytes:
    with _http.open(f"http://{host}{path}", timeout=timeout) as response:
        return response.read()


def fetch_json(host: str, path: str) -> dict:
    return json.loads(fetch(host, path))


def fetch_presets(host: str) -> bytes | None:
    try:
        return fetch(host, "/presets.json")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None  # A unit without saved presets has no file.
        raise


def read_unit(host: str) -> Unit:
    try:
        return Unit(host, fetch_json(host, "/json/info"), fetch_json(host, "/cfg.json"), fetch_presets(host))
    except (OSError, ValueError) as error:
        raise FlashError(f"{host}: cannot read the unit: {error}") from error


def eth_problems(cfg: dict) -> list[str]:
    eth = cfg.get("eth", {})
    problems = []
    if eth.get("type") != ETH_TYPE:
        problems.append(f"eth.type is {eth.get('type')}, expected {ETH_TYPE}")
    if eth.get("pin") != ETH_PINS:
        problems.append(f"eth.pin is {eth.get('pin')}, expected {ETH_PINS}")
    return problems


def identity_problems(info: dict, version: str) -> list[str]:
    problems = []
    if info.get("release") != RELEASE:
        problems.append(f"release is {info.get('release')}, expected {RELEASE}")
    if info.get("ver") != version.removeprefix("v"):
        problems.append(f"ver is {info.get('ver')}, expected {version.removeprefix('v')}")
    return problems


def precheck(host: str, version: str) -> Unit:
    unit = read_unit(host)
    problems = identity_problems(unit.info, version) + eth_problems(unit.cfg)
    if not unit.mac:
        problems.append("/json/info has no mac")
    # A stream in progress means the Pi is live; a flash would cut it and stop the motor.
    if unit.info.get("live"):
        problems.append("realtime stream is active; stop moon-observe first")
    if unit.info.get("moonMotor", {}).get("output") == "run":
        problems.append("motor output is RUN; stop the motor first")
    if problems:
        raise FlashError(f"{host}: refusing to flash:\n  " + "\n  ".join(problems))
    return unit


def check_image(path: Path) -> bytes:
    if not path.is_file():
        raise FlashError(f"{path} not found; run build.sh first.")
    image = path.read_bytes()
    if image[:1] != b"\xe9":
        raise FlashError(f"{path} is not an ESP32 app image.")
    if len(image) > APP_SIZE:
        raise FlashError(f"{path} is {len(image)} bytes; the app partition holds {APP_SIZE}.")
    for marker in (b"moonMotor", RELEASE.encode()):
        if marker not in image:
            raise FlashError(f"{path} does not contain {marker.decode()}; wrong build?")
    return image


def new_backup_dir(mac: str) -> Path:
    folder = BACKUP_DIR / f"{mac}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    folder.mkdir(parents=True)
    return folder


def backup_http(unit: Unit, folder: Path) -> None:
    (folder / "info.json").write_text(json.dumps(unit.info, indent=1), encoding="utf-8")
    (folder / "cfg.json").write_text(json.dumps(unit.cfg, indent=1), encoding="utf-8")
    if unit.presets is not None:
        (folder / "presets.json").write_bytes(unit.presets)


def wait_for_reboot(host: str, rebooted_after: float) -> Unit:
    deadline = time.monotonic() + REBOOT_TIMEOUT_S
    unit = None
    while time.monotonic() < deadline:
        try:
            candidate = read_unit(host)
        except FlashError:
            candidate = None
        # Uptime since the write ended proves a reboot. Same-version flashes leave `ver` unchanged.
        if candidate and candidate.info.get("uptime", 1 << 31) <= time.monotonic() - rebooted_after + 1:
            unit = candidate
            break
        time.sleep(POLL_INTERVAL_S)
    if unit is None:
        raise FlashError(f"{host}: no reboot seen within {REBOOT_TIMEOUT_S} s. "
                         "An unfinished OTA keeps IO13 on stop until the unit reboots.")
    return unit


def firmware_problems(unit: Unit, before: Unit | None, version: str) -> list[str]:
    problems = identity_problems(unit.info, version) + eth_problems(unit.cfg)
    motor = unit.info.get("moonMotor")
    if not isinstance(motor, dict):
        problems.append("/json/info has no moonMotor; the usermod is not running")
    else:
        if motor.get("output") != "stop":
            problems.append(f"motor output is {motor.get('output')}, expected stop")
        if motor.get("pin") != MOTOR_PIN:
            problems.append(f"usermod pin is {motor.get('pin')}, expected {MOTOR_PIN}; IO{MOTOR_PIN} in use?")
    if before is not None and unit.mac != before.mac:
        problems.append(f"mac changed from {before.mac} to {unit.mac}")
    return problems


def verify_after_flash(host: str, rebooted_after: float, before: Unit | None, version: str) -> list[str]:
    """Wait for the reboot, then check the new firmware. Returns non-fatal warnings."""
    unit = wait_for_reboot(host, rebooted_after)
    problems = firmware_problems(unit, before, version)
    if problems:
        raise FlashError(f"{host}: flash written but verification failed:\n  " + "\n  ".join(problems))

    warnings = []
    if before is not None:
        if unit.presets != before.presets:
            warnings.append("presets.json changed")
        keys = set(before.cfg) | set(unit.cfg)
        changed = sorted(k for k in keys - CFG_KEYS_MAY_CHANGE if before.cfg.get(k) != unit.cfg.get(k))
        if changed:
            warnings.append("cfg.json sections changed: " + ", ".join(changed))
    return warnings


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FlashError(f"Cannot read {SETTINGS_PATH}: {error}") from error


def settings_problems(actual, wanted, path: str = "") -> list[str]:
    """What differs from `wanted`. Keys only in `actual` are fine; lists must match in length."""
    if isinstance(wanted, dict):
        actual = actual if isinstance(actual, dict) else {}
        return [p for key, value in wanted.items() for p in settings_problems(actual.get(key), value, f"{path}/{key}")]
    if isinstance(wanted, list) and wanted and isinstance(wanted[0], dict):
        if not isinstance(actual, list) or len(actual) != len(wanted):
            return [f"{path} has {len(actual) if isinstance(actual, list) else 0} entries, wanted {len(wanted)}"]
        return [p for i, value in enumerate(wanted) for p in settings_problems(actual[i], value, f"{path}[{i}]")]
    return [] if actual == wanted else [f"{path} is {actual}, wanted {wanted}"]


def post_json(host: str, path: str, payload: dict) -> None:
    request = urllib.request.Request(f"http://{host}{path}", data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    with _http.open(request, timeout=HTTP_TIMEOUT_S):
        pass


def apply_settings(host: str, settings: dict, before: Unit | None, version: str) -> None:
    if not settings_problems(read_unit(host).cfg, settings):
        print(f"{host}: settings already match")
        return
    print(f"{host}: writing settings")
    try:
        post_json(host, "/json/cfg", settings)
    except urllib.error.HTTPError as error:
        hint = "; settings PIN is on, unlock it in the WLED page" if error.code == 401 else ""
        raise FlashError(f"{host}: settings rejected (HTTP {error.code}){hint}") from error
    except OSError as error:
        raise FlashError(f"{host}: cannot write settings: {error}") from error

    # WLED saves after re-initialising the LED outputs, and a port change only applies
    # after a reboot. Reboot once the saved file is right so nothing is lost.
    deadline = time.monotonic() + SETTINGS_SAVE_TIMEOUT_S
    problems = settings_problems(read_unit(host).cfg, settings)
    while problems and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_S)
        try:
            problems = settings_problems(read_unit(host).cfg, settings)
        except FlashError:
            pass
    if problems:
        raise FlashError(f"{host}: settings were not saved:\n  " + "\n  ".join(problems))

    print(f"{host}: rebooting to apply settings")
    try:
        post_json(host, "/json/state", {"rb": True})
    except OSError as error:
        print(f"  no HTTP response ({error}); checking for the reboot")
    unit = wait_for_reboot(host, time.monotonic())
    problems = settings_problems(unit.cfg, settings) + firmware_problems(unit, before, version)
    if problems:
        raise FlashError(f"{host}: settings written but verification failed:\n  " + "\n  ".join(problems))


def upload_ota(host: str, image: bytes, filename: str) -> None:
    # Field name as in the WLED update form. Never send skipValidation: the release
    # check is what stops an image for another board from losing the network.
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="update"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode() + image + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"http://{host}/update", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with _http.open(request, timeout=UPLOAD_TIMEOUT_S):
            pass
    except urllib.error.HTTPError as error:
        detail = re.sub(r"<[^>]+>", " ", error.read().decode("utf-8", "replace"))
        if error.code == 401:
            detail = "settings PIN or OTA lock is on; unlock it in the WLED page. " + detail
        raise FlashError(f"{host}: OTA rejected (HTTP {error.code}): {' '.join(detail.split())[:200]}\n"
                         "  If the upload had started, IO13 stays on stop until the unit reboots.") from error
    except OSError as error:
        # The unit can drop the connection as it reboots; the reboot check decides.
        print(f"  no HTTP response ({error}); checking for the reboot")


def confirm(prompt: str, assume_yes: bool) -> None:
    print(prompt)
    print("Motor power must be off, or the motor free to stop (it coasts for about 10 s).")
    if assume_yes:
        return
    try:
        answer = input("Type yes to flash: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        raise FlashError("Cancelled; nothing was written.")


def describe_image(path: Path, image: bytes) -> str:
    return f"Image: {path.name}, {len(image)} bytes, sha256 {hashlib.sha256(image).hexdigest()}"


def prepare_units(hosts: list[str], version: str) -> list[Unit]:
    # Check every unit before the first write, so a bad one cannot leave a half-done set.
    units = [precheck(host, version) for host in hosts]
    macs = [unit.mac for unit in units]
    if len(set(macs)) != len(macs):
        raise FlashError(f"Two addresses answer as the same unit: {macs}")
    for unit in units:
        folder = new_backup_dir(unit.mac)
        backup_http(unit, folder)
        print(f"{unit.host}: {unit.mac} checked; backup in {folder}")
    return units


def report_pending_settings(unit: Unit, settings: dict) -> None:
    for problem in settings_problems(unit.cfg, settings):
        print(f"{unit.host}: to change: {problem}")


def run_config(args, version: str, settings: dict) -> None:
    hosts = list(dict.fromkeys(args.hosts))
    units = prepare_units(hosts, version)
    for unit in units:
        if "moonMotor" not in unit.info:
            raise FlashError(f"{unit.host}: runs without the usermod; flash it first.")
        report_pending_settings(unit, settings)
    if args.check:
        print("Check passed; nothing was written.")
        return
    confirm(f"Write {SETTINGS_PATH.name} and reboot: " + ", ".join(hosts), args.yes)

    for index, unit in enumerate(units):
        try:
            apply_settings(unit.host, settings, unit, version)
        except FlashError:
            untouched = [u.host for u in units[index + 1:]]
            if untouched:
                print(f"Stopped. Not changed: {', '.join(untouched)}")
            raise
        print(f"{unit.host}: OK")


def run_ota(args, version: str, image_path: Path, image: bytes, settings: dict) -> None:
    hosts = list(dict.fromkeys(args.hosts))
    units = prepare_units(hosts, version)
    for unit in units:
        report_pending_settings(unit, settings)
    if args.check:
        print("Check passed; nothing was written.")
        return
    confirm(f"{describe_image(image_path, image)}\nOTA to: " + ", ".join(hosts), args.yes)

    for index, unit in enumerate(units):
        print(f"{unit.host}: uploading")
        try:
            upload_ota(unit.host, image, image_path.name)
            uploaded = time.monotonic()
            print(f"{unit.host}: waiting for the reboot")
            warnings = verify_after_flash(unit.host, uploaded, unit, version)
            apply_settings(unit.host, settings, unit, version)
        except FlashError as error:
            untouched = [u.host for u in units[index + 1:]]
            if untouched:
                print(f"Stopped. Not flashed: {', '.join(untouched)}")
            raise error
        print(f"{unit.host}: OK")
        for warning in warnings:
            print(f"{unit.host}: WARNING: {warning}")


def run_esptool(port: str, *command: str) -> str:
    try:
        result = subprocess.run([sys.executable, "-m", "esptool", "--port", port, "--baud", BAUD, *command],
                                capture_output=True, text=True, errors="replace")
    except OSError as error:
        raise FlashError(f"Cannot start esptool: {error}") from error
    output = result.stdout + result.stderr
    if result.returncode != 0:
        hint = ""
        if "Failed to connect" in output or "Wrong boot mode" in output:
            hint = "\nThe unit did not enter download mode. Hold GPIO0 low while resetting, then retry."
        tail = "\n".join(output.strip().splitlines()[-6:])
        raise FlashError(f"esptool {command[0]} failed:\n{tail}{hint}")
    return output


def probe_usb(port: str) -> str:
    """Return the MAC of the ESP32 on `port`, or raise if it is not the expected chip."""
    output = run_esptool(port, "flash_id")
    if not re.search(r"Chip is ESP32-D", output):
        raise FlashError(f"{port}: not a classic ESP32:\n{output}")
    if "Detected flash size: 4MB" not in output:
        raise FlashError(f"{port}: flash is not 4MB:\n{output}")
    match = re.search(r"MAC: ([0-9a-fA-F:]{17})", output)
    if not match:
        raise FlashError(f"{port}: esptool reported no MAC:\n{output}")
    return match.group(1).replace(":", "").lower()


def read_exact(port: str, offset: int, size: int, target: Path) -> bytes:
    run_esptool(port, "read_flash", hex(offset), hex(size), str(target))
    data = target.read_bytes()
    if len(data) != size:
        raise FlashError(f"Read {len(data)} bytes at {offset:#x}, expected {size}.")
    return data


def find_port() -> str:
    from serial.tools import list_ports  # pyserial comes with esptool.

    ports = [p.device for p in list_ports.comports() if p.vid is not None]
    if len(ports) != 1:
        raise FlashError(f"Pass --port: found {len(ports)} USB serial ports: {ports}")
    return ports[0]


def run_usb(args, version: str, image_path: Path, image: bytes, settings: dict) -> None:
    reference_path = image_path.parent / "partitions_reference.bin"
    if not reference_path.is_file():
        raise FlashError(f"{reference_path} not found; run build.sh first.")
    unit = precheck(args.ip, version) if args.ip else None
    port = args.port or find_port()

    print(f"{port}: identifying the unit")
    mac = probe_usb(port)
    if unit is not None and unit.mac != mac:
        raise FlashError(f"{args.ip} is {unit.mac} but {port} is {mac}: different units.")

    folder = new_backup_dir(mac)
    if unit is not None:
        backup_http(unit, folder)
    print(f"{port}: {mac}, comparing the partition table")
    partitions = read_exact(port, PARTITIONS_OFFSET, PARTITIONS_SIZE, folder / "partitions.bin")
    if partitions != reference_path.read_bytes():
        raise FlashError(f"{mac}: partition table differs from the build, so app0 may be elsewhere. "
                         f"Nothing was written; the table is in {folder}.")
    if args.no_backup:
        print(f"{port}: skipping the whole-flash backup; there is no full_flash.bin to restore from")
    else:
        print(f"{port}: backing up the whole flash (about 2 minutes)")
        read_exact(port, 0, FLASH_SIZE, folder / "full_flash.bin")
        print(f"Backup in {folder}. Restore: esptool --port {port} write_flash 0 full_flash.bin")
    if args.check:
        print("Check passed; nothing was written.")
        return
    confirm(f"{describe_image(image_path, image)}\nUSB flash to: {mac} on {port}", args.yes)

    # Only app0 and otadata: the filesystem holds the unit's settings. Blank otadata
    # boots app0 even if a past OTA left the unit running from app1. One esptool run
    # checks both regions by hash and resets once at the end.
    with tempfile.TemporaryDirectory() as temp:
        blank = Path(temp) / "otadata_blank.bin"
        blank.write_bytes(b"\xff" * OTADATA_SIZE)
        print(f"{port}: writing")
        run_esptool(port, "write_flash", hex(OTADATA_OFFSET), str(blank), hex(APP_OFFSET), str(image_path))
    written = time.monotonic()

    if unit is None:
        print(f"{port}: written and hash-checked. Without --ip the new firmware is not verified and "
              f"{SETTINGS_PATH.name} is not written: check the Info page for the motor rows, then run "
              "flash.py config once the unit is on the network.")
        return
    print(f"{args.ip}: waiting for the reboot")
    warnings = verify_after_flash(args.ip, written, unit, version)
    apply_settings(args.ip, settings, unit, version)
    print(f"{args.ip}: OK")
    for warning in warnings:
        print(f"{args.ip}: WARNING: {warning}")


def ask_args() -> list[str]:
    mode = input("Flash over [o]ta or [u]sb, or only write [c]onfig? ").strip().lower()
    if mode.startswith("o"):
        return ["ota", *input("Unit addresses, separated by spaces: ").split()]
    if mode.startswith("c"):
        return ["config", *input("Unit addresses, separated by spaces: ").split()]
    if mode.startswith("u"):
        port = input("Serial port (blank to detect): ").strip()
        ip = input("Unit address (blank if it is off the network): ").strip()
        return ["usb", *(["--port", port] if port else []), *(["--ip", ip] if ip else ["--no-ip"])]
    raise FlashError("Choose o, u or c.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flash the Moon Observe WLED build onto GL-C-616WL units.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--check", action="store_true", help="run the checks and backups, write nothing")
    common.add_argument("--yes", action="store_true", help="skip the confirmation")
    firmware = argparse.ArgumentParser(add_help=False, parents=[common])
    firmware.add_argument("--bin", type=Path, help="firmware image (default: dist/WLED_<WLED_VERSION>_moon_eth.bin)")
    modes = parser.add_subparsers(dest="mode", required=True)
    config = modes.add_parser("config", parents=[common], help="write unit_settings.json, no flash")
    config.add_argument("hosts", nargs="+", metavar="host")
    ota = modes.add_parser("ota", parents=[firmware], help="upload over the network, one unit at a time")
    ota.add_argument("hosts", nargs="+", metavar="host")
    usb = modes.add_parser("usb", parents=[firmware], help="write over a serial cable")
    usb.add_argument("--port", help="serial port (default: the only USB serial port)")
    usb.add_argument("--no-backup", action="store_true",
                     help="skip full_flash.bin (about 2 minutes); the JSON backup and partition check still run")
    target = usb.add_mutually_exclusive_group(required=True)
    target.add_argument("--ip", help="the unit's address, for the checks before and after")
    target.add_argument("--no-ip", action="store_true", help="unit is off the network: skip those checks")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv or ask_args())
        version = (HERE / "WLED_VERSION").read_text(encoding="ascii").strip()
        settings = load_settings()
        if args.mode == "config":
            run_config(args, version, settings)
            return 0
        image_path = args.bin or HERE / "dist" / f"WLED_{version}_moon_eth.bin"
        image = check_image(image_path)
        (run_ota if args.mode == "ota" else run_usb)(args, version, image_path, image, settings)
    except FlashError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
