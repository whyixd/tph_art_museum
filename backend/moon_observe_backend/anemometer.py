"""風速計測試讀取（「風力」分頁的按鈕）：讀一次，回報原始位元組、來回時間、解析值，以及失敗時可能的原因。

Anemometer test read (the wind tab's button): one read, reporting the raw bytes, the round trip, the decoded
values and, when it fails, what the failure points at. Read only (function 0x03); nothing is written to the sensor.
暫存器（與 hsz 相同）：reg 0 風速 ÷10 m/s、1 蒲福風級、2 風向 ÷10 度、3 16 方位代碼。
Registers (as on hsz): 0 speed /10 m/s, 1 Beaufort, 2 direction /10 deg, 3 16-point code.
"""

from __future__ import annotations

import glob
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping
from typing import Any

from . import modbus

REGISTER = 0
COUNT = 4
DEFAULTS = {"port": "", "baudrate": 9600, "address": 1, "timeout_seconds": 2.0}

# 只允許序列埠裝置，不讓網頁打開任意檔案。 / Serial devices only: the page cannot open arbitrary files.
PORT_PATTERN = re.compile(
    r"/dev/(serial/by-(id|path)/[A-Za-z0-9_:+-][A-Za-z0-9._:+-]*|tty(USB|ACM|AMA)\d{1,3}|cu\.[A-Za-z0-9_-][A-Za-z0-9._-]*)")
PORT_GLOBS = ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*", "/dev/cu.usbserial*", "/dev/cu.usbmodem*")

MESSAGES = {
    "ok": "讀取成功。",
    "no-port": "找不到序列埠：USB 轉 RS485 沒插好，或路徑不對（看下面偵測到的序列埠）。",
    "open-failed": "序列埠打不開（權限、被其他程式占用，或裝置剛拔掉）。",
    "no-response": "沒有收到任何位元組：訊號沒有到風速計或沒有回來。檢查 LoRa 天線／距離、兩台 DTU 的設定、A/B 接線、風速計與 DTU 的電源。",
    "incomplete": "收到的位元組不夠一幀：baud 可能不對，或 DTU 把回應切開、逾時太短（加長逾時再試）。",
    "bad-frame": "收到的資料不是有效的回應（CRC 或格式錯誤）：baud 不對、線路雜訊，或回應的不是這個設備位址。",
    "exception": "風速計回應了錯誤碼：線路是通的，但暫存器或功能碼不對，確認風速計的型號與規格書。",
}
EXCEPTION_NAMES = {1: "不支援的功能碼", 2: "暫存器位址不對", 3: "數值不對", 4: "設備故障"}


def _port_allowed(port: str, pattern: re.Pattern[str]) -> bool:
    return pattern.fullmatch(port) is not None and "/../" not in port + "/" and not port.endswith("/..")


def decode(registers: tuple[int, ...]) -> dict[str, Any]:
    """風速、風向與合理性檢查。 / Speed, direction and the sanity checks."""
    speed = registers[0] / 10
    direction = registers[2] / 10
    code = registers[3]
    expected = round(direction / 22.5) % 16
    difference = abs(code - expected) % 16
    code_matches = code < 16 and min(difference, 16 - difference) <= 1
    return {
        "speedMs": speed, "beaufort": registers[1], "directionDeg": direction, "directionCode": code,
        "codeMatches": code_matches,
        "plausible": 0 <= speed <= 60 and 0 <= direction <= 360 and code_matches,
    }


