"""馬達啟停重複測試：直接對 WLED 送 Art-Net ch469，重複固定次數的「啟動 → 停止」，用肉眼數漏掉幾次。
Motor start/stop cycle test: sends Art-Net ch469 straight to the WLED units for a fixed number of start/stop
cycles, so missed starts and stops can be counted by eye.

先停掉後端（它每格都送 ch469，兩邊會互相蓋掉）： / Stop the backend first (it sends ch469 in every frame):
    sudo systemctl stop tph-art-museum

用法 / Usage:
    python motor_cycle_test.py --units 5                 # 一台，20 次 / one unit, 20 cycles
    python motor_cycle_test.py --units 1-4,9 --cycles 50
    python motor_cycle_test.py --units all --stagger 1   # 24 台，每台錯開 1 秒 / staggered by 1 s each
    python motor_cycle_test.py --ip 192.168.1.50 --source-ip ""   # 桌上測試 / bench

Pi 上用 release 的 Python（有 stupidArtnet）： / On the Pi use the release's Python:
    /opt/tph-art-museum/current/.venv/bin/python motor_cycle_test.py --units 5

怎麼看 / How to read it:
- 燈條顏色 = 目前送出的命令：**綠 = 啟動（255）、紅 = 停止（0）**。
  綠燈期間結束時還沒轉 = 漏啟動；紅燈期間結束時還在轉 = 漏停止。
  Strip colour is the command being sent: green = run, red = stop. Not spinning by the end of green = missed
  start; still spinning by the end of red = missed stop.
- 每次切換後 1 秒會問該台 WLED（/json/info 的 moonMotor.output），確認 IO13 真的有切換；
  WLED 正確但馬達不對 = XIAO 端的問題。
  One second after each edge the unit's WLED is asked whether IO13 really switched; WLED right but motor
  wrong = the XIAO side.

全自動（XIAO 用 USB 接到這台電腦，只能測一台）： / Fully automatic (the XIAO on USB, one unit only):
    uv run --locked --project backend --with pyserial python backend/scripts/motor_cycle_test.py
        --ip 192.168.0.222 --source-ip 192.168.0.72 --xiao-port COM9
  讀 XIAO 序列埠的狀態切換（``[MOTOR_PWR] A -> B``），每次啟動／停止自動判定並在最後列出漏掉的次數，
  不用肉眼看。需要 pyserial（上面的 --with pyserial）。不要用 1200 baud 開這個埠（會進 bootloader）。
  Reads the XIAO's state changes from its serial port and judges every start and stop by itself.

預設時間配合 XIAO 韌體（duty 54：緩啟動約 7.2 秒、緩停約 6.8 秒）；--off-seconds 太短會踩到
「緩停中收到的啟動被忽略」那個已知問題，除非就是要測它。
Default timings fit the XIAO firmware (soft start about 7.2 s, soft stop about 6.8 s). A shorter --off-seconds
runs into the known "start ignored during the soft stop" behaviour.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from stupidArtnet import StupidArtnet

PIXELS = 129            # 每條燈條的 LED 數（firmware/wled/unit_settings.json）。 / LEDs per strip.
MOTOR_CHANNEL = 469     # 從 1 起算。 / 1-based.
PACKET_SIZE = 470       # 同後端的封包長度。 / Same packet length as the backend.
FPS = 40
RUN_COLOR = (0, 1, 0)   # 綠 = 啟動。 / Green = run.
STOP_COLOR = (1, 0, 0)  # 紅 = 停止。 / Red = stop.

RUN_STATES = {"PRECHARGING", "MAIN_RELAY_SETTLING", "PRECHARGE_RELEASE", "MOTOR_RAMPING", "MOTOR_RUNNING"}
TRANSITION = re.compile(r"\[MOTOR_PWR\] (\w+) -> (\w+)")

print_lock = threading.Lock()


def log(text: str) -> None:
    with print_lock:
        print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)


def parse_units(text: str) -> list[int]:
    if text.strip().lower() == "all":
        return list(range(1, 25))
    units: list[int] = []
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        units.extend(range(int(first), int(last or first) + 1))
    if not units or any(not 1 <= unit <= 54 for unit in units) or len(set(units)) != len(units):
        raise argparse.ArgumentTypeError("units: e.g. 5 / 1-4,9 / all (each once, 1..54)")
    return units


def packet(run: bool, level: int) -> bytes:
    data = bytearray(PACKET_SIZE)
    color = RUN_COLOR if run else STOP_COLOR
    data[:PIXELS * 3] = bytes(channel * level for channel in color) * PIXELS
    data[MOTOR_CHANNEL - 1] = 255 if run else 0
    return bytes(data)


class XiaoMonitor:
    """讀 XIAO 序列埠的狀態切換。 / Follows the XIAO's state from its ``[MOTOR_PWR] A -> B`` serial lines."""

    def __init__(self, port: str) -> None:
        import serial  # pyserial；只有用 --xiao-port 時才需要。 / Only needed with --xiao-port.

        # 115200：SAMD21 以 1200 baud 開埠會重置進 bootloader。 / Never 1200 baud: that resets into the bootloader.
        self.serial = serial.serial_for_url(port, 115200, timeout=0.2)
        self.state: str | None = None   # None = 還沒看到任何切換。 / No transition seen yet.
        self.transitions: list[tuple[float, str, str]] = []
        self.lock = threading.Lock()
        self.error: str | None = None
        self.closing = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        buffer = b""
        while not self.closing:
            try:
                buffer += self.serial.read(256)
            except Exception as error:  # noqa: BLE001 - 拔線等。 / Unplugged etc.
                if not self.closing:
                    self.error = str(error)
                    log(f"  xiao: serial port failed: {error}")
                return
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("ascii", "replace").strip()
                match = TRANSITION.search(line)
                if match:
                    with self.lock:
                        self.state = match.group(2)
                        self.transitions.append((time.monotonic(), match.group(1), match.group(2)))
                if line:
                    log(f"  xiao: {line}")

    def snapshot(self, since: float) -> tuple[str | None, list[tuple[float, str, str]]]:
        with self.lock:
            return self.state, [item for item in self.transitions if item[0] >= since]

    def close(self) -> None:
        self.closing = True
        self.thread.join(timeout=1)
        self.serial.close()


