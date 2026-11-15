"""Modbus RTU over a serial port, standard library only (``termios``; no pyserial on the Pi).

只讀保持暫存器（功能碼 0x03），給風速計用。回應可能分好幾段到（經 LoRa DTU），所以累積讀到完整的一幀或逾時。
Reads holding registers (function 0x03) for the anemometer. A reply may arrive in pieces (through the LoRa DTU),
so bytes are collected until a whole frame is there or the timeout runs out.
"""

from __future__ import annotations

import os
import select
import time
from collections.abc import Callable
from dataclasses import dataclass

READ_HOLDING_REGISTERS = 0x03
BAUDRATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)


def crc16(data: bytes) -> int:
    """Modbus CRC-16 (poly 0xA001, init 0xFFFF); sent low byte first."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _with_crc(body: bytes) -> bytes:
    crc = crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def build_read_request(address: int, register: int, count: int) -> bytes:
    """``01 03 00 00 00 04 44 09`` for address 1, register 0, 4 registers."""
    if not 1 <= address <= 247 or not 0 <= register <= 0xFFFF or not 1 <= count <= 125:
        raise ValueError("Modbus address 1..247, register 0..65535, count 1..125")
    return _with_crc(bytes((address, READ_HOLDING_REGISTERS, register >> 8, register & 0xFF, count >> 8, count & 0xFF)))


def response_length(count: int) -> int:
    """Address, function, byte count, 2 bytes per register, CRC."""
    return 5 + 2 * count


@dataclass(frozen=True)
class Frame:
    """一幀有效的回應。 / One valid reply frame."""

    offset: int  # 前面多出的位元組數（雜訊）。 / Stray bytes before the frame (line noise).
    registers: tuple[int, ...] | None  # None = 例外回應。 / None for an exception reply.
    exception_code: int | None = None


def find_frame(data: bytes, address: int, count: int) -> Frame | None:
    """找出 ``data`` 裡第一幀有效的回應（正常或例外），容許前面有雜訊。 / The first valid reply, allowing leading noise."""
    normal = response_length(count)
    for offset in range(len(data)):
        if data[offset] != address or offset + 1 >= len(data):
            continue
        function = data[offset + 1]
        if function == READ_HOLDING_REGISTERS and offset + normal <= len(data):
            frame = data[offset:offset + normal]
            if frame[2] == 2 * count and crc16(frame[:-2]) == frame[-2] | frame[-1] << 8:
                body = frame[3:-2]
                return Frame(offset, tuple(body[i] << 8 | body[i + 1] for i in range(0, len(body), 2)))
        elif function == READ_HOLDING_REGISTERS | 0x80 and offset + 5 <= len(data):
            frame = data[offset:offset + 5]
            if crc16(frame[:3]) == frame[3] | frame[4] << 8:
                return Frame(offset, None, frame[2])
    return None


def open_port(port: str, baudrate: int) -> int:
    """以 8N1、raw、非阻塞開啟序列埠，回傳 fd。 / Open the port raw 8N1, non-blocking; returns the fd."""
    import termios  # 只在 POSIX。 / POSIX only.

    if baudrate not in BAUDRATES:
        raise ValueError(f"baudrate must be one of {', '.join(map(str, BAUDRATES))}")
    speed = getattr(termios, f"B{baudrate}")
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0  # iflag：不轉換 CR/LF、不做軟體流量控制。 / No CR/LF mapping, no XON/XOFF.
        attrs[1] = 0  # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # 8N1，不看數據機線，關閉時不掛斷。 / 8N1, no modem lines.
        attrs[3] = 0  # lflag：raw。 / Raw mode.
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except BaseException:
        os.close(fd)
        raise
    return fd


@dataclass(frozen=True)
class Exchange:
    """一次要求與回應。 / One request and what came back."""

    sent: bytes
    received: bytes
    frame: Frame | None
    # 送出到收到最後一個位元組（沒有收到 = None）。 / From sending to the last byte received (None if nothing came).
    round_trip_seconds: float | None


def read_holding_registers(
    fd: int, address: int, register: int, count: int, timeout: float,
    clock: Callable[[], float] = time.monotonic,
) -> Exchange:
    """送出讀取要求，累積讀到一幀有效的回應或逾時；逾時時回傳收到的全部位元組，讓現場看得到。
    Send the request and collect bytes until a valid frame or the timeout; on timeout everything received is
    returned so it can be shown on site.
    """
    import termios

    request = build_read_request(address, register, count)
    termios.tcflush(fd, termios.TCIFLUSH)  # 丟掉上一次殘留的。 / Drop leftovers from before.
    start = clock()
    written = 0
    while written < len(request):
        try:
            written += os.write(fd, request[written:])
        except BlockingIOError:
            select.select([], [fd], [], max(0.0, start + timeout - clock()))
            if clock() >= start + timeout:
                return Exchange(request, b"", None, None)
    received = bytearray()
    last = None
    while True:
        remaining = start + timeout - clock()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 256)
        except BlockingIOError:
            continue
        if not chunk:
            # pty 另一端關了；序列埠不會。 / The other side of a pty closed; a serial port does not do this.
            break
        received += chunk
        last = clock()
        frame = find_frame(bytes(received), address, count)
        if frame is not None:
            return Exchange(request, bytes(received), frame, last - start)
    return Exchange(request, bytes(received), None, None if last is None else last - start)
