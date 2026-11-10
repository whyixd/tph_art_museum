"""Continuous effect output through stupidArtnet, without WLED control."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
import tomllib
from threading import Lock, Thread
from typing import Any, Callable, Mapping, Sequence

from stupidArtnet import StupidArtnet

from .audio import AudioPlayer
from .config import DEFAULT_HARDWARE, load_config
from .control import PresetStore, make_server
from .effects import BLACK, Rgb, completion_frames
from .dmx import DMX_CHANNELS, GROUPS, build_universes, patch_report
from .fixture_effects import fixture_geometry, render_fixture_frame
from .hardware import build_packets, default_motor_channel, packet_sizes
from .renderer import render_frame
from .sequence import SequenceStore, render_sequence_fixtures, render_sequence_frame, validate_sequence
from .show import ShowPlayer, ShowStore
from .palette import PaletteStore
from .playback import PlaybackMonitor
from .unit_colors import FixtureColorStore, UnitColorStore
from .weather import WeatherStore

SINGLE_ROUND_SECONDS = 60  # A single effect "round" for weather visits: at least this long.
DMX_EXIT_REPEATS = 2  # 結束時每個 DMX universe 送幾次全 0。 / All-zero frames per DMX universe on exit.


class OutputStats:
    """Frame timing of the output loop, in windows of ``window`` seconds.

    For each window: frames sent, frames skipped because the loop fell behind, and the render-and-send time
    per frame (average and maximum) against the frame budget. ``snapshot`` returns the last complete window;
    ``record`` returns a log line when a window completes.
    輸出迴圈的影格時間：每個區間的已送影格、落後而跳過的影格、每格渲染加送出的平均／最長時間。
    """

    def __init__(self, fps: int, window: float = 60.0) -> None:
        self.fps = fps
        self.window = window
        self.lock = Lock()
        self._start: float | None = None
        self._current = self._empty()
        self._last: dict[str, Any] | None = None

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"frames": 0, "skipped": 0, "total_ms": 0.0, "max_ms": 0.0}

    def record(self, now: float, skipped: int, work_seconds: float) -> str | None:
        with self.lock:
            if self._start is None:
                self._start = now
            current = self._current
            current["frames"] += 1
            current["skipped"] += skipped
            ms = work_seconds * 1000
            current["total_ms"] += ms
            current["max_ms"] = max(current["max_ms"], ms)
            if now - self._start < self.window:
                return None
            self._last = self._summary(current, now - self._start)
            self._current = self._empty()
            self._start = now
            last = self._last
        return (f"output: {last['frames']} frames in {last['windowSeconds']:.0f} s, {last['skippedFrames']} skipped, "
                f"render {last['renderMs']['avg']:.1f} ms avg / {last['renderMs']['max']:.1f} ms max "
                f"(budget {last['budgetMs']:.1f} ms)")

    def _summary(self, current: Mapping[str, Any], seconds: float) -> dict[str, Any]:
        frames = max(1, current["frames"])
        return {
            "windowSeconds": round(seconds, 1), "frames": current["frames"], "skippedFrames": current["skipped"],
            "renderMs": {"avg": round(current["total_ms"] / frames, 2), "max": round(current["max_ms"], 2)},
            "budgetMs": round(1000 / self.fps, 2),
        }

    def snapshot(self) -> dict[str, Any]:
        """Last complete window, or the window so far before the first one completes."""
        with self.lock:
            if self._last is not None:
                return {"active": True, **self._last}
            if self._start is None:
                return {"active": False}
            return {"active": True, "partial": True, **self._summary(self._current, 0.0)}


def fixture_counts(config: Mapping[str, Any]) -> dict[str, int]:
    dmx = config.get("dmx")
    return {group: len(dmx["fixtures"][group]) if dmx else 0 for group in GROUPS}


def round_length(sequence: Any, preset: Mapping[str, Any], fps: int, unit_count: int) -> tuple[int, bool]:
    """(frames per round, whether rounds repeat) for the playback being output."""
    if sequence is not None:
        total = sequence[-1]["start"] + sequence[-1]["duration"]
        return total, sequence[0]["ending"]["kind"] == "loop"
    minimum = SINGLE_ROUND_SECONDS * fps
    cycle = completion_frames(preset, fps, unit_count)
    return (cycle * math.ceil(minimum / cycle) if cycle else minimum), True


def boundary_key(sequence: Any, preset: Mapping[str, Any], playback_frame: int, fps: int,
                 unit_count: int) -> tuple[int, int] | None:
    """Changes when a new sequence step (or, for a single effect, a new cycle) starts; None once a
    non-looping sequence has ended. Deferred weather timing applies when it changes."""
    if sequence is None:
        cycle = completion_frames(preset, fps, unit_count) or round_length(None, preset, fps, unit_count)[0]
        return 0, playback_frame // cycle
    total, looping = round_length(sequence, preset, fps, unit_count)
    if not looping and playback_frame >= total:
        return None
    position = playback_frame % total if looping else playback_frame
    index = next(i for i, step in enumerate(sequence) if position < step["start"] + step["duration"])
    return playback_frame // total, index


def stream(
    config: Mapping[str, Any],
    preset: Mapping[str, Any],
    *,
    seconds: float | None = None,
    client_factory: Callable[..., Any] = StupidArtnet,
    preset_provider: Callable[[], Mapping[str, Any]] | None = None,
    motor_provider: Callable[[], int] | None = None,
    sequence_provider: Callable[[], tuple[str, tuple[dict[str, Any], ...], int]] | None = None,
    weather_provider: Callable[[], Mapping[str, Any]] | None = None,
    playback_reporter: Callable[[str, Any, int, int], None] | None = None,
    playback_advance: Callable[[int, int, bool, bool], None] | None = None,
    unit_colors_provider: Callable[[], Sequence[Rgb | None] | None] | None = None,
    fixture_preset_provider: Callable[[], Mapping[str, Mapping[str, Any] | None]] | None = None,
    fixture_colors_provider: Callable[[], Mapping[str, Sequence[Rgb | None]]] | None = None,
    beat_flash_provider: Callable[[], float | None] | None = None,
    show_provider: Callable[[], tuple[tuple[dict[str, Any], ...], int, int] | None] | None = None,
    stats: OutputStats | None = None,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Send frames continuously; returned count is send calls, not delivery ACKs.

    Spinners: one client per unit universe. DMX (``config["dmx"]``): one client per node universe, a full
    512-channel frame each frame; ball / wash colours come from the sequence step (or the single-effect
    fixture presets), with the same weather, and a fixture colour test overrides them. On exit every DMX
    universe gets all-zero frames. While a beat-flash test plays (``beat_flash_provider`` returns a level),
    every spinner and fixture shows that white level instead; motors are unaffected.

    While a show plays (``show_provider`` returns its schedule, frame and generation), the show replaces the
    sequence / single effect and its frame comes from the music time; weather does not apply. When it ends,
    the standby output restarts from its first frame.
    演出播放時取代串接／單一效果，影格由音樂時間決定，不套用天氣；演出結束後待機輸出從頭播放。
    """
    if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
        raise ValueError("seconds must be positive and finite")
    fps = config["fps"]
    targets = config.get("targets") or [{
        "unit_id": config["unit_id"], "ip": config["ip"], "universe": config["universe"],
    }]
    hardware = config.get("hardware", DEFAULT_HARDWARE)
    motor_channel = config.get("motor", {}).get("channel", default_motor_channel(config["pixels"]))
    sizes = packet_sizes(config["pixels"], motor_channel, hardware["channels_per_universe"])
    # 每台每個 universe 一個 client，依 universe 順序排列。 / One client per unit universe, in universe order.
    clients: list[tuple[Mapping[str, Any], list[Any]]] = []
    dmx = config.get("dmx")
    groups = [group for group in GROUPS if dmx and dmx["fixtures"][group]]
    geometry = {
        group: (dmx.get("geometry") or {}).get(group) or fixture_geometry([None] * len(dmx["fixtures"][group]))
        for group in groups
    } if dmx else {}
    dmx_clients: list[Any] = []
    last_pixels: dict[int, Sequence[Rgb]] = {}
    last_motor_value = 0
    last_sequence_generation: int | None = None
    last_show_generation: int | None = None
    last_weather_generation: int | None = None
    playback_start_frame = 0
    restarts = 0
    last_frame: int | None = None
    last_boundary: tuple[int, int] | None = None
    try:
        for target in targets:
            universes = target.get("universes") or [target["universe"] + offset for offset in range(len(sizes))]
            unit_clients: list[Any] = []
            clients.append((target, unit_clients))
            for universe, size in zip(universes, sizes, strict=True):
                client = client_factory(
                    target["ip"], universe & 0x0F, size, fps=fps,
                    source_address=(config["source_ip"], 0) if config["source_ip"] else None,
                )
                unit_clients.append(client)
                client.set_simplified(False)
                client.set_net(universe >> 8)
                client.set_subnet((universe >> 4) & 0x0F)
        for node in (dmx["nodes"] if dmx else []):
            client = client_factory(
                node["ip"], node["universe"] & 0x0F, DMX_CHANNELS, fps=fps,
                source_address=(config["source_ip"], 0) if config["source_ip"] else None,
            )
            dmx_clients.append(client)
            client.set_simplified(False)
            client.set_net(node["universe"] >> 8)
            client.set_subnet((node["universe"] >> 4) & 0x0F)
        start = clock()
        frame = 0
        sent = 0
        while True:
            elapsed = clock() - start
            if seconds is not None and elapsed >= seconds:
                break
            due = max(0, math.floor(elapsed * fps))
            if due < frame:
                sleep(min((frame / fps) - elapsed, seconds - elapsed if seconds is not None else math.inf))
                continue
            skipped = due - frame
            frame = due
            frame_started = clock()
            show_state = show_provider() if show_provider is not None else None
            show_generation = show_state[2] if show_state is not None else None
            if show_generation != last_show_generation:
                restarts += 1
                last_show_generation = show_generation
                if show_state is None:
                    # 演出結束：待機輸出從頭開始。 / Show over: the standby output starts from its first frame.
                    playback_start_frame = frame
                    last_frame = None
            active_preset = preset_provider() if preset_provider is not None else preset
            if show_state is not None:
                schedule, playback_frame = show_state[0], show_state[1]
                sequence_mode, weather, mode = True, None, "show"
            else:
                sequence_state = sequence_provider() if sequence_provider is not None else None
                sequence_generation = sequence_state[2] if sequence_state is not None else None
                restarted = sequence_generation != last_sequence_generation
                if restarted:
                    playback_start_frame = frame
                    last_sequence_generation = sequence_generation
                sequence_mode = sequence_state is not None and sequence_state[0] == "sequence"
                schedule = sequence_state[1] if sequence_mode else None
                if playback_advance is not None:
                    # 讀取天氣前先回報進度，讓造訪結束與延後的天氣時間效果從這一格生效。
                    # Report progress before reading the weather so visit ends and deferred timing apply from this frame.
                    key = boundary_key(schedule, active_preset, frame - playback_start_frame, fps, config["unit_count"])
                    playback_advance(
                        0 if last_frame is None else frame - last_frame,
                        round_length(schedule, active_preset, fps, config["unit_count"])[0],
                        key is None or key != last_boundary,
                        not sequence_mode,
                    )
                weather = weather_provider() if weather_provider is not None else None
                weather_generation = weather.get("generation") if weather is not None else None
                if weather_generation != last_weather_generation:
                    playback_start_frame = frame
                    restarted = True
                    last_weather_generation = weather_generation
                restarts += restarted
                playback_frame = frame - playback_start_frame
                last_frame = frame
                mode = "sequence" if sequence_mode else "single"
                last_boundary = boundary_key(schedule, active_preset, playback_frame, fps, config["unit_count"])
            motor_value = motor_provider() if motor_provider is not None else 0
            if motor_value not in (0, 255):
                raise ValueError("motor value must be 0 or 255")
            last_motor_value = motor_value
            if playback_reporter is not None:
                playback_reporter(mode, schedule if sequence_mode else active_preset, restarts, playback_frame)
            # 測試單色時，被指定的燈台整條填色；底下的播放照常前進。
            # A unit colour test fills the chosen units; playback underneath keeps advancing.
            unit_colors = unit_colors_provider() if unit_colors_provider is not None else None
            # 閃拍測試（延遲校準）：全部燈隨拍點閃白，蓋過效果與單色測試。
            # Beat-flash test (latency calibration): every light flashes white on the beat, over effects and tests.
            flash = beat_flash_provider() if beat_flash_provider is not None else None
            flash_color: Rgb | None = None if flash is None else (flash, flash, flash)
            # 同一格內畫面相同的燈台共用一次渲染。 / Units showing the same picture share one render per frame.
            shared: dict[Any, list[Rgb]] = {}
            for target, unit_clients in clients:
                unit_id = target["unit_id"]
                test_color = unit_colors[unit_id - 1] if unit_colors is not None and unit_id <= len(unit_colors) else None
                category = weather.get("category") if weather is not None else None
                unit = {
                    "unitId": unit_id,
                    "logicalPixelCount": config["pixels"],
                    "frameOffset": (unit_id - 1) * weather["frameStep"] if category == "cloudy" else 0,
                    # pillar-fill 依柱子的順序。 / pillar-fill's order by pillar.
                    "pillarRank": (config.get("pillar_ranks") or [])[unit_id - 1] if unit_id <= len(config.get("pillar_ranks") or []) else None,
                    # wave-split 用轉燈中心的場地座標。 / wave-split uses the spinner centre in the room.
                    "position": (config.get("spinner_positions") or [])[unit_id - 1] if unit_id <= len(config.get("spinner_positions") or []) else None,
                }
                color_override = weather.get("color") if category is not None else None
                if flash_color is not None:
                    pixels = [flash_color] * config["pixels"]
                elif test_color is not None:
                    pixels = [test_color] * config["pixels"]
                elif sequence_mode:
                    pixels = render_sequence_frame(
                        schedule, unit, playback_frame,
                        unit_count=config["unit_count"], fps=fps, rpm=config["rpm"],
                        rain=category == "rain", color_override=color_override, shared=shared,
                    )
                else:
                    pixels = render_frame(
                        active_preset,
                        unit, playback_frame, unit_count=config["unit_count"],
                        fps=fps, rpm=config["rpm"], raining=category == "rain",
                        color_override=color_override, shared=shared,
                    )
                packets = build_packets(pixels, hardware=hardware, motor_channel=motor_channel, motor_value=motor_value)
                for client, packet in zip(unit_clients, packets, strict=True):
                    client.send(packet)
                    sent += 1
                last_pixels[unit_id] = pixels
            if dmx_clients:
                category = weather.get("category") if weather is not None else None
                color_override = weather.get("color") if category is not None else None
                frame_step = weather["frameStep"] if category == "cloudy" else 0
                fixture_presets = fixture_preset_provider() if fixture_preset_provider is not None else {}
                fixture_tests = fixture_colors_provider() if fixture_colors_provider is not None else {}
                colors: dict[str, list[Rgb]] = {}
                for group in groups:
                    if sequence_mode:
                        group_colors = render_sequence_fixtures(
                            schedule, group, geometry[group], playback_frame, fps=fps,
                            rain=category == "rain", color_override=color_override, frame_step=frame_step,
                        )
                    elif fixture_presets.get(group) is not None:
                        group_colors = render_fixture_frame(
                            fixture_presets[group], geometry[group], playback_frame, fps=fps,
                            raining=category == "rain", color_override=color_override, frame_step=frame_step,
                        )
                    else:
                        group_colors = [BLACK] * len(geometry[group])
                    # 單色測試：指定的燈具填色，其餘照常播放。 / Colour test: chosen fixtures filled, the rest play on.
                    test = fixture_tests.get(group)
                    if test is not None:
                        group_colors = [own if override is None else override
                                        for own, override in zip(group_colors, test, strict=True)]
                    colors[group] = group_colors if flash_color is None else [flash_color] * len(group_colors)
                for client, data in zip(dmx_clients, build_universes(colors, dmx), strict=True):
                    client.send(data)
                    sent += 1
            if stats is not None:
                finished = clock()
                line = stats.record(finished, skipped, finished - frame_started)
                if line is not None:
                    print(line, flush=True)
            frame += 1
        return sent
    finally:
        try:
            # 先停馬達（最重要），再讓 DMX 燈具全暗。 / Stop the motors first (most important), then darken DMX.
            for target, unit_clients in clients:
                try:
                    if last_motor_value == 255 and target["unit_id"] in last_pixels:
                        # 保留最後畫面，只把馬達改成緩停。 / Keep the last picture; only stop the motor.
                        packets = build_packets(last_pixels[target["unit_id"]], hardware=hardware,
                                                motor_channel=motor_channel, motor_value=0)
                        for client, packet in zip(unit_clients, packets, strict=True):
                            client.send(packet)
                finally:
                    for client in unit_clients:
                        client.close()
        finally:
            try:
                # DMX 燈具全暗（含 dimmer 等固定通道）。 / DMX fixtures dark, static channels (dimmer...) included.
                for _ in range(DMX_EXIT_REPEATS):
                    for client in dmx_clients:
                        client.send(bytes(DMX_CHANNELS))
            finally:
                for client in dmx_clients:
                    client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Continuous Art-Net effect output")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.toml")
    parser.add_argument("--seconds", type=float, help="Stop after a duration; otherwise Ctrl+C")
    parser.add_argument("--check", action="store_true", help="Validate TOML and JSON without sending")
    parser.add_argument("--patch-report", action="store_true", help="Print the DMX patch (node, universe, addresses) and exit")
    parser.add_argument("--api-only", action="store_true", help="Serve the control API without Art-Net output")
    parser.add_argument("--api-port", type=int, default=8765, help="Preset API port")
    parser.add_argument("--api-host", default="0.0.0.0", help="Page and API bind IPv4 address")
    parser.add_argument("--frontend-dir", type=Path, help="Built frontend directory containing index.html")
    args = parser.parse_args(argv)
    try:
        config, preset = load_config(args.config)
        if args.patch_report:
            if config["dmx"] is None:
                print("No [dmx] section: no DMX fixtures configured.")
            else:
                print("\n".join(patch_report(config["dmx"])))
            return 0
        if not 0 <= args.api_port <= 65535:
            raise ValueError("api port must be 0..65535")
        sequence_path = Path(config["sequence_path"])
        counts = fixture_counts(config)
        if sequence_path.exists():
            validate_sequence(json.loads(sequence_path.read_text(encoding="utf-8")),
                              config["pixels"], config["fps"], config["unit_count"], counts)
        palette_store = PaletteStore(config["weather"]["palette_path"]) if config["weather"] is not None else None
        dmx_summary = (f", {' + '.join(f'{count} {group}' for group, count in counts.items() if count)} fixture(s) "
                       f"on {len(config['dmx']['nodes'])} DMX universe(s)") if config["dmx"] else ""
        if args.check:
            print(f"OK: {preset['effectId']} -> {len(config['targets'])} Art-Net target(s){dmx_summary}")
            return 0
        store = PresetStore(config["preset_path"], config["pixels"])
        initial = store.state(config)
        sequence_store = SequenceStore(sequence_path, config["pixels"], config["fps"],
                                       config["unit_count"], initial["preset"], counts,
                                       {group: entry["preset"] for group, entry in initial["fixtures"].items()})
        weather_store = (WeatherStore(config["weather"], config["unit_count"], palette=palette_store)
                         if config["weather"] is not None else None)
        default_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
        frontend_dir = args.frontend_dir or (default_frontend if (default_frontend / "index.html").is_file() else None)
        service_label = "Page and API" if frontend_dir else "API"
        playback_monitor = PlaybackMonitor(config["fps"], config["unit_count"])
        unit_color_store = UnitColorStore(config["unit_count"])
        fixture_color_store = FixtureColorStore(counts)
        output_stats = OutputStats(config["fps"])
        # 前端修正過的拍點檔存在串接檔旁的 beat-edits/（Pi：preset/beat-edits/）。 / Corrected beat maps live beside the sequence.
        audio_player = AudioPlayer(config["audio"], tracks_dir=Path(__file__).resolve().parents[1] / "tracks",
                                   edits_dir=sequence_path.parent / "beat-edits")
        if config["audio"] is not None and not audio_player.available:
            print(f"warning: audio disabled: {audio_player.error}", file=sys.stderr, flush=True)
        # 演出存在串接檔旁邊（Pi：preset/shows.json）。 / Shows are saved beside the sequence (preset/shows.json on the Pi).
        show_store = ShowStore(sequence_path.parent / "shows.json", beat_maps=audio_player.beat_maps,
                               fps=config["fps"], pixels=config["pixels"])
        show_player = ShowPlayer(show_store, audio_player, config["fps"])
        if audio_player.available:
            def preload() -> None:
                for warning in audio_player.preload():
                    print(f"warning: {warning}", file=sys.stderr, flush=True)
            Thread(target=preload, name="audio-preload", daemon=True).start()
        with make_server(store, config, args.api_port, host=args.api_host,
                         sequence_store=sequence_store, frontend_dir=frontend_dir,
                         weather_store=weather_store, playback_monitor=playback_monitor,
                         unit_color_store=unit_color_store, fixture_color_store=fixture_color_store,
                         output_stats=output_stats, audio_player=audio_player,
                         show_store=show_store, show_player=show_player) as server:
            if weather_store is not None:
                weather_store.start()
            try:
                if args.api_only:
                    print(f"{service_label} listening on {args.api_host}:{args.api_port}; Art-Net output is disabled. Ctrl+C to stop.", flush=True)
                    server.serve_forever()
                    return 0
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    print(f"Sending {preset['effectId']} -> {len(config['targets'])} Art-Net target(s){dmx_summary} at {config['fps']} FPS. {service_label} listening on {args.api_host}:{args.api_port}. Ctrl+C to stop.", flush=True)
                    sent = stream(config, preset, seconds=args.seconds,
                                  preset_provider=store.snapshot, motor_provider=store.motor_value,
                                  sequence_provider=sequence_store.snapshot,
                                  weather_provider=weather_store.snapshot if weather_store is not None else None,
                                  playback_reporter=playback_monitor.report,
                                  playback_advance=weather_store.advance if weather_store is not None else None,
                                  unit_colors_provider=unit_color_store.current,
                                  fixture_preset_provider=store.fixture_snapshot,
                                  fixture_colors_provider=fixture_color_store.current,
                                  beat_flash_provider=audio_player.flash,
                                  show_provider=show_player.current,
                                  stats=output_stats)
                finally:
                    playback_monitor.stop()
                    server.shutdown()
                    worker.join(timeout=2)
            finally:
                show_player.stop()
                audio_player.stop()
                if weather_store is not None:
                    weather_store.stop()
        print(f"Stopped after {sent} send calls; delivery is not confirmed.")
        return 0
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