def moon_motor(ip: str, timeout: float = 1.5) -> dict:
    """該台 WLED 的 moonMotor 狀態。 / The unit's moonMotor info (output, reason, value, ageMs)."""
    with urllib.request.urlopen(f"http://{ip}/json/info", timeout=timeout) as response:
        return json.load(response)["moonMotor"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Repeat motor start/stop over Art-Net ch469 and count misses by eye.")
    parser.add_argument("--units", type=parse_units, default=[],
                        help="轉燈編號：5、1-4,9、all（IP = 2.0.0.(200 + 編號)） / spinner numbers")
    parser.add_argument("--ip", action="append", default=[], help="直接給 WLED 的 IP，可重複 / explicit WLED IP, repeatable")
    parser.add_argument("--cycles", type=int, default=20, help="啟動＋停止的次數（預設 20） / start+stop cycles")
    parser.add_argument("--on-seconds", type=float, default=12.0, help="每次送啟動多久（預設 12） / run command time")
    parser.add_argument("--off-seconds", type=float, default=12.0, help="每次送停止多久（預設 12） / stop command time")
    parser.add_argument("--stagger", type=float, default=0.0,
                        help="每台比前一台晚幾秒切換（預設 0 = 同時；多台同時啟動要留意電源） / delay per unit")
    parser.add_argument("--source-ip", default="2.0.0.10",
                        help="本機在 Art-Net 網段的 IP（預設 2.0.0.10；\"\" = 不指定） / local Art-Net address")
    parser.add_argument("--universe", type=int, default=0)
    parser.add_argument("--led-level", type=int, default=40, help="指示燈亮度 0-255（預設 40，0 = 不亮） / indicator level")
    parser.add_argument("--no-wled-check", action="store_true",
                        help="不問 WLED（沒有 moonMotor usermod 或 HTTP 不通時） / skip the WLED /json/info checks")
    parser.add_argument("--xiao-port", default="",
                        help="XIAO 的序列埠（例 COM9）：自動判定每次啟停，只能測一台；需要 pyserial "
                             "/ the XIAO's serial port: judges every start and stop automatically (one unit)")
    args = parser.parse_args()

    targets = [(f"unit {unit}", f"2.0.0.{200 + unit}") for unit in args.units] + [(ip, ip) for ip in args.ip]
    if not targets:
        parser.error("give --units or --ip")
    if args.cycles < 1 or args.on_seconds <= 0 or args.off_seconds <= 0 or args.stagger < 0:
        parser.error("cycles >= 1, on/off seconds > 0, stagger >= 0")
    if not 0 <= args.led_level <= 255 or not 0 <= args.universe <= 32767:
        parser.error("led-level 0..255, universe 0..32767")
    check = not args.no_wled_check
    if args.xiao_port and len(targets) != 1:
        parser.error("--xiao-port follows one XIAO: give exactly one unit or IP")

    # 別人正在送 Art-Net（多半是後端沒停）就不開始。 / Refuse to start while something else is sending Art-Net.
    if check:
        for label, ip in targets:
            try:
                info = moon_motor(ip)
            except Exception as error:  # noqa: BLE001 - 任何失敗都只是「問不到」。 / Any failure just means "no answer".
                log(f"error: {label} ({ip}): no moonMotor answer from WLED ({error}). "
                    "Check the IP, or pass --no-wled-check.")
                return 2
            if info.get("ageMs", 10_000) < 1500:
                log(f"error: {label} ({ip}) is receiving Art-Net right now (ch469 = {info.get('value')}). "
                    "Stop the backend first: sudo systemctl stop tph-art-museum")
                return 2
            if info.get("inhibit"):
                log(f"error: {label} ({ip}): 禁止運轉 (inhibit) is on in WLED; unlock it first.")
                return 2

    source = (args.source_ip, 0) if args.source_ip else None
    clients = []
    try:
        for _, ip in targets:
            client = StupidArtnet(ip, args.universe & 0x0F, PACKET_SIZE, fps=FPS, source_address=source)
            client.set_simplified(False)
            client.set_net(args.universe >> 8)
            client.set_subnet((args.universe >> 4) & 0x0F)
            clients.append(client)
    except OSError as error:
        log(f"error: cannot open the Art-Net socket from {args.source_ip or 'any address'}: {error}. "
            "Is this host on the Art-Net network? See --source-ip.")
        return 2

    monitor = None
    if args.xiao_port:
        try:
            monitor = XiaoMonitor(args.xiao_port)
        except ImportError:
            log("error: --xiao-port needs pyserial: add `--with pyserial` to `uv run`, or pip install pyserial.")
            return 2
        except Exception as error:  # noqa: BLE001
            log(f"error: cannot open {args.xiao_port}: {error}. Close the Arduino serial monitor if it is open.")
            return 2

    run_packet, stop_packet = packet(True, args.led_level), packet(False, args.led_level)
    period = args.on_seconds + args.off_seconds
    # 開頭先送一段停止：XIAO 要先看過 HIGH 才接受啟動，而且要完全停下。 / Lead in with stop: arms the XIAO.
    lead = args.off_seconds
    total = lead + args.cycles * period + (len(targets) - 1) * args.stagger
    log(f"{len(targets)} unit(s), {args.cycles} cycles, on {args.on_seconds:g} s / off {args.off_seconds:g} s, "
        f"stagger {args.stagger:g} s; about {total / 60:.1f} min. Green = run, red = stop. Ctrl+C stops the motors.")
    log(f"lead-in: stop for {lead:g} s")

    mismatches: dict[str, list[str]] = {label: [] for label, _ in targets}
    unanswered = 0
    pool = ThreadPoolExecutor(max_workers=8)

    def verify(label: str, ip: str, cycle: int, run: bool) -> None:
        nonlocal unanswered
        want = "run" if run else "stop"
        try:
            info = moon_motor(ip)
        except Exception as error:  # noqa: BLE001
            with print_lock:
                unanswered += 1
            log(f"  ? {label} cycle {cycle} {want}: WLED did not answer ({error})")
            return
        if info.get("output") != want:
            mismatches[label].append(f"cycle {cycle} {want}")
            log(f"  ! {label} cycle {cycle}: WLED output is {info.get('output')} ({info.get('reason')}), sent {want}")

    def state(elapsed: float, index: int) -> tuple[int, bool]:
        """(第幾次, 是否送啟動)；0 = 開頭。 / (cycle number, run command); 0 = lead-in."""
        local = elapsed - lead - index * args.stagger
        if local < 0:
            return 0, False
        cycle = int(local // period)
        if cycle >= args.cycles:
            return args.cycles, False
        return cycle + 1, local - cycle * period < args.on_seconds

    # XIAO 自動判定：每個階段（一次啟動或停止）在下一次切換時結算。
    # Automatic verdicts: each phase (one start or stop) is judged when the next edge arrives.
    results: list[tuple[int, bool, str, str]] = []   # (第幾次, 啟動?, ok/MISS/skip, 說明)
    phase: tuple[int, bool, float, str | None] | None = None   # (第幾次, 啟動?, 切換時間, 當時的 XIAO 狀態)

    def judge() -> None:
        if monitor is None or phase is None:
            return
        cycle, run, since, before = phase
        state, seen = monitor.snapshot(since)
        first = f"{(seen[0][0] - since) * 1000:.0f} ms" if seen else None
        if run:
            if before in RUN_STATES:
                verdict, note = "skip", f"already {before} (an earlier stop was missed)"
            elif state == "MOTOR_RUNNING":
                reached = next(at for at, _, to in seen if to == "MOTOR_RUNNING")
                verdict, note = "ok", f"reacted after {first}, running after {reached - since:.1f} s"
            elif not seen:
                verdict, note = "MISS", f"no state change at all, still {state or 'POWER_OFF (assumed)'}"
            else:
                verdict, note = "MISS", f"reacted after {first} but ended in {state}"
        else:
            if before not in RUN_STATES and before != "MOTOR_STOPPING":
                verdict, note = "skip", "was not running (the start was missed)"
            elif state == "POWER_OFF" and seen:
                off = next(at for at, _, to in seen if to == "POWER_OFF")
                verdict, note = "ok", f"reacted after {first}, off after {off - since:.1f} s"
            elif seen and state in ("MOTOR_STOPPING", "POWER_DOWN"):
                # --off-seconds 比緩停短：停止有收到，只是還沒停完。 / Shorter than the soft stop: received, still ramping down.
                verdict, note = "ok", f"reacted after {first}, still {state} when the next start was sent"
            elif not seen:
                verdict, note = "MISS", f"no state change at all, still {state}"
            else:
                verdict, note = "MISS", f"reacted after {first} but ended in {state}"
        results.append((cycle, run, verdict, note))
        log(f"  => cycle {cycle} {'START' if run else 'stop'}: {verdict}: {note}")

    previous = [(0, False)] * len(targets)
    checks: list[tuple[float, str, str, int, bool]] = []  # (到期時間, …) / (due time, …)
    start = time.monotonic()
    frame = 0
    interrupted = False
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= total:
                break
            for index, ((label, ip), client) in enumerate(zip(targets, clients)):
                current = state(elapsed, index)
                if current != previous[index]:
                    cycle, run = current
                    shown = cycle if run else previous[index][0]
                    if 1 <= shown <= args.cycles and (index == 0 or args.stagger > 0):
                        who = label if args.stagger > 0 or len(targets) == 1 else f"{len(targets)} units"
                        log(f"cycle {shown}/{args.cycles}  {'START' if run else 'stop '}  {who}")
                    if monitor is not None:
                        judge()
                        phase = (shown, run, time.monotonic(), monitor.snapshot(0)[0])
                    if check and 1 <= shown <= args.cycles:
                        checks.append((elapsed + 1.0, label, ip, shown, run))
                    previous[index] = current
                client.send(run_packet if current[1] else stop_packet)
            # HTTP 放到別的執行緒：這個迴圈停 1 秒 WLED 就會逾時停馬達。
            # HTTP runs on other threads: a 1 s stall here would trip the WLED timeout and stop the motors.
            while checks and checks[0][0] <= elapsed:
                _, label, ip, cycle, run = checks.pop(0)
                pool.submit(verify, label, ip, cycle, run)
            frame += 1
            time.sleep(max(0.0, start + frame / FPS - time.monotonic()))
        judge()
    except KeyboardInterrupt:
        interrupted = True
        log("interrupted: stopping the motors")
    finally:
        # 結束：送 1 秒停止（紅燈）再關燈。 / Finish: one second of stop, then LEDs off.
        dark = packet(False, 0)
        for step in range(FPS + 5):
            for client in clients:
                client.send(stop_packet if step < FPS else dark)
            time.sleep(1 / FPS)
        pool.shutdown(wait=True)
        if monitor is not None:
            monitor.close()

    done = "stopped early" if interrupted else f"{args.cycles} cycles done"
    log(f"{done}. Motors are being sent nothing now; WLED stops them after its 1 s timeout.")
    if check:
        bad = {label: items for label, items in mismatches.items() if items}
        if bad:
            for label, items in bad.items():
                log(f"WLED mismatch {label}: {len(items)} ({', '.join(items)})")
        else:
            log("WLED output matched every command that was checked"
                + (f" ({unanswered} check(s) got no answer)." if unanswered else "."))
        log("WLED right but motor wrong = the XIAO missed it. Write down the misses you saw per unit.")
    if monitor is not None:
        for run, name in ((True, "start"), (False, "stop")):
            mine = [item for item in results if item[1] == run]
            missed = [str(item[0]) for item in mine if item[2] == "MISS"]
            skipped = sum(item[2] == "skip" for item in mine)
            log(f"XIAO {name}: {len(missed)} missed of {len(mine) - skipped} judged"
                + (f" (cycles {', '.join(missed)})" if missed else "")
                + (f"; {skipped} not judged" if skipped else ""))
        if monitor.error:
            log(f"warning: the XIAO serial port failed during the run ({monitor.error}); later verdicts are not valid.")
        elif not monitor.transitions:
            log("warning: the XIAO printed no state change at all: wrong port, or its firmware has no serial log.")
    return 1 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
