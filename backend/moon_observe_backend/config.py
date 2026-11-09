"""Minimal TOML configuration for Art-Net outputs, spinner hardware and optional weather."""

from __future__ import annotations

import ipaddress
import json
import math
from pathlib import Path
import tomllib
from typing import Any

from .dmx import COLOR_CHANNELS, DMX_CHANNELS, GROUPS, WHITE_MODES
from .fixture_effects import fixture_geometry, parse_preset_document
from .hardware import CHANNELS_PER_PIXEL, COLOR_ORDERS, default_motor_channel, packet_sizes

MAX_PIXELS = 2048
MAX_UNITS = 64
MAX_UNIVERSES_PER_TARGET = 8
# [hardware]、[motor] 整段或個別欄位都可省略；預設值與參考系統逐 byte 相同。
# Both sections and each of their fields are optional; the defaults reproduce the reference payload.
DEFAULT_HARDWARE = {"color_order": "RGB", "brightness": 1.0, "channels_per_universe": 510, "pixel_rotation": 0}


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


def _hardware(raw: Any, pixels: int) -> dict[str, Any]:
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
        # 實體第 i 顆顯示效果的第 (i + rotation) mod pixels 顆。 / Physical LED i shows effect pixel (i + rotation) % pixels.
        "pixel_rotation": _integer(raw.get("pixel_rotation", DEFAULT_HARDWARE["pixel_rotation"]),
                                   "hardware.pixel_rotation", 0, pixels - 1),
    }


