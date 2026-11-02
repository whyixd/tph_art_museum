"""Spinner hardware mapping: one unit's logical DMX channel stream, split into Art-Net universes.

Pure functions only (no sockets, no state). A unit's channels are numbered from 1 in one logical
stream: pixel channels first (in the strip's colour order), then the motor channel. The stream is
split into consecutive universes of ``channels_per_universe`` channels (WLED's multi-universe
convention: 510 = 170 RGB pixels per universe). Each universe's packet is padded to an even length
as ArtDmx requires.

With the defaults (156 pixels, RGB, brightness 1, motor channel 469, 510 channels per universe)
this is byte-for-byte the Moon Observe reference payload: one 470-byte packet.

轉燈硬體對應：每台一條邏輯通道串（LED 在前、馬達通道在後），依每個 universe 的通道數切成連續的 universe。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .effects import Rgb, clamp01

DMX_CHANNELS = 512
CHANNELS_PER_PIXEL = 3
COLOR_ORDERS = ("RGB", "RBG", "GRB", "GBR", "BRG", "BGR")
MOTOR_VALUES = (0, 255)  # 緩停／緩啟動（reference ch469）。 / Soft stop / soft start.


def default_motor_channel(pixels: int) -> int:
    """The channel right after the pixels, as in the reference (156 px -> 469)."""
    return pixels * CHANNELS_PER_PIXEL + 1


def channel_count(pixels: int, motor_channel: int) -> int:
    return max(pixels * CHANNELS_PER_PIXEL, motor_channel)


def universe_count(pixels: int, motor_channel: int, channels_per_universe: int) -> int:
    return math.ceil(channel_count(pixels, motor_channel) / channels_per_universe)


def packet_sizes(pixels: int, motor_channel: int, channels_per_universe: int) -> list[int]:
    """Packet length per universe: the channels it carries, padded to even (ArtDmx)."""
    total = channel_count(pixels, motor_channel)
    sizes = []
    for start in range(0, total, channels_per_universe):
        size = min(channels_per_universe, total - start)
        sizes.append(size + size % 2)
    return sizes


def _channel_byte(value: float, brightness: float) -> int:
    # 與參考系統相同的捨入：floor(x + 0.5)，不是 Python 的銀行家捨入。
    # Same rounding as the reference: floor(x + 0.5), not banker's rounding.
    return math.floor(clamp01(clamp01(value) * brightness) * 255 + 0.5)


def led_bytes(pixels: Sequence[Rgb], color_order: str = "RGB", brightness: float = 1.0) -> bytes:
    """Quantize RGB (0..1) pixels to bytes in the strip's colour order, scaled by brightness."""
    if color_order not in COLOR_ORDERS:
        raise ValueError(f"color order must be one of {', '.join(COLOR_ORDERS)}")
    if type(brightness) not in (int, float) or not math.isfinite(brightness) or not 0 <= brightness <= 1:
        raise ValueError("brightness must be between 0 and 1")
    order = tuple("RGB".index(channel) for channel in color_order)
    if brightness == 1:
        return bytes(math.floor(clamp01(pixel[channel]) * 255 + 0.5) for pixel in pixels for channel in order)
    return bytes(_channel_byte(pixel[channel], brightness) for pixel in pixels for channel in order)


def build_packets(
    pixels: Sequence[Rgb],
    *,
    hardware: Mapping[str, Any],
    motor_channel: int,
    motor_value: int,
) -> list[bytes]:
    """One unit's packets, one per universe in order.

    ``hardware`` is the validated ``[hardware]`` config section.
    """
    if motor_value not in MOTOR_VALUES:
        raise ValueError("motor value must be 0 or 255")
    leds = led_bytes(pixels, hardware["color_order"], hardware["brightness"])
    if len(leds) >= motor_channel:
        raise ValueError("pixel channels overlap the motor channel")
    stream = bytearray(channel_count(len(pixels), motor_channel))
    stream[:len(leds)] = leds
    stream[motor_channel - 1] = motor_value
    size = hardware["channels_per_universe"]
    packets = []
    for start in range(0, len(stream), size):
        chunk = stream[start:start + size]
        if len(chunk) % 2:
            chunk.append(0)
        packets.append(bytes(chunk))
    return packets