class AnemometerProbe:
    """一次只做一個讀取；預設值來自 ``config.toml`` 的 ``[wind]``。 / One read at a time; defaults from ``[wind]``."""

    def __init__(
        self, settings: Mapping[str, Any] | None = None, *,
        port_pattern: re.Pattern[str] = PORT_PATTERN,
        read: Callable[..., modbus.Exchange] = modbus.read_holding_registers,
        open_port: Callable[[str, int], int] = modbus.open_port,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.defaults = {**DEFAULTS, **(settings or {})}
        self.port_pattern = port_pattern
        self._read = read
        self._open = open_port
        self._log = log if log is not None else (lambda line: print(line, file=sys.stderr, flush=True))
        self._lock = threading.Lock()

    def ports(self) -> list[str]:
        """偵測到的序列埠；by-id 路徑排前面（不會因為插拔順序改變）。 / Detected ports; stable by-id paths first."""
        found: list[str] = []
        for pattern in PORT_GLOBS:
            for path in sorted(glob.glob(pattern)):
                if _port_allowed(path, self.port_pattern) and path not in found:
                    found.append(path)
        return found

    def state(self) -> dict[str, Any]:
        ports = self.ports()
        port = self.defaults["port"] or (ports[0] if ports else "")
        return {
            "defaults": {
                "port": port, "baudrate": self.defaults["baudrate"], "address": self.defaults["address"],
                "timeoutSeconds": self.defaults["timeout_seconds"],
            },
            "configured": bool(self.defaults["port"]),
            "ports": ports,
            "baudrates": list(modbus.BAUDRATES),
        }

    def _request(self, raw: Any) -> tuple[str, int, int, float]:
        if not isinstance(raw, dict) or set(raw) - {"port", "baudrate", "address", "timeoutSeconds"}:
            raise ValueError("probe takes port, baudrate, address and timeoutSeconds")
        port = raw.get("port", self.defaults["port"])
        if not isinstance(port, str) or not port:
            raise ValueError("請選擇或輸入序列埠")
        if not _port_allowed(port, self.port_pattern):
            raise ValueError("序列埠只能是 /dev/serial/by-id/…、/dev/ttyUSB…、/dev/ttyACM… 這類裝置")
        baudrate = raw.get("baudrate", self.defaults["baudrate"])
        if type(baudrate) is not int or baudrate not in modbus.BAUDRATES:
            raise ValueError(f"baud 必須是 {', '.join(map(str, modbus.BAUDRATES))} 其中之一")
        address = raw.get("address", self.defaults["address"])
        if type(address) is not int or not 1 <= address <= 247:
            raise ValueError("設備位址必須是 1–247")
        timeout = raw.get("timeoutSeconds", self.defaults["timeout_seconds"])
        if type(timeout) not in (int, float) or not 0.1 <= timeout <= 10:
            raise ValueError("逾時必須是 0.1–10 秒")
        return port, baudrate, address, float(timeout)

    def probe(self, raw: Any) -> dict[str, Any]:
        port, baudrate, address, timeout = self._request(raw)
        if not self._lock.acquire(blocking=False):
            raise ValueError("上一個讀取還沒結束")
        try:
            result = self._probe(port, baudrate, address, timeout)
        finally:
            self._lock.release()
        detail = f" {result['detail']}" if result.get("detail") else ""
        rtt = f" rtt {result['roundTripMs']:.0f} ms" if result["roundTripMs"] is not None else ""
        self._log(f"wind probe: {result['kind']} {port} {baudrate} addr {address}{rtt} "
                  f"rx {result['bytes']} byte(s){detail}")
        return result

    def _probe(self, port: str, baudrate: int, address: int, timeout: float) -> dict[str, Any]:
        result: dict[str, Any] = {
            "port": port, "baudrate": baudrate, "address": address, "timeoutSeconds": timeout,
            "sent": modbus.build_read_request(address, REGISTER, COUNT).hex(" "), "received": "", "bytes": 0,
            "roundTripMs": None, "noiseBytes": 0, "registers": None, "values": None, "detail": None,
        }

        def done(kind: str, detail: str | None = None) -> dict[str, Any]:
            result.update(ok=kind == "ok", kind=kind, message=MESSAGES[kind], detail=detail)
            return result

        if not os.path.exists(port):
            return done("no-port")
        try:
            fd = self._open(port, baudrate)
        except OSError as error:
            return done("open-failed", error.strerror or str(error))
        try:
            exchange = self._read(fd, address, REGISTER, COUNT, timeout)
        except OSError as error:
            return done("open-failed", f"讀寫失敗：{error.strerror or error}")
        finally:
            os.close(fd)
        result["received"] = exchange.received.hex(" ")
        result["bytes"] = len(exchange.received)
        if exchange.round_trip_seconds is not None:
            result["roundTripMs"] = round(exchange.round_trip_seconds * 1000, 1)
        frame = exchange.frame
        if frame is None:
            if not exchange.received:
                return done("no-response")
            if len(exchange.received) < modbus.response_length(COUNT):
                return done("incomplete")
            return done("bad-frame")
        result["noiseBytes"] = frame.offset
        if frame.registers is None:
            code = frame.exception_code
            return done("exception", f"例外碼 {code}（{EXCEPTION_NAMES.get(code, '其他')}）")
        result["registers"] = list(frame.registers)
        result["values"] = decode(frame.registers)
        notes = []
        if frame.offset:
            notes.append(f"回應前面有 {frame.offset} 個多餘的位元組（線路雜訊）")
        if not result["values"]["plausible"]:
            notes.append("數值不合理：風向角度與 16 方位代碼對不上，或超出範圍；暫存器的意義可能與 hsz 不同")
        return done("ok", "；".join(notes) or None)