def _motor(raw: Any, pixels: int, channels_per_universe: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - {"channel"}:
        raise ValueError("unknown motor config field")
    led_channels = pixels * CHANNELS_PER_PIXEL
    channel = _integer(raw.get("channel", default_motor_channel(pixels)), "motor.channel",
                       led_channels + 1, MAX_UNIVERSES_PER_TARGET * channels_per_universe)
    return {"channel": channel}


def _name(value: Any, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError(f"{name} must be 1..40 characters")
    return value


def _table_list(value: Any, name: str, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum or not all(isinstance(entry, dict) for entry in value):
        raise ValueError(f"{name} must list at most {maximum} tables")
    return value


def _dmx_profile(entry: dict[str, Any], name: str) -> dict[str, Any]:
    if not {"name", "channels"} <= set(entry) or set(entry) - {"name", "channels", "white", "brightness", "static"}:
        raise ValueError(f"{name} requires name and channels (optional: white, brightness, static)")
    channels = entry["channels"]
    if not isinstance(channels, list) or not 1 <= len(channels) <= 32 \
            or not all(isinstance(channel, str) and channel for channel in channels) or len(set(channels)) != len(channels):
        raise ValueError(f"{name}.channels must list 1..32 distinct channel names")
    if not {"r", "g", "b"} <= set(channels):
        raise ValueError(f"{name}.channels must include r, g and b")
    static = entry.get("static", {})
    if not isinstance(static, dict) or set(static) & set(COLOR_CHANNELS):
        raise ValueError(f"{name}.static must map non-colour channels to values")
    others = set(channels) - set(COLOR_CHANNELS)
    if set(static) != others:
        # 非顏色通道（dimmer、strobe…）都要給固定值，避免燈具停在未知狀態。
        # Every non-colour channel (dimmer, strobe, ...) needs a fixed value so fixtures never sit in an unknown mode.
        raise ValueError(f"{name}.static must give a value for exactly: {', '.join(sorted(others)) or '(none)'}")
    white = entry.get("white", "min" if "w" in channels else "none")
    if white not in WHITE_MODES:
        raise ValueError(f"{name}.white must be one of {', '.join(WHITE_MODES)}")
    if white == "min" and "w" not in channels:
        raise ValueError(f"{name}.white = \"min\" needs a w channel")
    return {
        "channels": tuple(channels),
        "white": white,
        "brightness": _coordinate(entry.get("brightness", 1.0), f"{name}.brightness", 0, 1),
        "static": {key: _integer(value, f"{name}.static.{key}", 0, 255) for key, value in static.items()},
    }


def _dmx(raw: Any, spinner_pairs: set[tuple[str, int]]) -> dict[str, Any]:
    """[dmx]: fixture profiles, Art-Net -> DMX node universes, and the patch (where each fixture sits)."""
    if not isinstance(raw, dict) or set(raw) - {"profiles", "nodes", "patch"}:
        raise ValueError("unknown dmx config field")
    profiles: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(_table_list(raw.get("profiles", []), "dmx.profiles", 16)):
        label = f"dmx.profiles[{index}]"
        profile = _dmx_profile(entry, label)
        key = _name(entry["name"], f"{label}.name")
        if key in profiles:
            raise ValueError(f"dmx profile name {key!r} repeats")
        profiles[key] = profile
    nodes: list[dict[str, Any]] = []
    node_index: dict[str, int] = {}
    for index, entry in enumerate(_table_list(raw.get("nodes", []), "dmx.nodes", 32)):
        label = f"dmx.nodes[{index}]"
        if set(entry) != {"name", "ip", "universe"}:
            raise ValueError(f"{label} requires name, ip and universe")
        node = {
            "name": _name(entry["name"], f"{label}.name"),
            "ip": _ipv4(entry["ip"], f"{label}.ip"),
            "universe": _integer(entry["universe"], f"{label}.universe", 0, 32767),
        }
        if node["name"] in node_index:
            raise ValueError(f"dmx node name {node['name']!r} repeats")
        if any((node["ip"], node["universe"]) == (other["ip"], other["universe"]) for other in nodes):
            raise ValueError(f"{label} repeats another node's IP/universe")
        if (node["ip"], node["universe"]) in spinner_pairs:
            raise ValueError(f"{label} IP/universe is already used by a spinner")
        node_index[node["name"]] = len(nodes)
        nodes.append(node)
    fixtures: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUPS}
    used: dict[int, list[tuple[int, int, str]]] = {index: [] for index in range(len(nodes))}
    for index, entry in enumerate(_table_list(raw.get("patch", []), "dmx.patch", 64)):
        label = f"dmx.patch[{index}]"
        required = {"group", "first_id", "count", "profile", "node", "address"}
        if not required <= set(entry) or set(entry) - required - {"address_step"}:
            raise ValueError(f"{label} requires {', '.join(sorted(required))} (optional: address_step)")
        group = entry["group"]
        if group not in GROUPS:
            raise ValueError(f"{label}.group must be one of {', '.join(GROUPS)}")
        profile_name = entry["profile"]
        if profile_name not in profiles:
            raise ValueError(f"{label}.profile {profile_name!r} is not a dmx profile")
        if entry["node"] not in node_index:
            raise ValueError(f"{label}.node {entry['node']!r} is not a dmx node")
        width = len(profiles[profile_name]["channels"])
        first_id = _integer(entry["first_id"], f"{label}.first_id", 1, 999)
        count = _integer(entry["count"], f"{label}.count", 1, DMX_CHANNELS)
        address = _integer(entry["address"], f"{label}.address", 1, DMX_CHANNELS)
        # 預設每顆接續前一顆（位址間隔 = 通道數）。 / By default each fixture follows the previous one.
        step = _integer(entry.get("address_step", width), f"{label}.address_step", 1, DMX_CHANNELS)
        node = node_index[entry["node"]]
        for offset in range(count):
            fixture_id = first_id + offset
            start = address + offset * step
            end = start + width - 1
            if end > DMX_CHANNELS:
                raise ValueError(f"{group} {fixture_id} channels {start}-{end} exceed {DMX_CHANNELS}")
            for other_start, other_end, other in used[node]:
                if start <= other_end and other_start <= end:
                    raise ValueError(f"{group} {fixture_id} channels {start}-{end} overlap {other} "
                                     f"on dmx node {nodes[node]['name']!r}")
            if any(fixture["id"] == fixture_id for fixture in fixtures[group]):
                raise ValueError(f"{group} id {fixture_id} repeats")
            used[node].append((start, end, f"{group} {fixture_id}"))
            fixtures[group].append({"id": fixture_id, "profile": profile_name, "node": node, "address": start})
    for group in GROUPS:
        fixtures[group].sort(key=lambda fixture: fixture["id"])
    return {"profiles": profiles, "nodes": nodes, "fixtures": fixtures}


LAYOUT_SCHEMA_VERSION = 1
LAYOUT_AXES = ("x", "y", "z")
LAYOUT_LIMIT_CM = 100_000


def _position(value: Any, name: str) -> dict[str, float]:
    if not isinstance(value, dict) or not value or set(value) - set(LAYOUT_AXES):
        raise ValueError(f"{name} must have some of x, y, z")
    return {axis: _coordinate(entry, f"{name}.{axis}", -LAYOUT_LIMIT_CM, LAYOUT_LIMIT_CM) for axis, entry in value.items()}


MAX_POLES = 200


def _layout(path: Path, unit_count: int, fixtures: dict[str, list[dict[str, Any]]] | None,
            ) -> tuple[list[dict[str, float] | None], dict[str, list[dict[str, float] | None]], list[dict[str, float]]]:
    """Scene positions (cm) from the layout JSON: spinners by unit id, DMX fixtures by group and id.

    {"schemaVersion": 1,
     "spinners": {"1": {"x": 961, "y": 118, "z": 565}, ...},      # z = spinner centre
     "fixtures": {"ball": {"1": {"x": 961, "y": 202, "z": 340}, ...}, "wash": {...}},
     "poles": [{"x": 961, "y": 169, "z": 1000}, ...]}                 # z = pole top (scene only)
    Origin: inner corner of the left wall and the front wall; x along the long wall, y towards the back
    wall, z above the floor. Any of x / y / z may be left out; for fixture effects an axis missing on any
    fixture of a group falls back to fixture order. Returns positions in unit order and in each group's
    fixture order (None where missing), and the poles (x, y, top z all required; only drawn by the frontend).
    場景座標（cm）：轉燈依 unit 編號、DMX 燈具依群組與編號；x/y/z 可缺。燈桿 x/y/z（頂端）皆必填，只供前端繪製。
    """
    with path.open("r", encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict) or raw.get("schemaVersion") != LAYOUT_SCHEMA_VERSION \
            or set(raw) - {"schemaVersion", "spinners", "fixtures", "poles"}:
        raise ValueError("layout must contain schemaVersion 1 (optional: spinners, fixtures, poles)")
    pole_entries = raw.get("poles", [])
    if not isinstance(pole_entries, list) or len(pole_entries) > MAX_POLES:
        raise ValueError(f"layout poles must list at most {MAX_POLES} positions")
    poles = []
    for index, entry in enumerate(pole_entries):
        position = _position(entry, f"layout pole {index + 1}")
        if set(position) != set(LAYOUT_AXES):
            raise ValueError(f"layout pole {index + 1} needs x, y and z (top)")
        poles.append(position)
    spinner_entries = raw.get("spinners", {})
    if not isinstance(spinner_entries, dict):
        raise ValueError("layout spinners must map unit ids to positions")
    # 超過 unit_count 的轉燈忽略（例如只接一台測試時）。 / Units beyond unit_count are ignored (e.g. a one-unit bench test).
    unknown = sorted(set(spinner_entries) - {str(unit) for unit in range(1, MAX_UNITS + 1)}, key=lambda key: (len(key), key))
    if unknown:
        raise ValueError(f"layout spinner ids {', '.join(unknown)} are not units 1..{MAX_UNITS}")
    spinners = [
        _position(spinner_entries[str(unit)], f"layout spinner {unit}") if str(unit) in spinner_entries else None
        for unit in range(1, unit_count + 1)
    ]
    entries = raw.get("fixtures", {})
    if not isinstance(entries, dict) or set(entries) - set(GROUPS):
        raise ValueError(f"layout fixtures must be keyed by group ({', '.join(GROUPS)})")
    if fixtures is None:
        return spinners, {}, poles  # 沒有 [dmx]：不用燈具座標。 / No [dmx]: fixture positions are unused.
    positions: dict[str, list[dict[str, float] | None]] = {}
    for group in GROUPS:
        by_id = entries.get(group, {})
        if not isinstance(by_id, dict):
            raise ValueError(f"layout {group} must map fixture ids to positions")
        known = {str(fixture["id"]) for fixture in fixtures[group]}
        unknown = sorted(set(by_id) - known, key=lambda key: (len(key), key))
        if unknown:
            raise ValueError(f"layout {group} ids {', '.join(unknown)} are not in the patch")
        parsed = {key: _position(position, f"layout {group} {key}") for key, position in by_id.items()}
        positions[group] = [parsed.get(str(fixture["id"])) for fixture in fixtures[group]]
    return spinners, positions, poles


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
    if not {"artnet", "render"} <= sections or sections - {"artnet", "render", "hardware", "motor", "dmx", "weather"} \
            or not isinstance(raw["artnet"], dict) or not isinstance(raw["render"], dict):
        raise ValueError("config requires [artnet] and [render] sections (optional: [hardware], [motor], [dmx], [weather])")
    artnet = raw["artnet"]
    render = raw["render"]
    if set(artnet) - {"ip", "universe", "source_ip", "targets"} or set(render) - {"preset", "sequence", "fps", "rpm", "pixels", "unit_id", "unit_count", "layout"}:
        raise ValueError("unknown config field")
    source_ip = _ipv4(artnet["source_ip"], "artnet.source_ip") if "source_ip" in artnet else None
    fps = _integer(render.get("fps", 40), "render.fps", 1, 120)
    rpm = _integer(render.get("rpm", 360), "render.rpm", 0, 10000)
    pixels = _integer(render.get("pixels", 156), "render.pixels", 1, MAX_PIXELS)
    unit_count = _integer(render.get("unit_count", 9), "render.unit_count", 1, MAX_UNITS)
    unit_id = _integer(render.get("unit_id", 1), "render.unit_id", 1, unit_count)
    hardware = _hardware(raw.get("hardware", {}), pixels)
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
    dmx = _dmx(raw["dmx"], set(pairs)) if "dmx" in raw else None
    if "layout" in render:
        spinner_positions, fixture_positions, poles = _layout(
            (config_path.parent / _file_name(render["layout"], "render.layout")).resolve(),
            unit_count, dmx["fixtures"] if dmx is not None else None)
    else:
        spinner_positions = [None] * unit_count
        poles = []
        fixture_positions = {group: [None] * len(fixtures) for group, fixtures in dmx["fixtures"].items()} if dmx else {}
    if dmx is not None:
        dmx["positions"] = fixture_positions
        # 每顆的 (高度, x, y)，群組內 0..1；給球燈／洗牆燈效果。 / Per-fixture (height, x, y) within the group, 0..1.
        dmx["geometry"] = {group: fixture_geometry(positions) for group, positions in dmx["positions"].items()}

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
        document = parse_preset_document(json.load(stream), pixels)
    if "spinner" not in document:
        raise ValueError("render.preset must include a spinner preset")
    preset = document["spinner"]
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
        "dmx": dmx,
        "spinner_positions": spinner_positions,
        "poles": poles,
        "unit_id": targets[0]["unit_id"],
        "unit_count": unit_count,
        "preset_path": str(preset_path),
        "sequence_path": str(sequence_path),
    }
    return config, preset
