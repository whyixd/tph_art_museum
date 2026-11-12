"""Preset and motor-command API; Art-Net output remains in the main loop."""

from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Any, Mapping

from .effects import preset_to_json
from .fixture_effects import fixture_preset_to_json, parse_preset_document
from .hardware import default_motor_channel
from .motor import MotorGate

FIXTURE_GROUPS = ("ball", "wash")



class PresetStore:
    def __init__(self, preset_path: str | Path, pixels: int, *, motor_gate: MotorGate | None = None) -> None:
        self.path = Path(preset_path)
        # 輸出迴圈送出前延後啟動（見 motor.py）；None = 照 motor_units 直接送。 / Holds starts back (motor.py).
        self.motor_gate = motor_gate
        self.pixels = pixels
        self.lock = Lock()
        document = parse_preset_document(json.loads(self.path.read_text(encoding="utf-8")), pixels)
        if "spinner" not in document:
            raise ValueError("preset file must include a spinner preset")
        self.active = document["spinner"]
        self.raw = preset_to_json(self.active)
        # 單一效果模式下的球燈／洗牆燈 preset；None = 全暗。 / Single-effect ball / wash presets; None = dark.
        self.fixtures: dict[str, Any] = {group: document.get(group) for group in FIXTURE_GROUPS}
        self.revision = 0
        # 轉動中的轉燈編號；後端重啟後一律停止。 / Units whose motor runs; all stopped after a backend restart.
        self.motor_units: set[int] = set()
        # 全部關燈：轉燈、球燈、洗牆燈送全黑，播放在背景照常前進；只在記憶體，後端重啟後恢復亮燈。
        # Blackout: spinners, balls and washes send black while playback keeps advancing; memory only.
        self.blackout = False
        # 總亮度 0..1：轉燈、球燈、洗牆燈送出前一起乘上（效果、演出、單色測試、閃拍都算）；只在記憶體，後端重啟後回到 1。
        # Master level 0..1: scales spinners, balls and washes at output (effects, shows, tests and beat flash); memory only.
        self.master_level = 1.0

    def snapshot(self) -> Mapping[str, Any]:
        with self.lock:
            return self.active

    def fixture_snapshot(self) -> Mapping[str, Any]:
        with self.lock:
            return dict(self.fixtures)

    def motor_value(self) -> int:
        """255 while any unit's motor runs, else 0 (summary)."""
        with self.lock:
            return 255 if self.motor_units else 0

    def motor_values(self, unit_count: int) -> tuple[int, ...]:
        """Motor command per unit (index = unit - 1): 255 = soft start / run, 0 = soft stop."""
        with self.lock:
            return tuple(255 if unit in self.motor_units else 0 for unit in range(1, unit_count + 1))

    def blackout_active(self) -> bool:
        """Called by the output loop every frame."""
        with self.lock:
            return self.blackout

    def master_level_value(self) -> float:
        """Called by the output loop every frame."""
        with self.lock:
            return self.master_level

    def apply_master_level(self, raw: Any, config: Mapping[str, Any]) -> dict[str, Any]:
        """``{"level": 0..1}``: brightness of every light. Motors are not touched."""
        if not isinstance(raw, dict) or set(raw) != {"level"} or type(raw["level"]) not in (int, float) \
                or not 0 <= raw["level"] <= 1:
            raise ValueError("master level must be {\"level\": 0..1}")
        with self.lock:
            level = float(raw["level"])
            if level != self.master_level:
                self.master_level = level
                self.revision += 1
            return self._state_unlocked(config)

    def apply_blackout(self, raw: Any, config: Mapping[str, Any]) -> dict[str, Any]:
        """``{"enabled": bool}``: all lights off (black) or back to normal. Motors are not touched."""
        if not isinstance(raw, dict) or set(raw) != {"enabled"} or type(raw["enabled"]) is not bool:
            raise ValueError("blackout must be {\"enabled\": true|false}")
        with self.lock:
            if raw["enabled"] != self.blackout:
                self.blackout = raw["enabled"]
                self.revision += 1
            return self._state_unlocked(config)

    def motor_output(self, unit_count: int) -> tuple[int, ...]:
        """What the output loop sends: ``motor_values`` with each start held until the unit has surely stopped."""
        desired = self.motor_values(unit_count)
        return self.motor_gate.apply(desired) if self.motor_gate is not None else desired

    def apply_motor(self, raw: Any, config: Mapping[str, Any]) -> dict[str, Any]:
        """``{"enabled": bool}`` for every unit, or ``{"enabled": bool, "units": [1, 3, ...]}`` for those only.

        全部或指定的轉燈轉動／停止。 / Start or stop every unit, or only the listed ones.
        """
        if not isinstance(raw, dict) or "enabled" not in raw or set(raw) - {"enabled", "units"} \
                or type(raw["enabled"]) is not bool:
            raise ValueError("motor command must be {\"enabled\": true|false, \"units\"?: [unit ids]}")
        count = config["unit_count"]
        if "units" in raw:
            units = raw["units"]
            if not isinstance(units, list) or not units or len(units) > count \
                    or any(type(unit) is not int or not 1 <= unit <= count for unit in units):
                raise ValueError(f"motor units must list unit ids 1..{count}")
            chosen = set(units)
        else:
            chosen = set(range(1, count + 1))
        with self.lock:
            updated = self.motor_units | chosen if raw["enabled"] else self.motor_units - chosen
            if updated != self.motor_units:
                self.motor_units = updated
                self.revision += 1
            return self._state_unlocked(config)

    def state(self, config: Mapping[str, Any]) -> dict[str, Any]:
        with self.lock:
            return self._state_unlocked(config)

    def apply(self, raw: Any, config: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the groups given (an EffectPreset alone is the spinner); ``"ball": null`` turns a group off."""
        update = parse_preset_document(raw, self.pixels)
        with self.lock:
            normalized = update.get("spinner", self.active)
            fixtures = {group: update.get(group, self.fixtures[group]) if group in update else self.fixtures[group]
                        for group in FIXTURE_GROUPS}
            canonical = preset_to_json(normalized)
            # 沒有球燈／洗牆燈時維持舊格式（只有轉燈）。 / Without fixture presets the file keeps the old spinner-only form.
            document: dict[str, Any] = canonical
            if any(preset is not None for preset in fixtures.values()):
                document = {"spinner": canonical, **{
                    group: fixture_preset_to_json(preset) for group, preset in fixtures.items() if preset is not None}}
            encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".preset-", suffix=".tmp", dir=self.path.parent, delete=False) as stream:
                    temporary = stream.name
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
                self.raw = canonical
                self.active = normalized
                self.fixtures = fixtures
                self.revision += 1
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
            return self._state_unlocked(config)

    def _state_unlocked(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "preset": self.raw,
            # enabled／value：有任何一台在轉；units：每台（index = 編號 − 1）。 / enabled / value: any unit runs; units: per unit.
            "motor": {"channel": config.get("motor", {}).get("channel", default_motor_channel(config["pixels"])),
                      "enabled": bool(self.motor_units),
                      "value": 255 if self.motor_units else 0,
                      "units": [unit in self.motor_units for unit in range(1, config["unit_count"] + 1)],
                      # 每台：要轉但還在等前一次緩停結束的剩餘秒數；null = 沒有在等。
                      # Per unit: seconds until a held start is sent; null = not held.
                      **({"waiting": self.motor_gate.waiting(
                          [unit in self.motor_units for unit in range(1, config["unit_count"] + 1)])}
                         if self.motor_gate is not None else {})},
            # true：全部燈光關閉（送全黑），蓋過效果、演出與測試。 / true: every light sends black, over effects, shows and tests.
            "blackout": self.blackout,
            # 總亮度 0..1（全部燈光）。 / Master level 0..1 for every light.
            "masterLevel": self.master_level,
            "output": {
                "ip": config["ip"], "universe": config["universe"],
                "fps": config["fps"], "rpm": config["rpm"], "pixels": config["pixels"],
                "pixelRotation": config.get("hardware", {}).get("pixel_rotation", 0),
                "unitId": config["unit_id"], "unitCount": config["unit_count"],
                "targets": config.get("targets", [{"unit_id": config["unit_id"], "ip": config["ip"], "universe": config["universe"]}]),
            },
            # 球燈／洗牆燈：顆數（patch）與單一效果模式的 preset（null = 全暗）。
            # Ball / wash: fixture count from the patch and the single-effect preset (null = dark).
            "fixtures": {
                group: {
                    "count": len((config.get("dmx") or {}).get("fixtures", {}).get(group, [])),
                    "preset": None if self.fixtures[group] is None else fixture_preset_to_json(self.fixtures[group]),
                }
                for group in FIXTURE_GROUPS
            },
        }


def patch_table(config: Mapping[str, Any]) -> dict[str, Any]:
    """Every output's address for on-site wiring: spinner targets, DMX nodes and each fixture's channels."""
    dmx = config.get("dmx")
    targets = config.get("targets") or [{"unit_id": config["unit_id"], "ip": config["ip"], "universe": config["universe"]}]
    result: dict[str, Any] = {
        "spinners": [
            {"unitId": target["unit_id"], "ip": target["ip"], "universes": target.get("universes", [target["universe"]]),
             "position": (config.get("spinner_positions") or [None] * target["unit_id"])[target["unit_id"] - 1]}
            for target in targets
        ],
        # 全部 unit 的座標（含沒有 target 的），給前端全場模擬。 / Every unit's position (targets or not) for the site view.
        "spinnerPositions": list(config.get("spinner_positions") or [None] * config["unit_count"]),
        "poles": list(config.get("poles") or []),
        "nodes": [dict(node) for node in dmx["nodes"]] if dmx else [],
        "fixtures": {group: [] for group in FIXTURE_GROUPS},
    }
    if not dmx:
        return result
    for group in FIXTURE_GROUPS:
        positions = dmx.get("positions", {}).get(group) or [None] * len(dmx["fixtures"][group])
        for fixture, position in zip(dmx["fixtures"][group], positions, strict=True):
            node = dmx["nodes"][fixture["node"]]
            channels = dmx["profiles"][fixture["profile"]]["channels"]
            result["fixtures"][group].append({
                "id": fixture["id"], "node": node["name"], "ip": node["ip"], "universe": node["universe"],
                "address": fixture["address"], "lastChannel": fixture["address"] + len(channels) - 1,
                "profile": fixture["profile"], "channels": list(channels), "position": position,
            })
    return result


def make_server(
    store: PresetStore,
    config: Mapping[str, Any],
    port: int,
    *,
    host: str = "127.0.0.1",
    sequence_store: Any = None,
    frontend_dir: str | Path | None = None,
    weather_store: Any = None,
    playback_monitor: Any = None,
    unit_color_store: Any = None,
    fixture_color_store: Any = None,
    output_stats: Any = None,
    audio_player: Any = None,
    show_store: Any = None,
    show_player: Any = None,
) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("api port must be 0..65535")
    try:
        address = str(ipaddress.IPv4Address(host))
    except (ipaddress.AddressValueError, TypeError):
        raise ValueError("API host must be an IPv4 address") from None
    static_dir = Path(frontend_dir).resolve() if frontend_dir is not None else None
    palette_store = getattr(weather_store, "palette", None)
    if static_dir is not None and not (static_dir.is_dir() and (static_dir / "index.html").is_file()):
        raise ValueError("frontend directory must contain index.html")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(static_dir) if static_dir else None, **kwargs)

        def _respond(self, status: int, value: Mapping[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _sequence_state(self) -> dict[str, Any]:
            result = sequence_store.state()
            result["output"] = {
                "fps": config["fps"], "rpm": config["rpm"],
                "unitCount": config["unit_count"],
            }
            return result

        def _audio_track(self, rest: str) -> None:
            """``<id>``: the beat map; ``<id>/wav``: the WAV file, streamed, for the browser preview."""
            track_id, _, kind = rest.partition("/")
            beat_map = audio_player.beat_map(track_id)
            if beat_map is None or kind not in ("", "wav"):
                self._respond(404, {"error": "unknown track"})
                return
            if kind == "":
                self._respond(200, beat_map)
                return
            path = audio_player.wav_file(track_id)
            if path is None:
                self._respond(404, {"error": "audio file is not on the host"})
                return
            with path.open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while chunk := source.read(1 << 16):
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _shows_state(self) -> dict[str, Any]:
            return {**show_store.state(), **show_player.state()}

        def _stop_show(self) -> None:
            # 手動播放、套用效果或串接時先停止演出。 / Manual playback, presets and sequences stop the show first.
            if show_player is not None:
                show_player.stop()

        def _track_id(self) -> str | None:
            """The track id of /api/audio/tracks/<id> (not .../wav), or None for other paths."""
            prefix = "/api/audio/tracks/"
            if audio_player is None or not self.path.startswith(prefix) or "/" in self.path[len(prefix):]:
                return None
            return self.path[len(prefix):]

        def _refresh_show(self, track_id: str) -> None:
            # 修正拍點後，播放中的演出（同一首）改用新拍點。 / A playing show on the corrected track picks up the new beats.
            if show_player is None:
                return
            playing = show_player.state()["playing"]
            if playing is not None and playing["trackId"] == track_id:
                try:
                    show_player.replace(show_store.compiled(playing["showId"]))
                except ValueError:
                    show_player.stop()

        def _show_id(self) -> str | None:
            """The show id of /api/shows/<id>, or None for other paths."""
            prefix = "/api/shows/"
            if show_store is None or not self.path.startswith(prefix):
                return None
            return self.path[len(prefix):]

        def do_GET(self) -> None:
            if self.path == "/api/weather":
                self._respond(200, weather_store.snapshot() if weather_store is not None else {"enabled": False})
            elif self.path == "/api/weather/palette" and palette_store is not None:
                self._respond(200, palette_store.state())
            elif self.path == "/api/state":
                self._respond(200, store.state(config))
            elif self.path == "/api/sequence" and sequence_store is not None:
                self._respond(200, self._sequence_state())
            elif self.path == "/api/playback":
                result = playback_monitor.state() if playback_monitor is not None else {"active": False}
                test = unit_color_store.snapshot() if unit_color_store is not None else None
                if result["active"] and test is not None and test["active"]:
                    # 讓 3D 鏡像也顯示單色測試。 / Lets the 3D mirror show the unit colour test.
                    result["unitColors"] = test["colors"]
                fixture_test = fixture_color_store.snapshot() if fixture_color_store is not None else {}
                if result["active"] and any(entry["active"] for entry in fixture_test.values()):
                    result["fixtureColors"] = {group: entry["colors"] for group, entry in fixture_test.items() if entry["active"]}
                if result["active"]:
                    # 總亮度：3D 鏡像跟著變暗。 / Master level: the 3D mirror dims with it.
                    result["masterLevel"] = store.master_level_value()
                if result["active"] and store.blackout_active():
                    # 全部關燈：3D 鏡像也全黑。 / Blackout: the 3D mirror goes dark too.
                    black = {"r": 0, "g": 0, "b": 0}
                    dmx_fixtures = (config.get("dmx") or {}).get("fixtures", {})
                    result["blackout"] = True
                    result["unitColors"] = [black] * config["unit_count"]
                    result["fixtureColors"] = {group: [black] * len(dmx_fixtures.get(group, []))
                                               for group in FIXTURE_GROUPS if dmx_fixtures.get(group)}
                self._respond(200, result)
            elif self.path == "/api/test/units" and unit_color_store is not None:
                self._respond(200, unit_color_store.snapshot())
            elif self.path == "/api/test/fixtures" and fixture_color_store is not None:
                self._respond(200, fixture_color_store.snapshot())
            elif self.path == "/api/patch":
                self._respond(200, patch_table(config))
            elif self.path == "/api/output":
                # 輸出迴圈的影格時間（--api-only 時沒有輸出）。 / Output loop frame timing (none with --api-only).
                self._respond(200, output_stats.snapshot() if output_stats is not None else {"active": False})
            elif self.path == "/api/audio" and audio_player is not None:
                self._respond(200, audio_player.state())
            elif self.path == "/api/shows" and show_store is not None:
                self._respond(200, self._shows_state())
            elif self.path.startswith("/api/audio/tracks/") and audio_player is not None:
                self._audio_track(self.path[len("/api/audio/tracks/"):])
            elif self.path.startswith("/api/") or static_dir is None:
                self._respond(404, {"error": "not found"})
            else:
                super().do_GET()

        def do_HEAD(self) -> None:
            if self.path.startswith("/api/") or static_dir is None:
                self.send_error(404)
            else:
                super().do_HEAD()

        def do_DELETE(self) -> None:
            if self.path == "/api/weather/override" and weather_store is not None:
                self._respond(200, weather_store.clear_override())
            elif self.path == "/api/weather/visit" and weather_store is not None:
                self._respond(200, weather_store.end_visit())
            elif self.path == "/api/test/units" and unit_color_store is not None:
                self._respond(200, unit_color_store.clear())
            elif self.path == "/api/test/fixtures" and fixture_color_store is not None:
                self._respond(200, fixture_color_store.clear())
            elif self.path == "/api/audio/play" and audio_player is not None:
                self._stop_show()
                self._respond(200, audio_player.stop())
            elif self.path == "/api/show/play" and show_player is not None:
                show_player.stop()
                self._respond(200, self._shows_state())
            elif self._track_id() is not None:
                try:
                    beat_map = audio_player.revert_beat_map(self._track_id())
                except ValueError as error:
                    self._respond(422, {"error": str(error)})
                    return
                except OSError:
                    self._respond(500, {"error": "data could not be saved"})
                    return
                self._refresh_show(beat_map["id"])
                self._respond(200, beat_map)
            elif self._show_id() is not None:
                show_id = self._show_id()
                try:
                    show_store.delete(show_id)
                except KeyError:
                    self._respond(404, {"error": "unknown show"})
                    return
                except OSError:
                    self._respond(500, {"error": "data could not be saved"})
                    return
                playing = show_player.state()["playing"]
                if playing is not None and playing["showId"] == show_id:
                    show_player.stop()
                self._respond(200, self._shows_state())
            else:
                self._respond(404, {"error": "not found"})

        def do_PUT(self) -> None:
            limits = {
                "/api/preset": 32768, "/api/motor": 64, "/api/blackout": 64, "/api/master": 64, "/api/sequence": 262144,
                "/api/weather/override": 256, "/api/weather/palette": 16384,
                "/api/playback/restart": 64, "/api/weather/visit": 64, "/api/test/units": 4096,
                "/api/test/fixtures": 8192, "/api/audio/play": 512, "/api/audio/calibration": 128,
                "/api/show/play": 256, "/api/shows/": 262144, "/api/audio/tracks/": 524288,
            }
            show_id = self._show_id()
            track_id = self._track_id()
            route = "/api/shows/" if show_id is not None else "/api/audio/tracks/" if track_id is not None else self.path
            if (route not in limits
                    or (self.path in ("/api/sequence", "/api/playback/restart") and sequence_store is None)
                    or (self.path in ("/api/weather/override", "/api/weather/visit") and weather_store is None)
                    or (self.path == "/api/weather/palette" and palette_store is None)
                    or (self.path == "/api/test/units" and unit_color_store is None)
                    or (self.path == "/api/test/fixtures" and fixture_color_store is None)
                    or (route.startswith("/api/audio/") and audio_player is None)
                    or (self.path == "/api/show/play" and show_player is None)):
                self._respond(404, {"error": "not found"})
                return
            if self.headers.get_content_type() != "application/json":
                self._respond(415, {"error": "Content-Type must be application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = 0
            limit = limits[route]
            if not 0 < length <= limit:
                self._respond(413, {"error": f"JSON must be 1..{limit} bytes"})
                return
            try:
                raw = json.loads(self.rfile.read(length))
                if track_id is not None:
                    result = dict(audio_player.save_beat_map(track_id, raw))
                    self._refresh_show(track_id)
                elif show_id is not None:
                    _, compiled = show_store.save(show_id, raw)
                    show_player.replace(compiled)
                    result = self._shows_state()
                elif self.path == "/api/show/play":
                    show_player.play(raw)
                    result = self._shows_state()
                elif self.path == "/api/sequence":
                    sequence_store.apply(raw)
                    self._stop_show()
                    result = self._sequence_state()
                elif self.path == "/api/playback/restart":
                    sequence_store.restart(raw)
                    self._stop_show()
                    result = self._sequence_state()
                elif self.path == "/api/preset":
                    result = store.apply(raw, config)
                    self._stop_show()
                    if sequence_store is not None:
                        sequence_store.use_single_preset()
                elif self.path == "/api/weather/override":
                    result = weather_store.set_override(raw)
                elif self.path == "/api/weather/visit":
                    result = weather_store.start_visit(raw)
                elif self.path == "/api/weather/palette":
                    result = palette_store.apply(raw)
                elif self.path == "/api/test/units":
                    result = unit_color_store.apply(raw)
                elif self.path == "/api/test/fixtures":
                    result = fixture_color_store.apply(raw)
                elif self.path == "/api/audio/play":
                    self._stop_show()
                    result = audio_player.play(raw)
                elif self.path == "/api/audio/calibration":
                    result = audio_player.set_calibration(raw)
                elif self.path == "/api/blackout":
                    result = store.apply_blackout(raw, config)
                elif self.path == "/api/master":
                    result = store.apply_master_level(raw, config)
                else:
                    result = store.apply_motor(raw, config)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                self._respond(422, {"error": str(error)})
                return
            except OSError:
                self._respond(500, {"error": "data could not be saved"})
                return
            self._respond(200, result)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    return ThreadingHTTPServer((address, port), Handler)
