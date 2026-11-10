import json
from pathlib import Path
import struct
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave

from moon_observe_backend.audio import (
    CLICK_TRACK_ID, AudioPlayer, beat_at, click_track, flash_level, load_beat_maps, validate_beat_map,
)
from moon_observe_backend.config import load_config
from moon_observe_backend.control import PresetStore, make_server
from moon_observe_backend.effects import default_preset, validate_preset
from moon_observe_backend.output import OutputStats, stream

BACKEND = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class CallbackStop(Exception):
    pass


class FakeStream:
    """Records the stream settings; the test calls ``pull`` to play buffers like PortAudio would."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.latency = 0.05
        self.started = self.closed = self.aborted = False

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def pull(self, frames, dac_delay=0.02, underflow=False):
        width = {"int16": 2, "int24": 3, "int32": 4}[self.kwargs["dtype"]] * self.kwargs["channels"]
        buffer = bytearray(frames * width)
        view = memoryview(buffer)
        try:
            status = SimpleNamespace(output_underflow=True) if underflow else None
            self.kwargs["callback"](view, frames, SimpleNamespace(outputBufferDacTime=10 + dac_delay, currentTime=10.0), status)
        except CallbackStop:
            self.kwargs["finished_callback"]()
            return bytes(buffer), True
        return bytes(buffer), False


class FakeSounddevice:
    CallbackStop = CallbackStop

    def __init__(self):
        self.streams = []

    def RawOutputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        self.streams.append(stream)
        return stream

    def query_devices(self, device, kind):
        return {"name": "Fake USB Audio" if device else "Default"}


def write_wav(path, frames, rate=1000, width=3):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(width)
        target.setframerate(rate)
        target.writeframes(b"".join(index.to_bytes(width, "little") * 2 for index in range(frames)))


def beat_map(**overrides):
    document = {
        "schemaVersion": 1, "id": "song", "title": "Song", "file": "song.wav", "durationSeconds": 4.0,
        "tempo": 60.0, "beatsPerBar": 4, "firstDownbeat": 1, "beats": [0.5, 1.5, 2.5, 3.5],
    }
    document.update(overrides)
    return document


class BeatMapTests(unittest.TestCase):
    def test_validates_beat_maps(self):
        self.assertEqual([0.5, 1.5, 2.5, 3.5], validate_beat_map(beat_map())["beats"])
        for bad in (
            {"id": "Bad Id"}, {"id": CLICK_TRACK_ID}, {"file": "../song.wav"}, {"file": "song.mp3"},
            {"beats": [1.0, 1.0]}, {"beats": [5.0]}, {"firstDownbeat": 4}, {"tempo": True}, {"extra": 1},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_beat_map(beat_map(**bad))

    def test_repository_beat_maps_load(self):
        maps = {entry["id"]: entry for entry in load_beat_maps(BACKEND / "tracks")}
        self.assertEqual({"ambient-3min", "christmas-5"}, set(maps))
        self.assertEqual("光林_聖誕5.wav", maps["christmas-5"]["file"])
        self.assertGreater(len(maps["christmas-5"]["beats"]), 150)

    def test_beat_lookup_and_bars(self):
        document = validate_beat_map(beat_map())
        self.assertIsNone(beat_at(document, 0.4, loop=False))
        self.assertEqual((1, 0.25, True), beat_at(document, 1.75, loop=False))  # firstDownbeat = 1
        self.assertEqual((2, 0.0, False), beat_at(document, 2.5, loop=False))
        # 循環：第一拍之前接上一輪最後一拍。 / Looping: before the first beat comes the last one of the previous round.
        index, since, _ = beat_at(document, 4.25, loop=True)
        self.assertEqual(3, index)
        self.assertAlmostEqual(0.75, since)

    def test_flash_levels(self):
        document = validate_beat_map(beat_map())
        self.assertEqual(1.0, flash_level(document, 1.51, loop=False))      # downbeat, within the hold
        self.assertAlmostEqual(0.4, flash_level(document, 2.51, loop=False))  # other beat: 40 %
        self.assertEqual(0.0, flash_level(document, 1.8, loop=False))       # faded out
        self.assertEqual(0.0, flash_level(document, 0.2, loop=False))       # before the first beat

    def test_click_track_has_exact_beats_and_clicks(self):
        document, pcm = click_track()
        self.assertEqual(120, len(document["beats"]))
        self.assertEqual(0.25, document["beats"][0])
        self.assertAlmostEqual(0.5, document["beats"][1] - document["beats"][0])
        self.assertEqual(48000 * 60 * 4, len(pcm))
        sample = lambda seconds: struct.unpack_from("<h", pcm, round(seconds * 48000) * 4)[0]
        self.assertEqual(0, sample(0.1))
        self.assertNotEqual(0, sample(0.2501))
        self.assertGreater(max(abs(struct.unpack_from("<h", pcm, (12000 + n) * 4)[0]) for n in range(40)),
                           max(abs(struct.unpack_from("<h", pcm, (36000 + n) * 4)[0]) for n in range(40)))


class AudioPlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "audio").mkdir()
        (root / "tracks").mkdir()
        write_wav(root / "audio" / "song.wav", 4000)
        (root / "tracks" / "song.json").write_text(json.dumps(beat_map()), encoding="utf-8")
        self.config = {"device": "USB", "directory": str(root / "audio"),
                       "calibration_path": str(root / "audio-calibration.json")}
        self.root = root
        self.clock = FakeClock()
        self.sd = FakeSounddevice()
        self.player = AudioPlayer(self.config, tracks_dir=root / "tracks", sounddevice=self.sd, clock=self.clock)

    def test_state_lists_tracks_and_missing_files(self):
        (self.root / "tracks" / "gone.json").write_text(json.dumps(beat_map(id="gone", file="gone.wav")), encoding="utf-8")
        player = AudioPlayer(self.config, tracks_dir=self.root / "tracks", sounddevice=self.sd, clock=self.clock)
        state = player.state()
        self.assertTrue(state["available"])
        self.assertEqual("Fake USB Audio", state["device"]["name"])
        tracks = {track["id"]: track for track in state["tracks"]}
        self.assertEqual([CLICK_TRACK_ID, "gone", "song"], [track["id"] for track in state["tracks"]])
        self.assertTrue(tracks[CLICK_TRACK_ID]["available"])
        self.assertTrue(tracks["song"]["available"])
        self.assertFalse(tracks["gone"]["available"])
        with self.assertRaisesRegex(ValueError, "gone.wav"):
            player.play({"trackId": "gone"})

    def test_music_time_follows_the_dac_clock(self):
        self.player.play({"trackId": "song", "startSeconds": 1.0, "flash": True})
        stream = self.sd.streams[0]
        self.assertEqual({"samplerate": 1000, "channels": 2, "dtype": "int24", "device": "USB", "latency": 0.1},
                         {key: stream.kwargs[key] for key in ("samplerate", "channels", "dtype", "device", "latency")})
        self.assertTrue(stream.started)
        self.assertIsNone(self.player.music_time())  # nothing heard before the first callback
        self.assertIsNone(self.player.flash())
        data, _ = stream.pull(100)
        # 從第 1000 個取樣開始（startSeconds 1.0）。 / Starts at sample 1000 (startSeconds 1.0).
        self.assertEqual((1000).to_bytes(3, "little") * 2, data[:6])
        # 這批第一個取樣在 20 ms 後被聽到。 / The buffer's first sample is heard 20 ms later.
        _, seconds, loop, flash = self.player.music_time()
        self.assertAlmostEqual(0.98, seconds)
        self.assertEqual((False, True), (loop, flash))
        self.clock.now += 0.53
        self.assertAlmostEqual(1.51, self.player.music_time()[1])
        self.assertEqual(1.0, self.player.flash())  # beat 1 (a downbeat) at 1.5 s
        self.player.set_calibration({"offsetMs": 100})
        self.assertAlmostEqual(1.61, self.player.music_time()[1])
        state = self.player.state()["playing"]
        self.assertEqual("song", state["trackId"])
        self.assertAlmostEqual(1.51, state["positionSeconds"])
        self.assertEqual(1, state["beatIndex"])

    def test_counts_underflows_and_uses_the_configured_buffer(self):
        player = AudioPlayer({**self.config, "buffer_seconds": 0.25}, tracks_dir=self.root / "tracks",
                             sounddevice=self.sd, clock=self.clock)
        player.play({"trackId": "song"})
        stream = self.sd.streams[-1]
        self.assertEqual(0.25, stream.kwargs["latency"])
        stream.pull(100)
        stream.pull(100, underflow=True)
        stream.pull(100, underflow=True)
        state = player.state()
        self.assertEqual((250, 2), (state["bufferMs"], state["underflows"]))
        stats = OutputStats(40, window=1.0, audio_underflows=lambda: player.underflows)
        stream.pull(100, underflow=True)
        stats.record(0.0, 0, 0.001)
        line = stats.record(1.0, 0, 0.001)
        self.assertTrue(line.endswith(", audio underflows 1"), line)
        self.assertEqual(1, stats.snapshot()["audioUnderflows"])

    def test_buffer_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("full-on")), encoding="utf-8")
            base = '[artnet]\nip = "192.0.2.20"\nuniverse = 0\n[render]\npreset = "preset.json"\n[audio]\n'
            (root / "config.toml").write_text(base, encoding="utf-8")
            self.assertEqual(0.1, load_config(root / "config.toml")[0]["audio"]["buffer_seconds"])
            (root / "config.toml").write_text(base + "buffer_ms = 40\n", encoding="utf-8")
            self.assertEqual(0.04, load_config(root / "config.toml")[0]["audio"]["buffer_seconds"])
            for bad in ("5", "0.1", "2000"):
                (root / "config.toml").write_text(base + f"buffer_ms = {bad}\n", encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "buffer_ms"):
                    load_config(root / "config.toml")

    def test_track_end_stops_playback(self):
        self.player.play({"trackId": "song", "startSeconds": 3.9})
        stream = self.sd.streams[0]
        data, stopped = stream.pull(200)
        self.assertTrue(stopped)
        self.assertEqual(bytes(600), data[600:])  # padded with silence after the last sample
        self.assertIsNone(self.player.state()["playing"])
        self.assertIsNone(self.player.music_time())

    def test_loop_wraps_to_the_start(self):
        self.player.play({"trackId": "song", "startSeconds": 3.95, "loop": True})
        data, stopped = self.sd.streams[0].pull(100)
        self.assertFalse(stopped)
        self.assertEqual((3950).to_bytes(3, "little") * 2, data[:6])
        self.assertEqual((0).to_bytes(3, "little") * 2, data[50 * 6:51 * 6])
        self.clock.now += 0.1
        self.assertAlmostEqual(0.03, self.player.state()["playing"]["positionSeconds"])

    def test_stop_and_replace_close_the_stream(self):
        self.player.play({"trackId": "song"})
        self.player.play({"trackId": CLICK_TRACK_ID, "loop": True})
        self.assertTrue(self.sd.streams[0].aborted and self.sd.streams[0].closed)
        self.assertEqual({"samplerate": 48000, "dtype": "int16"},
                         {key: self.sd.streams[1].kwargs[key] for key in ("samplerate", "dtype")})
        self.assertIsNone(self.player.stop()["playing"])
        self.assertTrue(self.sd.streams[1].closed)

    def test_rejects_bad_requests(self):
        for raw in ({}, {"trackId": "nope"}, {"trackId": "song", "startSeconds": 9},
                    {"trackId": "song", "loop": 1}, {"trackId": "song", "volume": 1}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.player.play(raw)
        for raw in ({"offsetMs": 1.5}, {"offsetMs": 1001}, {"offsetMs": 0, "save": "yes"}, {}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.player.set_calibration(raw)

    def test_calibration_saves_and_reloads(self):
        state = self.player.set_calibration({"offsetMs": -40})
        self.assertEqual({"offsetMs": -40, "savedOffsetMs": 0, "maxOffsetMs": 1000}, state["calibration"])
        self.assertFalse(Path(self.config["calibration_path"]).exists())
        state = self.player.set_calibration({"offsetMs": 75, "save": True})
        self.assertEqual(75, state["calibration"]["savedOffsetMs"])
        self.assertEqual({"schemaVersion": 1, "offsetMs": 75},
                         json.loads(Path(self.config["calibration_path"]).read_text(encoding="utf-8")))
        reloaded = AudioPlayer(self.config, tracks_dir=self.root / "tracks", sounddevice=self.sd, clock=self.clock)
        self.assertEqual(75, reloaded.state()["calibration"]["offsetMs"])

    def test_without_audio_config_or_portaudio(self):
        player = AudioPlayer(None, tracks_dir=self.root / "tracks")
        self.assertFalse(player.state()["available"])
        with self.assertRaises(ValueError):
            player.play({"trackId": CLICK_TRACK_ID})
        self.assertIsNone(player.flash())


class AudioIntegrationTests(unittest.TestCase):
    def test_example_config_has_audio(self):
        config, _ = load_config(BACKEND / "config.example.toml")
        self.assertEqual("", config["audio"]["device"])
        self.assertEqual(str((BACKEND.parent / "audio").resolve()), config["audio"]["directory"])

    def test_api_plays_stops_and_calibrates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            sd = FakeSounddevice()
            player = AudioPlayer({"device": "", "directory": str(root), "calibration_path": str(root / "cal.json")},
                                 tracks_dir=root / "none", sounddevice=sd)
            config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 156,
                      "unit_id": 1, "unit_count": 9}
            with make_server(PresetStore(root / "preset.json", 156), config, 0, audio_player=player) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}"

                    def put(path, body):
                        request = Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="PUT")
                        with urlopen(request) as response:
                            return json.load(response)

                    with urlopen(base + "/api/audio") as response:
                        self.assertEqual([CLICK_TRACK_ID], [track["id"] for track in json.load(response)["tracks"]])
                    state = put("/api/audio/play", {"trackId": CLICK_TRACK_ID, "flash": True, "loop": True})
                    self.assertEqual(CLICK_TRACK_ID, state["playing"]["trackId"])
                    self.assertEqual(20, put("/api/audio/calibration", {"offsetMs": 20, "save": True})["calibration"]["savedOffsetMs"])
                    with self.assertRaises(HTTPError) as caught:
                        put("/api/audio/play", {"trackId": "missing"})
                    self.assertEqual(422, caught.exception.code)
                    with urlopen(Request(base + "/api/audio/play", method="DELETE")) as response:
                        self.assertIsNone(json.load(response)["playing"])
                finally:
                    server.shutdown()

    def test_output_flashes_every_light_while_the_flash_test_plays(self):
        packets = []

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            def set_simplified(self, _value):
                pass

            def set_net(self, _value):
                pass

            def set_subnet(self, _value):
                pass

            def send(self, payload):
                packets.append(bytes(payload))

            def close(self):
                pass

        levels = iter([None, 1.0, 0.4])
        config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 4,
                  "unit_id": 1, "unit_count": 1}
        times = iter([0.0, 0.0, 0.0, 0.025, 0.025, 0.025, 0.05, 0.05, 0.05, 1.0])
        stream(config, validate_preset(default_preset("full-on"), 4), seconds=0.07, client_factory=Client,
               beat_flash_provider=lambda: next(levels), clock=lambda: next(times), sleep=lambda _s: None)
        self.assertEqual(bytes([255] * 12), packets[1][:12])
        self.assertEqual(bytes([102] * 12), packets[2][:12])


if __name__ == "__main__":
    unittest.main()
