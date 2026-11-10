"""Music playback on the host's sound card, the audio clock the lights follow, and beat maps.

燈光的時間以音訊實際播放到的位置為準：PortAudio callback 每次填資料時記下「這批第一個取樣何時被聽到」
（outputBufferDacTime），輸出迴圈每格以此推算目前的音樂時間，再加上延遲補償 offset。
The lights follow the position the sound card is actually playing: each PortAudio callback records when the
first sample of its buffer will be heard (outputBufferDacTime); the output loop extrapolates the current music
time from that each frame and adds the calibration offset.
"""

from __future__ import annotations

from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from threading import Lock
import time
from typing import Any, Callable, Mapping
import wave

BEAT_MAP_SCHEMA_VERSION = 1
CALIBRATION_SCHEMA_VERSION = 1
MAX_OFFSET_MS = 1000
MAX_BEATS = 20000
TRACK_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
SAMPLE_FORMATS = {2: "int16", 3: "int24", 4: "int32"}

# 內建節拍器：120 BPM、4/4、60 秒循環；拍點精確，用來校準延遲。
# 預設音效卡緩衝（秒）；設定 [audio] buffer_ms。 / Default sound card buffer (s); [audio] buffer_ms.
DEFAULT_BUFFER_SECONDS = 0.1
# Built-in metronome: 120 BPM, 4/4, a 60 s loop with exact beats, for latency calibration.
CLICK_TRACK_ID = "click-120"
CLICK_BPM = 120
CLICK_SECONDS = 60
CLICK_SAMPLE_RATE = 48000
CLICK_FIRST_BEAT = 0.25

# 閃拍：拍點後全亮一小段再線性變暗；小節第一拍全亮，其他拍 40%。
# Beat flash: full for a moment after the beat, then a linear fade; the bar's first beat full, other beats 40%.
FLASH_HOLD_SECONDS = 0.03
FLASH_FADE_SECONDS = 0.12
FLASH_OFFBEAT_LEVEL = 0.4


