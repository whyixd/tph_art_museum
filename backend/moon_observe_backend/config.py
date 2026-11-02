"""Minimal TOML configuration for Art-Net outputs, spinner hardware and optional weather."""

from __future__ import annotations

import ipaddress
import json
import math
from pathlib import Path
import tomllib
from typing import Any

from .effects import validate_preset
from .hardware import CHANNELS_PER_PIXEL, COLOR_ORDERS, default_motor_channel, packet_sizes

MAX_PIXELS = 2048
MAX_UNITS = 64
MAX_UNIVERSES_PER_TARGET = 8
# [hardware]、[motor] 整段或個別欄位都可省略；預設值與參考系統逐 byte 相同。
# Both sections and each of their fields are optional; the defaults reproduce the reference payload.
DEFAULT_HARDWARE = {"color_order": "RGB", "brightness": 1.0, "channels_per_universe": 510}


def _ipv4(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an IPv4 address")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise ValueError(f"{name} must be an IPv4 address") from None
    if address.is_multicast or address.is_unspecified:
        raise ValueError(f"{name} must be a unicast IPv4 address")
    return str(address)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _coordinate(value: Any, name: str, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return float(value)


def _hardware(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_HARDWARE):
        raise ValueError("unknown hardware config field")
    color_order = raw.get("color_order", DEFAULT_HARDWARE["color_order"])
    if color_order not in COLOR_ORDERS:
        raise ValueError(f"hardware.color_order must be one of {', '.join(COLOR_ORDERS)}")
    channels = _integer(raw.get("channels_per_universe", DEFAULT_HARDWARE["channels_per_universe"]),
                        "hardware.channels_per_universe", CHANNELS_PER_PIXEL, 510)
    if channels % CHANNELS_PER_PIXEL:
        # 一顆燈的三個通道不可跨 universe（與 WLED 相同）。 / A pixel never straddles two universes (as WLED).
        raise ValueError(f"hardware.channels_per_universe must be a multiple of {CHANNELS_PER_PIXEL}")
    return {
        "color_order": color_order,
        "brightness": _coordinate(raw.get("brightness", DEFAULT_HARDWARE["brightness"]), "hardware.brightness", 0, 1),
        "channels_per_universe": channels,
    }


def _motor(raw: Any, pixels: int, channels_per_universe: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - {"channel"}:
        raise ValueError("unknown motor config field")
    led_channels = pixels * CHANNELS_PER_PIXEL
    channel = _integer(raw.get("channel", default_motor_channel(pixels)), "motor.channel",
                       led_channels + 1, MAX_UNIVERSES_PER_TARGET * channels_per_universe)
    return {"channel": channel}


def _file_name(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must name a JSON file")
    return value


def load_config(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load host/network settings from TOML and EffectPreset v2 from JSON."""
    config_path = Path(path).resolve()
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)
    sections = set(raw)
    if not {"artnet", "render"} <= sections or sections - {"artnet", "render", "hardware", "motor", "weather"} \
            or not isinstance(raw["artnet"], dict) or not isinstance(raw["render"], dict):
        raise ValueError("config requires [artnet] and [render] sections (optional: [hardware], [motor], [weather])")
    artnet = raw["artnet"]
    render = raw["render"]
    if set(artnet) - {"ip", "universe", "source_ip", "targets"} or set(render) - {"preset", "sequence", "fps", "rpm", "pixels", "unit_id", "unit_count"}:
        raise ValueError("unknown config field")
    source_ip = _ipv4(artnet["source_ip"], "artnet.source_ip") if "source_ip" in artnet else None
    fps = _integer(render.get("fps", 40), "render.fps", 1, 120)
    rpm = _integer(render.get("rpm", 360), "render.rpm", 0, 10000)
    pixels = _integer(render.get("pixels", 156), "render.pixels", 1, MAX_PIXELS)
    unit_count = _integer(render.get("unit_count", 9), "render.unit_count", 1, MAX_UNITS)
    unit_id = _integer(render.get("unit_id", 1), "render.unit_id", 1, unit_count)
    hardware = _hardware(raw.get("hardware", {}))
    if pixels * CHANNELS_PER_PIXEL >= MAX_UNIVERSES_PER_TARGET * hardware["channels_per_universe"]:
        raise ValueError(f"render.pixels needs more than {MAX_UNIVERSES_PER_TARGET} universes per unit")
    motor = _motor(raw.get("motor", {}), pixels, hardware["channels_per_universe"])
    sizes = packet_sizes(pixels, motor["channel"], hardware["channels_per_universe"])

    if "targets" in artnet:
        if "ip" in artnet or "universe" in artnet or not isinstance(artnet["targets"], list) or not artnet["targets"]:
            raise ValueError("artnet.targets must replace ip/universe and contain at least one target")
        targets = []
        for index, target in enumerate(artnet["targets"]):
            if not isinstance(target, dict) or set(target) != {"unit_id", "ip", "universe"}:
                raise ValueError("each artnet target requires unit_id, ip and universe")
            targets.append({
                "unit_id": _integer(target["unit_id"], f"artnet.targets[{index}].unit_id", 1, unit_count),
                "ip": _ipv4(target["ip"], f"artnet.targets[{index}].ip"),
                "universe": _integer(target["universe"], f"artnet.targets[{index}].universe", 0, 32767),
            })
        if len({target["unit_id"] for target in targets}) != len(targets):
            raise ValueError("artnet target unit_id values must be unique")
    else:
        targets = [{
            "unit_id": unit_id, "ip": _ipv4(artnet.get("ip"), "artnet.ip"),
            "universe": _integer(artnet.get("universe", 0), "artnet.universe", 0, 32767),
        }]
    # 每台從 universe 起連續使用 len(sizes) 個 universe（WLED 的多 universe 慣例）。
    # Each unit uses len(sizes) consecutive universes from its first one (WLED multi-universe convention).
    for index, target in enumerate(targets):
        target["universes"] = [target["universe"] + offset for offset in range(len(sizes))]
        if target["universes"][-1] > 32767:
            raise ValueError(f"artnet target {index} universes exceed 32767")
    pairs = [(target["ip"], universe) for target in targets for universe in target["universes"]]
    if len(set(pairs)) != len(pairs):
        raise ValueError("artnet target IP/universe pairs must be unique (each unit uses "
                         f"{len(sizes)} consecutive universe(s))")

    weather = None
    if "weather" in raw:
        entry = raw["weather"]
        if not isinstance(entry, dict) or set(entry) - {"latitude", "longitude", "interval_minutes", "cloud_step", "palette", "visits"}:
            raise ValueError("unknown weather config field")
        raw_visits = entry.get("visits", [])
        if not isinstance(raw_visits, list) or len(raw_visits) > 24:
            raise ValueError("weather.visits must list at most 24 locations")
        visits = []
        for index, visit in enumerate(raw_visits):
            if not isinstance(visit, dict) or set(visit) != {"name", "latitude", "longitude"}:
                raise ValueError(f"weather.visits[{index}] requires name, latitude and longitude")
            name = visit["name"]
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                raise ValueError(f"weather.visits[{index}].name must be 1..40 characters")
            visits.append({
                "name": name.strip(),
                "latitude": _coordinate(visit["latitude"], f"weather.visits[{index}].latitude", -90, 90),
                "longitude": _coordinate(visit["longitude"], f"weather.visits[{index}].longitude", -180, 180),
            })
        weather = {
            "latitude": _coordinate(entry.get("latitude"), "weather.latitude", -90, 90),
            "longitude": _coordinate(entry.get("longitude"), "weather.longitude", -180, 180),
            "interval_minutes": _integer(entry.get("interval_minutes", 15), "weather.interval_minutes", 1, 1440),
            "cloud_step": _integer(entry.get("cloud_step", 6), "weather.cloud_step", 4, 8),
            "palette_path": str((config_path.parent / _file_name(entry.get("palette", "palette.json"), "weather.palette")).resolve()),
            "visits": visits,
        }

    preset_name = render.get("preset")
    if not isinstance(preset_name, str) or not preset_name:
        raise ValueError("render.preset must name a JSON file")
    preset_path = (config_path.parent / preset_name).resolve()
    sequence_name = render.get("sequence", "sequence.json")
    if not isinstance(sequence_name, str) or not sequence_name:
        raise ValueError("render.sequence must name a JSON file")
    sequence_path = (config_path.parent / sequence_name).resolve()
    with preset_path.open("r", encoding="utf-8") as stream:
        preset = validate_preset(json.load(stream), pixels)
    config = {
        "ip": targets[0]["ip"],
        "source_ip": source_ip,
        "universe": targets[0]["universe"],
        "targets": targets,
        "weather": weather,
        "fps": fps,
        "rpm": rpm,
        "pixels": pixels,
        "hardware": hardware,
        "motor": motor,
        "packet_sizes": sizes,
        "unit_id": targets[0]["unit_id"],
        "unit_count": unit_count,
        "preset_path": str(preset_path),
        "sequence_path": str(sequence_path),
    }
    return config, preset