def _finite(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number in [{low}, {high}]")
    return float(value)


def validate_beat_map(raw: Any) -> dict[str, Any]:
    """Validate a beat map document (``backend/tracks/<id>.json``) and return it normalized."""
    fields = {"schemaVersion", "id", "title", "file", "durationSeconds", "tempo", "beatsPerBar", "firstDownbeat", "beats"}
    if not isinstance(raw, dict) or not fields <= set(raw) or set(raw) - fields - {"source"}:
        raise ValueError("beat map must contain " + ", ".join(sorted(fields)) + " (optional: source)")
    if raw["schemaVersion"] != BEAT_MAP_SCHEMA_VERSION or type(raw["schemaVersion"]) is not int:
        raise ValueError("unsupported beat map schemaVersion")
    track_id = raw["id"]
    if not isinstance(track_id, str) or not 1 <= len(track_id) <= 40 or set(track_id) - TRACK_ID_CHARS \
            or track_id == CLICK_TRACK_ID:
        raise ValueError("beat map id must be 1..40 characters of a-z, 0-9 and -")
    title = raw["title"]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError("beat map title must be 1..80 characters")
    file_name = raw["file"]
    if not isinstance(file_name, str) or not file_name or Path(file_name).name != file_name or not file_name.lower().endswith(".wav"):
        raise ValueError("beat map file must be a .wav file name without directories")
    duration = _finite(raw["durationSeconds"], "durationSeconds", 0.1, 3600)
    tempo = None if raw["tempo"] is None else _finite(raw["tempo"], "tempo", 20, 400)
    beats_per_bar = raw["beatsPerBar"]
    if type(beats_per_bar) is not int or not 1 <= beats_per_bar <= 16:
        raise ValueError("beatsPerBar must be an integer in [1, 16]")
    first_downbeat = raw["firstDownbeat"]
    if type(first_downbeat) is not int or not 0 <= first_downbeat < beats_per_bar:
        raise ValueError("firstDownbeat must be a beat index in [0, beatsPerBar)")
    beats = raw["beats"]
    if not isinstance(beats, list) or len(beats) > MAX_BEATS:
        raise ValueError(f"beats must be a list of at most {MAX_BEATS} times")
    times = [_finite(beat, "beat time", 0, duration) for beat in beats]
    if any(later <= earlier for earlier, later in zip(times, times[1:])):
        raise ValueError("beat times must be strictly increasing")
    source = raw.get("source")
    if source is not None and (not isinstance(source, str) or len(source) > 200):
        raise ValueError("beat map source must be at most 200 characters")
    result = {
        "schemaVersion": BEAT_MAP_SCHEMA_VERSION, "id": track_id, "title": title.strip(), "file": file_name,
        "durationSeconds": duration, "tempo": tempo, "beatsPerBar": beats_per_bar, "firstDownbeat": first_downbeat,
        "beats": times,
    }
    if source is not None:
        result["source"] = source
    return result


def load_beat_maps(directory: str | Path) -> list[dict[str, Any]]:
    """Every ``*.json`` beat map in a directory, sorted by id; ids must be unique."""
    path = Path(directory)
    maps: list[dict[str, Any]] = []
    if not path.is_dir():
        return maps
    for file in sorted(path.glob("*.json")):
        try:
            maps.append(validate_beat_map(json.loads(file.read_text(encoding="utf-8"))))
        except ValueError as error:
            raise ValueError(f"{file.name}: {error}") from None
    ids = [entry["id"] for entry in maps]
    if len(set(ids)) != len(ids):
        raise ValueError("beat map ids must be unique")
    return sorted(maps, key=lambda entry: entry["id"])


def click_track() -> tuple[dict[str, Any], bytes]:
    """(beat map, PCM) of the metronome: 16-bit stereo, a short sine click on every beat."""
    rate = CLICK_SAMPLE_RATE
    period = 60 / CLICK_BPM
    count = round(CLICK_SECONDS / period)
    beats = [CLICK_FIRST_BEAT + index * period for index in range(count)]
    pcm = bytearray(rate * CLICK_SECONDS * 4)
    click_samples = int(0.02 * rate)
    clicks = {}
    for accent in (True, False):
        frequency, amplitude = (1760, 0.5) if accent else (880, 0.35)
        samples = bytearray()
        for n in range(click_samples):
            value = round(32767 * amplitude * math.exp(-n / (0.004 * rate)) * math.sin(math.tau * frequency * n / rate))
            samples += struct.pack("<hh", value, value)
        clicks[accent] = bytes(samples)
    for index, beat in enumerate(beats):
        start = round(beat * rate) * 4
        click = clicks[index % 4 == 0]
        pcm[start:start + len(click)] = click
    beat_map = {
        "schemaVersion": BEAT_MAP_SCHEMA_VERSION, "id": CLICK_TRACK_ID, "title": "節拍器",
        "file": None, "durationSeconds": float(CLICK_SECONDS), "tempo": float(CLICK_BPM),
        "beatsPerBar": 4, "firstDownbeat": 0, "beats": beats, "source": "built-in",
    }
    return beat_map, bytes(pcm)


def beat_at(beat_map: Mapping[str, Any], seconds: float, loop: bool) -> tuple[int, float, bool] | None:
    """(beat index, seconds since that beat, whether it starts a bar) for the latest beat at ``seconds``."""
    beats = beat_map["beats"]
    if not beats:
        return None
    duration = beat_map["durationSeconds"]
    if loop:
        seconds %= duration
    index = bisect_right(beats, seconds) - 1
    if index < 0:
        if not loop:
            return None
        # 循環時，第一拍之前接上一輪的最後一拍。 / When looping, before the first beat is the previous round's last.
        index = len(beats) - 1
        since = seconds + duration - beats[index]
    else:
        since = seconds - beats[index]
    downbeat = (index - beat_map["firstDownbeat"]) % beat_map["beatsPerBar"] == 0
    return index, since, downbeat


def flash_level(beat_map: Mapping[str, Any], seconds: float, loop: bool) -> float:
    beat = beat_at(beat_map, seconds, loop)
    if beat is None:
        return 0.0
    _, since, downbeat = beat
    if since < FLASH_HOLD_SECONDS:
        level = 1.0
    else:
        level = max(0.0, 1 - (since - FLASH_HOLD_SECONDS) / FLASH_FADE_SECONDS)
    return level if downbeat else level * FLASH_OFFBEAT_LEVEL


def read_wav(path: str | Path) -> tuple[int, int, int, bytes]:
    """(sample rate, channels, bytes per sample, PCM) of an uncompressed PCM WAV file."""
    with wave.open(str(path), "rb") as stream:
        if stream.getcomptype() != "NONE":
            raise ValueError(f"{Path(path).name} must be uncompressed PCM")
        width = stream.getsampwidth()
        if width not in SAMPLE_FORMATS:
            raise ValueError(f"{Path(path).name} must be 16-, 24- or 32-bit PCM")
        return stream.getframerate(), stream.getnchannels(), width, stream.readframes(stream.getnframes())


def _load_offset(path: Path) -> int:
    if not path.exists():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schemaVersion") != CALIBRATION_SCHEMA_VERSION or set(raw) != {"schemaVersion", "offsetMs"}:
        raise ValueError(f"{path.name} must be {{schemaVersion: 1, offsetMs}}")
    return _offset(raw["offsetMs"])


def _offset(value: Any) -> int:
    if type(value) is not int or not -MAX_OFFSET_MS <= value <= MAX_OFFSET_MS:
        raise ValueError(f"offsetMs must be an integer in [{-MAX_OFFSET_MS}, {MAX_OFFSET_MS}]")
    return value


class _Playback:
    """One open stream and what it plays. ``anchor`` = (absolute frame index, monotonic time it is heard)."""

    def __init__(self, beat_map: Mapping[str, Any], pcm: bytes, rate: int, channels: int, width: int,
                 loop: bool, flash: bool, start_frame: int) -> None:
        self.beat_map = beat_map
        self.pcm = pcm
        self.rate = rate
        self.frame_bytes = channels * width
        self.frames = len(pcm) // self.frame_bytes
        self.loop = loop
        self.flash = flash
        self.next_frame = start_frame
        self.anchor: tuple[int, float] | None = None
        self.finished = False
        self.stream: Any = None


class AudioPlayer:
    """Thread-safe player: the API starts and stops tracks, the output loop reads the music time every frame."""

    def __init__(self, audio_config: Mapping[str, Any] | None, *, tracks_dir: str | Path,
                 edits_dir: str | Path | None = None,
                 sounddevice: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        """``tracks_dir``: the beat maps shipped with the release (read-only on the Pi). ``edits_dir``: beat maps
        corrected in the frontend (M14), which replace the shipped ones with the same id.
        ``tracks_dir`` 是 release 內建的拍點檔；``edits_dir`` 是前端修正後的拍點檔，同 id 時優先使用。"""
        self.config = audio_config
        self.clock = clock
        self.lock = Lock()
        self.error: str | None = None
        self._sd = sounddevice
        self._playback: _Playback | None = None
        # 音效卡要資料時緩衝已空（斷音）的次數，啟動後累計。 / Output underflows (drop-outs) since start.
        self.underflows = 0
        # 已載入的音檔（全部常駐記憶體）。 / Loaded tracks, all kept in memory.
        self._cache: dict[str, tuple[int, int, int, bytes]] = {}
        self._cache_lock = Lock()
        click_map, click_pcm = click_track()
        self._click_pcm = click_pcm
        self.beat_maps: dict[str, Mapping[str, Any]] = {CLICK_TRACK_ID: click_map}
        for entry in load_beat_maps(tracks_dir):
            self.beat_maps[entry["id"]] = entry
        # 內建版本，還原修正時用。 / The shipped versions, for reverting a correction.
        self._shipped: dict[str, Mapping[str, Any]] = dict(self.beat_maps)
        self.edits_dir = Path(edits_dir) if edits_dir is not None else None
        if self.edits_dir is not None and self.edits_dir.resolve() == Path(tracks_dir).resolve():
            raise ValueError("the corrected beat map directory must differ from the shipped tracks directory")
        self.edited: set[str] = set()
        for entry in load_beat_maps(self.edits_dir) if self.edits_dir is not None else []:
            shipped = self._shipped.get(entry["id"])
            if shipped is None or entry["id"] == CLICK_TRACK_ID:
                continue  # 曲目已不在 release 內：忽略。 / The track left the release: ignored.
            self.beat_maps[entry["id"]] = entry
            self.edited.add(entry["id"])
        self.calibration_path = Path(audio_config["calibration_path"]) if audio_config else None
        self.saved_offset = _load_offset(self.calibration_path) if self.calibration_path else 0
        self.offset = self.saved_offset
        if audio_config is None:
            self.error = "沒有 [audio] 設定"
        elif self._sd is None:
            try:
                import sounddevice as sd  # Needs the PortAudio library (Debian: libportaudio2).
            except (ImportError, OSError) as failure:
                self.error = f"無法載入 PortAudio：{failure}"
            else:
                self._sd = sd

    @property
    def available(self) -> bool:
        return self.error is None

    def _wav_path(self, beat_map: Mapping[str, Any]) -> Path | None:
        if beat_map["file"] is None or self.config is None:
            return None
        return Path(self.config["directory"]) / beat_map["file"]

    def _track_info(self, beat_map: Mapping[str, Any]) -> dict[str, Any]:
        path = self._wav_path(beat_map)
        return {
            "id": beat_map["id"], "title": beat_map["title"], "file": beat_map["file"],
            "durationSeconds": beat_map["durationSeconds"], "tempo": beat_map["tempo"],
            "beatCount": len(beat_map["beats"]), "beatsPerBar": beat_map["beatsPerBar"],
            "available": beat_map["file"] is None or (path is not None and path.is_file()),
            "loaded": beat_map["file"] is None or beat_map["id"] in self._cache,
            "edited": beat_map["id"] in self.edited,
        }

    def beat_map(self, track_id: str) -> Mapping[str, Any] | None:
        return self.beat_maps.get(track_id)

    def save_beat_map(self, track_id: str, raw: Any) -> Mapping[str, Any]:
        """Store a corrected beat map (same track: id, file and duration unchanged) in ``edits_dir``."""
        shipped = self._shipped.get(track_id)
        if shipped is None or track_id == CLICK_TRACK_ID:
            raise ValueError("unknown or built-in track")
        if self.edits_dir is None:
            raise ValueError("no directory for corrected beat maps")
        beat_map = validate_beat_map(raw)
        for key in ("id", "file", "durationSeconds"):
            if beat_map[key] != shipped[key]:
                raise ValueError(f"beat map {key} must stay {shipped[key]!r}")
        self.edits_dir.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(beat_map, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        target = self.edits_dir / f"{track_id}.json"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".beats-", suffix=".tmp", dir=self.edits_dir, delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        with self.lock:
            self.beat_maps[track_id] = beat_map
            self.edited.add(track_id)
        return beat_map

    def revert_beat_map(self, track_id: str) -> Mapping[str, Any]:
        """Drop the correction and use the shipped beat map again."""
        shipped = self._shipped.get(track_id)
        if shipped is None or track_id == CLICK_TRACK_ID:
            raise ValueError("unknown or built-in track")
        if self.edits_dir is not None:
            (self.edits_dir / f"{track_id}.json").unlink(missing_ok=True)
        with self.lock:
            self.beat_maps[track_id] = shipped
            self.edited.discard(track_id)
        return shipped

    def wav_file(self, track_id: str) -> Path | None:
        """The track's WAV on the host, for the browser preview; None for the metronome or a missing file."""
        beat_map = self.beat_maps.get(track_id)
        path = self._wav_path(beat_map) if beat_map is not None else None
        return path if path is not None and path.is_file() else None

    def _device_name(self) -> str | None:
        if self._sd is None or self.config is None:
            return None
        try:
            return str(self._sd.query_devices(self.config["device"] or None, "output")["name"])
        except Exception:  # noqa: BLE001 - PortAudio raises several types for missing devices.
            return None

    def _position_unlocked(self, playback: _Playback) -> float | None:
        """Seconds of the track heard now (unwrapped when looping); None before the first callback."""
        if playback.anchor is None:
            return None
        frame, heard_at = playback.anchor
        return frame / playback.rate + (self.clock() - heard_at)

    def state(self) -> dict[str, Any]:
        with self.lock:
            playback = self._playback
            playing = None
            if playback is not None and not playback.finished:
                position = self._position_unlocked(playback)
                beat = None
                if position is not None:
                    beat = beat_at(playback.beat_map, position, playback.loop)
                    duration = playback.beat_map["durationSeconds"]
                    position = position % duration if playback.loop else min(max(position, 0.0), duration)
                playing = {
                    "trackId": playback.beat_map["id"], "positionSeconds": position,
                    "durationSeconds": playback.beat_map["durationSeconds"],
                    "loop": playback.loop, "flash": playback.flash,
                    "beatIndex": None if beat is None else beat[0],
                }
            offset, saved = self.offset, self.saved_offset
        return {
            "available": self.available, "error": self.error,
            "device": {"requested": self.config["device"] if self.config else None, "name": self._device_name()},
            "calibration": {"offsetMs": offset, "savedOffsetMs": saved, "maxOffsetMs": MAX_OFFSET_MS},
            "bufferMs": round(1000 * self.config.get("buffer_seconds", DEFAULT_BUFFER_SECONDS)) if self.config else None,
            "underflows": self.underflows,
            "tracks": [self._track_info(entry) for entry in self.beat_maps.values()],
            "playing": playing,
        }

    def _load(self, beat_map: Mapping[str, Any]) -> tuple[int, int, int, bytes]:
        if beat_map["id"] == CLICK_TRACK_ID:
            return CLICK_SAMPLE_RATE, 2, 2, self._click_pcm
        # 同一時間只讀一個檔，預載與播放請求不會重複讀同一首。 / One file at a time, so preload and play never read twice.
        with self._cache_lock:
            cached = self._cache.get(beat_map["id"])
            if cached is not None:
                return cached
            path = self._wav_path(beat_map)
            if path is None or not path.is_file():
                raise ValueError(f"音檔不存在：{beat_map['file']}")
            try:
                loaded = read_wav(path)
            except (wave.Error, EOFError, OSError) as failure:
                raise ValueError(f"無法讀取 {beat_map['file']}：{failure}") from None
            self._cache[beat_map["id"]] = loaded
            return loaded

    def preload(self) -> list[str]:
        """Read every track's WAV that is on the host into memory; returns warnings for the ones that fail.

        Reading a large WAV stalls the output loop for a moment (about 160 ms for 22 MB on the Pi 5), so the
        service does it once at startup instead of on the first play.
        讀大型 WAV 會讓輸出迴圈停頓一下，所以服務啟動時先全部讀進來，而不是第一次播放時才讀。
        """
        warnings = []
        for beat_map in list(self.beat_maps.values()):
            path = self._wav_path(beat_map)
            if path is None or not path.is_file():
                continue
            try:
                self._load(beat_map)
            except ValueError as failure:
                warnings.append(str(failure))
        return warnings

    def play(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or "trackId" not in raw or set(raw) - {"trackId", "startSeconds", "loop", "flash"}:
            raise ValueError("play request must be {trackId, startSeconds?, loop?, flash?}")
        if not self.available:
            raise ValueError(self.error or "audio is unavailable")
        beat_map = self.beat_maps.get(raw["trackId"]) if isinstance(raw["trackId"], str) else None
        if beat_map is None:
            raise ValueError("unknown trackId")
        start = _finite(raw.get("startSeconds", 0), "startSeconds", 0, beat_map["durationSeconds"])
        loop, flash = raw.get("loop", False), raw.get("flash", False)
        if type(loop) is not bool or type(flash) is not bool:
            raise ValueError("loop and flash must be booleans")
        rate, channels, width, pcm = self._load(beat_map)
        playback = _Playback(beat_map, pcm, rate, channels, width, loop, flash, round(start * rate))
        self.stop()
        sd = self._sd

        def callback(outdata: Any, frames: int, time_info: Any, status: Any) -> None:
            now = self.clock()
            if status is not None and getattr(status, "output_underflow", False):
                self.underflows += 1  # 只有這個 callback 寫入。 / Only this callback writes it.
            dac, current = time_info.outputBufferDacTime, time_info.currentTime
            # 有些 host API 不提供時間（0）：改用串流延遲估計。 / Some host APIs report 0: use the stream latency instead.
            delay = dac - current if dac and current else playback.stream.latency
            start_frame = playback.next_frame
            size = frames * playback.frame_bytes
            if playback.loop:
                chunk = bytearray()
                frame = start_frame % playback.frames
                while len(chunk) < size:
                    take = min(size - len(chunk), (playback.frames - frame) * playback.frame_bytes)
                    offset = frame * playback.frame_bytes
                    chunk += playback.pcm[offset:offset + take]
                    frame = 0
                data = bytes(chunk)
            else:
                offset = start_frame * playback.frame_bytes
                data = playback.pcm[offset:offset + size]
                data += bytes(size - len(data))
            outdata[:] = data
            with self.lock:
                playback.anchor = (start_frame, now + max(0.0, delay))
                playback.next_frame = start_frame + frames
            if not playback.loop and start_frame + frames >= playback.frames:
                raise sd.CallbackStop

        def finished() -> None:
            with self.lock:
                playback.finished = True

        try:
            stream = sd.RawOutputStream(
                samplerate=rate, channels=channels, dtype=SAMPLE_FORMATS[width],
                device=self.config["device"] or None, latency=self.config.get("buffer_seconds", DEFAULT_BUFFER_SECONDS),
                callback=callback, finished_callback=finished,
            )
        except Exception as failure:  # noqa: BLE001 - PortAudioError, ValueError for unknown devices, ...
            raise ValueError(f"無法開啟音訊裝置：{failure}") from None
        playback.stream = stream
        with self.lock:
            self._playback = playback
        try:
            stream.start()
        except Exception as failure:  # noqa: BLE001
            self.stop()
            raise ValueError(f"無法開始播放：{failure}") from None
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            playback, self._playback = self._playback, None
        # 在鎖外關閉：abort 會等 callback 結束，而 callback 需要鎖。 / Close outside the lock: abort waits for the callback.
        if playback is not None and playback.stream is not None:
            try:
                playback.stream.abort()
            finally:
                playback.stream.close()
        return self.state()

    def set_calibration(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or "offsetMs" not in raw or set(raw) - {"offsetMs", "save"}:
            raise ValueError("calibration must be {offsetMs, save?}")
        offset = _offset(raw["offsetMs"])
        save = raw.get("save", False)
        if type(save) is not bool:
            raise ValueError("save must be a boolean")
        if save:
            if self.calibration_path is None:
                raise ValueError("no calibration file configured")
            encoded = (json.dumps({"schemaVersion": CALIBRATION_SCHEMA_VERSION, "offsetMs": offset}) + "\n").encode()
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".audio-", suffix=".tmp",
                                                 dir=self.calibration_path.parent, delete=False) as stream:
                    temporary = stream.name
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.calibration_path)
                temporary = None
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        with self.lock:
            self.offset = offset
            if save:
                self.saved_offset = offset
        return self.state()

    def music_time(self) -> tuple[Mapping[str, Any], float, bool, bool] | None:
        """(beat map, calibrated seconds, loop, flash) for the output loop; None when nothing is heard yet."""
        with self.lock:
            playback = self._playback
            if playback is None or playback.finished:
                return None
            position = self._position_unlocked(playback)
            if position is None:
                return None
            return playback.beat_map, position + self.offset / 1000, playback.loop, playback.flash

    def flash(self) -> float | None:
        """Beat-flash level for the output loop while a flash test plays, else None (normal output)."""
        current = self.music_time()
        if current is None or not current[3]:
            return None
        beat_map, seconds, loop, _ = current
        return flash_level(beat_map, seconds, loop)
