import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from moon_observe_backend.audio import CLICK_TRACK_ID, AudioPlayer, click_track
from moon_observe_backend.control import PresetStore, make_server
from moon_observe_backend.effects import default_preset, validate_preset
from moon_observe_backend.fixture_effects import default_fixture_preset
from moon_observe_backend.output import stream
from moon_observe_backend.playback import PlaybackMonitor
from moon_observe_backend.sequence import render_sequence_fixtures, render_sequence_frame, sequence_position
from moon_observe_backend.show import (
    ShowPlayer, ShowStore, compile_show, show_finished, show_frame, transition_seconds, validate_show,
)

from test_audio import FakeClock, FakeSounddevice, write_wav

CLICK = click_track()[0]  # 120 BPM, 4/4, beats at 0.25 + 0.5 k s, 60 s
UNIT = {"unitId": 1, "logicalPixelCount": 4, "frameOffset": 0}


def solid(red, green, blue):
    preset = default_preset("full-on")
    preset["color"] = {"mode": "solid", "color": {"r": red, "g": green, "b": blue}}
    return preset


def show(**overrides):
    document = {
        "schemaVersion": 1, "id": "demo", "title": "Demo", "trackId": CLICK_TRACK_ID, "ending": {"kind": "loop"},
        "sections": [
            {"id": 1, "start": {"seconds": 0}, "spinner": solid(1, 0, 0), "transition": {"kind": "crossfade", "beats": 1}},
            {"id": 2, "start": {"bar": 2}, "spinner": solid(0, 1, 0), "ball": default_fixture_preset("fixture-solid"),
             "transition": {"kind": "crossfade", "beats": 2}},
            {"id": 3, "start": {"bar": 3, "beat": 3}, "spinner": solid(0, 0, 1), "transition": {"kind": "cut", "seconds": 0}},
        ],
    }
    document.update(overrides)
    return document


class FakeAudio:
    """Stands in for AudioPlayer: the test sets the music time the output loop reads."""

    available = True

    def __init__(self):
        self.played = []
        self.stopped = 0
        self.time = None

    def play(self, raw):
        self.played.append(raw)

    def stop(self):
        self.stopped += 1

    def music_time(self):
        return None if self.time is None else (CLICK, self.time, True, False)


class CompileTests(unittest.TestCase):
    def test_sections_start_on_bars_and_beats(self):
        compiled = compile_show(show(), CLICK, 40, 4)
        # 第 2 小節 = 第 4 拍 = 2.25 s；第 3 小節第 3 拍 = 第 10 拍 = 5.25 s。 / Bar 2 = beat 4; bar 3 beat 3 = beat 10.
        self.assertEqual([0, 90, 210], [step["start"] for step in compiled])
        self.assertEqual([90, 120, 2400 - 210], [step["duration"] for step in compiled])
        self.assertEqual(2400, compiled[0]["show"]["totalFrames"])
        # 進入第 2 段的轉場 2 拍 = 1 s；轉場種類記在前一段。 / The 2-beat transition into section 2 is 1 s, stored on section 1.
        self.assertEqual(40, compiled[1]["transition_frames"])
        self.assertEqual(["crossfade", "cut", "crossfade"], [step["transition_kind"] for step in compiled])
        # 循環回到第 1 段：1 拍，從 0 s 算起到 0.5 s。 / Looping back into section 1: one beat from 0 s ends at 0.5 s.
        self.assertEqual(20, compiled[0]["loop_transition_frames"])
        self.assertIsNotNone(compiled[1]["ball"])
        self.assertIsNone(compiled[0]["ball"])

    def test_transition_beats_follow_the_beat_map(self):
        uneven = {**CLICK, "beats": [0.0, 1.0, 1.5, 2.0]}
        self.assertAlmostEqual(1.5, transition_seconds({"kind": "crossfade", "beats": 2}, uneven, 0.0))
        self.assertAlmostEqual(0.25, transition_seconds({"kind": "crossfade", "beats": 0.5}, uneven, 1.5))
        # 最後一拍之後以最後的間隔延伸。 / Past the last beat the last interval continues.
        self.assertAlmostEqual(1.0, transition_seconds({"kind": "crossfade", "beats": 2}, uneven, 2.0))
        self.assertEqual(0.4, transition_seconds({"kind": "fade-black", "seconds": 0.4}, uneven, 0.0))

    def test_rejects_bad_shows(self):
        sections = show()["sections"]
        for bad in (
            {"id": "Bad"}, {"title": " "}, {"ending": {"kind": "hold"}}, {"sections": []},
            {"sections": [{**sections[0], "start": {"bar": 1}}]},
            {"sections": [sections[0], {**sections[1], "start": {"bar": 1, "beat": 5}}]},
            {"sections": [sections[0], {**sections[1], "start": {"bar": 99}}]},
            {"sections": [sections[0], {**sections[1], "start": {"seconds": 61}}]},
            {"sections": [sections[0], sections[2], sections[1]]},
            {"sections": [sections[0], {**sections[1], "id": 1}]},
            {"sections": [sections[0], {**sections[1], "transition": {"kind": "wipe", "seconds": 1}}]},
            {"sections": [sections[0], {**sections[1], "transition": {"kind": "cut", "seconds": 1, "beats": 1}}]},
            {"trackId": "other"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compile_show(show(**bad), CLICK, 40, 4)

    def test_canonical_document(self):
        document = validate_show(show(title="  Demo  "), 4)
        self.assertEqual("Demo", document["title"])
        self.assertEqual({"bar": 3, "beat": 3}, document["sections"][2]["start"])
        self.assertEqual(document, validate_show(json.loads(json.dumps(document)), 4))

    def test_renders_like_a_sequence(self):
        compiled = compile_show(show(), CLICK, 40, 4)
        render = lambda frame: render_sequence_frame(compiled, UNIT, frame, unit_count=1, fps=40, rpm=360)
        self.assertEqual((1.0, 0.0, 0.0), render(89)[0])
        # 交叉淡入的中間（40 格，權重 (e + 1) / 41）。 / Middle of the 40-frame crossfade, weight (e + 1) / 41.
        red, green, _ = render(90 + 19)[0]
        self.assertAlmostEqual(21 / 41, red, places=6)
        self.assertAlmostEqual(20 / 41, green, places=6)
        self.assertEqual((0.0, 0.0, 1.0), render(210)[0])
        balls = render_sequence_fixtures(compiled, "ball", [(0.0, 0.0, 0.0)], 150, fps=40)
        self.assertGreater(balls[0][0], 0)
        self.assertEqual(2, sequence_position(compiled, 2400 + 220)["loopIteration"])

    def test_spinner_overrides(self):
        raw = show()
        raw["sections"][0]["overrides"] = [
            {"units": [3, 2], "spinner": solid(0, 0, 1)},
            {"units": [5], "spinner": solid(1, 1, 0)},
        ]
        document = validate_show(raw, 4)
        self.assertEqual([2, 3], document["sections"][0]["overrides"][0]["units"])
        self.assertNotIn("overrides", document["sections"][1])
        self.assertEqual(document, validate_show(json.loads(json.dumps(document)), 4))
        for bad in ([{"units": [2], "spinner": solid(0, 0, 1)}, {"units": [2], "spinner": solid(0, 1, 0)}],
                    [{"units": [0], "spinner": solid(0, 0, 1)}], [{"units": [], "spinner": solid(0, 0, 1)}],
                    [{"units": [2, 2], "spinner": solid(0, 0, 1)}], [{"units": [2]}]):
            broken = show()
            broken["sections"][0]["overrides"] = bad
            with self.assertRaises(ValueError):
                validate_show(broken, 4)

        compiled = compile_show(raw, CLICK, 40, 4)

        def colour(unit_id: int, frame: int = 40) -> tuple[float, ...]:
            unit = {"unitId": unit_id, "logicalPixelCount": 4, "frameOffset": 0}
            return render_sequence_frame(compiled, unit, frame, unit_count=6, fps=40, rpm=360)[0]

        # Section 1: unit 1 plays the main red, units 2 and 3 the blue override, unit 5 the yellow one.
        self.assertEqual([(1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)],
                         [colour(unit) for unit in range(1, 6)])
        # Section 2 has no overrides: every unit is green; the crossfade blends each unit's own outgoing effect.
        self.assertEqual((0.0, 1.0, 0.0), colour(2, 150))
        red, green, blue = colour(2, 90 + 19)
        self.assertAlmostEqual(20 / 41, green, places=6)
        self.assertAlmostEqual(21 / 41, blue, places=6)
        self.assertEqual(0.0, red)

    def test_frame_from_music_time(self):
        looping = compile_show(show(), CLICK, 40, 4)
        self.assertEqual(20, show_frame(looping, 0.5, 40))
        self.assertEqual(2400 + 20, show_frame(looping, 60.5, 40))
        self.assertEqual(0, show_frame(looping, -0.1, 40))
        once = compile_show(show(ending={"kind": "fade-black", "seconds": 1}), CLICK, 40, 4)
        self.assertEqual(2420, show_frame(once, 60.5, 40))
        self.assertFalse(show_finished(once, 2439))
        self.assertTrue(show_finished(once, 2440))
        self.assertFalse(show_finished(looping, 10 ** 6))


class StoreTests(unittest.TestCase):
    def test_save_reload_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shows.json"
            store = ShowStore(path, beat_maps={CLICK_TRACK_ID: CLICK}, fps=40, pixels=4)
            state, compiled = store.save("demo", show())
            self.assertEqual(["demo"], [entry["id"] for entry in state["shows"]])
            self.assertEqual(90, compiled[1]["start"])
            with self.assertRaises(ValueError):
                store.save("other", show())
            reloaded = ShowStore(path, beat_maps={CLICK_TRACK_ID: CLICK}, fps=40, pixels=4)
            self.assertEqual(state["shows"], reloaded.state()["shows"])
            # 拍點檔不見時列出錯誤，服務照常啟動。 / A missing beat map is listed as an error, not a startup failure.
            orphan = ShowStore(path, beat_maps={}, fps=40, pixels=4)
            self.assertIn("demo", orphan.state()["errors"])
            with self.assertRaises(ValueError):
                orphan.compiled("demo")
            self.assertEqual([], reloaded.delete("demo")["shows"])
            with self.assertRaises(KeyError):
                reloaded.delete("demo")
            self.assertEqual({"schemaVersion": 1, "shows": []}, json.loads(path.read_text(encoding="utf-8")))


class PlayerTests(unittest.TestCase):
    def store(self, directory, **overrides):
        store = ShowStore(Path(directory) / "shows.json", beat_maps={CLICK_TRACK_ID: CLICK}, fps=40, pixels=4)
        store.save("demo", show(**overrides))
        return store

    def test_follows_the_music_time(self):
        with tempfile.TemporaryDirectory() as directory:
            audio, clock = FakeAudio(), FakeClock()
            player = ShowPlayer(self.store(directory), audio, 40, clock=clock)
            self.assertIsNone(player.current())
            player.play({"showId": "demo", "startSeconds": 2})
            self.assertEqual([{"trackId": CLICK_TRACK_ID, "startSeconds": 2.0, "loop": True}], audio.played)
            # 還沒出聲：停在起點。 / Before the first sound: hold the start.
            self.assertEqual(80, player.current()[1])
            audio.time = 5.3
            schedule, frame, generation = player.current()
            self.assertEqual(212, frame)
            self.assertEqual("demo", schedule[0]["show"]["id"])
            self.assertEqual(round(5.3, 6), round(player.state()["playing"]["positionSeconds"], 6))
            player.stop()
            self.assertEqual(1, audio.stopped)
            self.assertIsNone(player.current())

    def test_ending_fade_then_standby(self):
        with tempfile.TemporaryDirectory() as directory:
            audio, clock = FakeAudio(), FakeClock()
            player = ShowPlayer(self.store(directory, ending={"kind": "fade-black", "seconds": 1}), audio, 40, clock=clock)
            player.play({"showId": "demo"})
            audio.time = 59.9
            generation = player.current()[2]
            # 曲子播完：從最後讀到的時間往後推，跑完 1 秒淡出後結束。
            # Track finished: extrapolate from the last time read, then end after the 1 s fade.
            audio.time = None
            clock.now += 0.5
            self.assertEqual(math_floor((59.9 + 0.5) * 40), player.current()[1])
            clock.now += 0.7
            self.assertIsNone(player.current())
            self.assertIsNone(player.state()["playing"])
            self.assertGreater(player.generation, generation)

    def motor_player(self, directory, **overrides):
        audio, clock, calls = FakeAudio(), FakeClock(), []
        player = ShowPlayer(self.store(directory, **overrides), audio, 40, clock=clock)
        player.on_motors = calls.append
        return player, audio, clock, calls

    def test_start_motors_with_the_show_and_stop_them_when_it_ends(self):
        with tempfile.TemporaryDirectory() as directory:
            player, audio, clock, calls = self.motor_player(directory, ending={"kind": "fade-black", "seconds": 1})
            player.play({"showId": "demo", "startMotors": True})
            self.assertEqual([True], calls)
            self.assertEqual(1, len(audio.played))
            audio.time = 59.9
            player.current()
            audio.time = None
            clock.now += 1.2
            self.assertIsNone(player.current())
            self.assertEqual([True, False], calls)
            # 沒勾啟動馬達的播放，結束時不動馬達。 / A play without startMotors leaves the motors alone.
            player.play({"showId": "demo"})
            player.stop(manual=True)
            self.assertEqual([True, False], calls)

    def test_motors_stop_on_the_stop_button_only(self):
        with tempfile.TemporaryDirectory() as directory:
            player, audio, clock, calls = self.motor_player(directory)
            player.play({"showId": "demo", "startMotors": True})
            # 循環演出不會結束；換演出、套用效果（非手動的停止）都不停馬達。
            # A looping show never ends; another show or an effect (a non-manual stop) leaves the motors running.
            audio.time = 59.9
            clock.now += 500
            self.assertIsNotNone(player.current())
            player.play({"showId": "demo", "startMotors": True})
            player.stop()
            self.assertEqual([True, True], calls)
            player.play({"showId": "demo", "startMotors": True})
            player.stop(manual=True)
            self.assertEqual([True, True, True, False], calls)

    def test_delay_starts_the_motors_first(self):
        with tempfile.TemporaryDirectory() as directory:
            player, audio, clock, calls = self.motor_player(directory)
            state = player.play({"showId": "demo", "startSeconds": 2, "startMotors": True, "delaySeconds": 30})
            self.assertEqual([True], calls)
            self.assertEqual([], audio.played)
            self.assertIsNone(state["playing"])
            self.assertEqual({"showId": "demo", "remainingSeconds": 30.0}, state["starting"])
            self.assertIsNone(player.current())
            timer = player._pending[3]
            player._begin_pending(timer)
            self.assertEqual([{"trackId": CLICK_TRACK_ID, "startSeconds": 2.0, "loop": True}], audio.played)
            self.assertNotIn("starting", player.state())
            self.assertEqual("demo", player.state()["playing"]["showId"])
            # 等待中按停止：不播，馬達停；過期的計時器不再啟動演出。
            # Stop while waiting: no show, motors stop; a stale timer no longer starts anything.
            player.stop()
            player.play({"showId": "demo", "startMotors": True, "delaySeconds": 30})
            stale = player._pending[3]
            self.assertEqual({"playing": None}, player.stop(manual=True))
            player._begin_pending(stale)
            self.assertEqual(1, len(audio.played))
            self.assertEqual([True, True, False], calls)

    def test_rejects_bad_motor_options(self):
        with tempfile.TemporaryDirectory() as directory:
            player, _, _, calls = self.motor_player(directory)
            for bad in ({"startMotors": 1}, {"delaySeconds": -1}, {"delaySeconds": 121}, {"motors": True}):
                with self.assertRaises(ValueError):
                    player.play({"showId": "demo", **bad})
            player.on_motors = None
            with self.assertRaises(ValueError):
                player.play({"showId": "demo", "startMotors": True})
            self.assertEqual([], calls)

    def test_silent_without_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            player = ShowPlayer(self.store(directory), None, 40, clock=clock)
            player.play({"showId": "demo"})
            clock.now += 2.25
            self.assertEqual(90, player.current()[1])
            self.assertTrue(player.state()["playing"]["silent"])

    def test_saving_the_playing_show_swaps_it_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = FakeAudio()
            store = self.store(directory)
            player = ShowPlayer(store, audio, 40, clock=FakeClock())
            player.play({"showId": "demo"})
            generation = player.current()[2]
            sections = show()["sections"]
            _, compiled = store.save("demo", show(sections=sections[:2]))
            player.replace(compiled)
            schedule, _, swapped = player.current()
            self.assertEqual(2, len(schedule))
            self.assertGreater(swapped, generation)
            self.assertEqual(0, audio.stopped)
            # 換成不循環：要重新開始音樂，所以停止。 / No longer looping: the music must restart, so it stops.
            _, compiled = store.save("demo", show(ending={"kind": "fade-black", "seconds": 1}))
            player.replace(compiled)
            self.assertIsNone(player.current())
            self.assertEqual(1, audio.stopped)

    def test_rejects_bad_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            player = ShowPlayer(self.store(directory), FakeAudio(), 40, clock=FakeClock())
            for bad in ({}, {"showId": "missing"}, {"showId": "demo", "startSeconds": 61}, {"showId": "demo", "loop": True}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    player.play(bad)


def math_floor(value):
    return int(value // 1)


class Client:
    sent = []

    def __init__(self, *_args, **_kwargs):
        pass

    def set_simplified(self, _value):
        pass

    def set_net(self, _value):
        pass

    def set_subnet(self, _value):
        pass

    def send(self, payload):
        Client.sent.append(bytes(payload))

    def close(self):
        pass


class OutputTests(unittest.TestCase):
    def test_show_replaces_standby_and_standby_restarts_after_it(self):
        Client.sent = []
        compiled = compile_show(show(), CLICK, 40, 4)
        shows = iter([None, (compiled, 0, 1), (compiled, 135, 1), None])
        reports = []
        config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 4,
                  "unit_id": 1, "unit_count": 1}
        times = iter([0.0, 0.0, 0.0, 0.025, 0.025, 0.05, 0.05, 0.075, 0.075, 1.0])
        stream(config, validate_preset(solid(1, 1, 1), 4), seconds=0.09, client_factory=Client,
               show_provider=lambda: next(shows), playback_reporter=lambda *args: reports.append(args),
               clock=lambda: next(times), sleep=lambda _s: None)
        self.assertEqual(["single", "show", "show", "single"], [report[0] for report in reports])
        self.assertEqual([0, 0, 135, 0], [report[3] for report in reports])
        # 重播計數：演出開始與結束各一次。 / Restart count: once when the show starts and once when it ends.
        self.assertEqual([0, 1, 1, 2], [report[2] for report in reports])
        self.assertEqual(bytes([255] * 12), Client.sent[0][:12])
        self.assertEqual(bytes([255, 0, 0] * 4), Client.sent[1][:12])
        self.assertEqual(bytes([0, 255, 0] * 4), Client.sent[2][:12])

    def test_playback_monitor_reports_the_show(self):
        compiled = compile_show(show(), CLICK, 40, 4)
        monitor = PlaybackMonitor(40, 1)
        monitor.report("show", compiled, 3, 120)
        state = monitor.state()
        self.assertEqual("show", state["mode"])
        self.assertEqual(1, state["step"]["index"])
        self.assertEqual({"id": "demo", "title": "Demo", "trackId": CLICK_TRACK_ID, "durationSeconds": 60.0,
                          "totalFrames": 2400, "loop": True}, state["show"])


class ApiTests(unittest.TestCase):
    def test_save_play_stop_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            audio = FakeAudio()
            store = ShowStore(root / "shows.json", beat_maps={CLICK_TRACK_ID: CLICK}, fps=40, pixels=156)
            player = ShowPlayer(store, audio, 40)
            config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 156,
                      "unit_id": 1, "unit_count": 9}
            with make_server(PresetStore(root / "preset.json", 156), config, 0,
                             show_store=store, show_player=player) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}"

                    def call(method, path, body=None):
                        data = None if body is None else json.dumps(body).encode()
                        request = Request(base + path, data, {"Content-Type": "application/json"}, method=method)
                        with urlopen(request) as response:
                            return json.load(response)

                    self.assertEqual({"revision": 0, "shows": [], "errors": {}, "playing": None}, call("GET", "/api/shows"))
                    self.assertEqual(1, len(call("PUT", "/api/shows/demo", show())["shows"]))
                    for path, body, code in (("/api/shows/other", show(), 422), ("/api/show/play", {"showId": "nope"}, 422)):
                        with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                            call("PUT", path, body)
                        self.assertEqual(code, caught.exception.code)
                    self.assertEqual("demo", call("PUT", "/api/show/play", {"showId": "demo"})["playing"]["showId"])
                    # 套用單一效果會停止演出。 / Applying a preset stops the show.
                    call("PUT", "/api/preset", default_preset("full-on"))
                    self.assertIsNone(call("GET", "/api/shows")["playing"])
                    call("PUT", "/api/show/play", {"showId": "demo"})
                    self.assertIsNone(call("DELETE", "/api/show/play")["playing"])
                    self.assertEqual([], call("DELETE", "/api/shows/demo")["shows"])
                    with self.assertRaises(HTTPError) as caught:
                        call("DELETE", "/api/shows/demo")
                    self.assertEqual(404, caught.exception.code)
                finally:
                    server.shutdown()


class TrackFileTests(unittest.TestCase):
    def test_serves_beat_maps_and_wav_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
            tracks = root / "tracks"
            tracks.mkdir()
            write_wav(root / "song.wav", 100)
            (tracks / "song.json").write_text(json.dumps({
                "schemaVersion": 1, "id": "song", "title": "Song", "file": "song.wav", "durationSeconds": 0.1,
                "tempo": None, "beatsPerBar": 4, "firstDownbeat": 0, "beats": [0.05],
            }), encoding="utf-8")
            player = AudioPlayer({"device": "", "directory": str(root), "calibration_path": str(root / "cal.json")},
                                 tracks_dir=tracks, sounddevice=FakeSounddevice())
            config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 156,
                      "unit_id": 1, "unit_count": 9}
            with make_server(PresetStore(root / "preset.json", 156), config, 0, audio_player=player) as server:
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}/api/audio/tracks/"
                    with urlopen(base + "song") as response:
                        self.assertEqual([0.05], json.load(response)["beats"])
                    with urlopen(base + CLICK_TRACK_ID) as response:
                        self.assertEqual(120, len(json.load(response)["beats"]))
                    with urlopen(base + "song/wav") as response:
                        self.assertEqual("audio/wav", response.headers["Content-Type"])
                        self.assertEqual((root / "song.wav").read_bytes(), response.read())
                    for path in (CLICK_TRACK_ID + "/wav", "missing", "song/other", "../song"):
                        with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                            urlopen(base + path)
                        self.assertEqual(404, caught.exception.code)
                finally:
                    server.shutdown()


def song_map(**overrides):
    document = {
        "schemaVersion": 1, "id": "song", "title": "Song", "file": "song.wav", "durationSeconds": 4.0,
        "tempo": 60.0, "beatsPerBar": 4, "firstDownbeat": 0, "beats": [0.5, 1.5, 2.5, 3.5],
    }
    document.update(overrides)
    return document


class BeatEditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "tracks").mkdir()
        (self.root / "tracks" / "song.json").write_text(json.dumps(song_map()), encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def player(self):
        return AudioPlayer({"device": "", "directory": str(self.root), "calibration_path": str(self.root / "cal.json")},
                           tracks_dir=self.root / "tracks", edits_dir=self.root / "edits", sounddevice=FakeSounddevice())

    def test_corrections_replace_the_shipped_map_and_revert(self):
        player = self.player()
        saved = player.save_beat_map("song", song_map(beats=[0.4, 1.4, 2.6], firstDownbeat=1, source="手動修正"))
        self.assertEqual([0.4, 1.4, 2.6], player.beat_map("song")["beats"])
        self.assertEqual(saved, json.loads((self.root / "edits" / "song.json").read_text(encoding="utf-8")))
        # 重新啟動後仍用修正版，且標示為已修正；內建檔不動。 / After a restart the correction still applies; the shipped file is untouched.
        reloaded = self.player()
        self.assertEqual([0.4, 1.4, 2.6], reloaded.beat_map("song")["beats"])
        self.assertEqual({CLICK_TRACK_ID: False, "song": True},
                         {track["id"]: track["edited"] for track in reloaded.state()["tracks"]})
        self.assertEqual([0.5, 1.5, 2.5, 3.5], json.loads((self.root / "tracks" / "song.json").read_text(encoding="utf-8"))["beats"])
        self.assertEqual([0.5, 1.5, 2.5, 3.5], reloaded.revert_beat_map("song")["beats"])
        self.assertFalse((self.root / "edits" / "song.json").exists())
        self.assertEqual([0.5, 1.5, 2.5, 3.5], self.player().beat_map("song")["beats"])

    def test_rejects_other_tracks_and_changed_identity(self):
        player = self.player()
        for track, document in (
            ("song", song_map(file="other.wav")), ("song", song_map(durationSeconds=5.0)), ("song", song_map(beats=[2.0, 1.0])),
            ("missing", song_map(id="missing")), (CLICK_TRACK_ID, song_map(id=CLICK_TRACK_ID)), ("song", song_map(id="other")),
        ):
            with self.subTest(track=track, document=document), self.assertRaises(ValueError):
                player.save_beat_map(track, document)
        with self.assertRaises(ValueError):
            AudioPlayer(None, tracks_dir=self.root / "tracks", edits_dir=self.root / "tracks")

    def test_api_saves_reverts_and_updates_the_playing_show(self):
        (self.root / "preset.json").write_text(json.dumps(default_preset("center-out-sine")), encoding="utf-8")
        player = self.player()
        store = ShowStore(self.root / "shows.json", beat_maps=player.beat_maps, fps=40, pixels=156)
        shows = ShowPlayer(store, None, 40)
        store.save("demo", show(trackId="song", sections=[
            {**show()["sections"][0]}, {**show()["sections"][1], "start": {"bar": 1, "beat": 2}},
        ]))
        shows.play({"showId": "demo"})
        config = {"ip": "127.0.0.1", "source_ip": None, "universe": 0, "fps": 40, "rpm": 360, "pixels": 156,
                  "unit_id": 1, "unit_count": 9}
        with make_server(PresetStore(self.root / "preset.json", 156), config, 0, audio_player=player,
                         show_store=store, show_player=shows) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}/api/audio/tracks/song"

                def call(method, body=None):
                    data = None if body is None else json.dumps(body).encode()
                    with urlopen(Request(base, data, {"Content-Type": "application/json"}, method=method)) as response:
                        return json.load(response)

                self.assertEqual(60, shows.current()[0][1]["start"])  # bar 1 beat 2 = 1.5 s
                self.assertEqual([0.5, 1.25, 2.5, 3.5], call("PUT", song_map(beats=[0.5, 1.25, 2.5, 3.5]))["beats"])
                # 播放中的演出改用新拍點。 / The playing show follows the new beats.
                self.assertEqual(50, shows.current()[0][1]["start"])
                self.assertEqual([0.5, 1.5, 2.5, 3.5], call("DELETE")["beats"])
                self.assertEqual(60, shows.current()[0][1]["start"])
                with self.assertRaises(HTTPError) as caught:
                    call("PUT", song_map(durationSeconds=9.0))
                self.assertEqual(422, caught.exception.code)
            finally:
                server.shutdown()


class PreloadTests(unittest.TestCase):
    def test_preload_reads_every_track_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracks = root / "tracks"
            tracks.mkdir()
            write_wav(root / "song.wav", 4000)
            (tracks / "song.json").write_text(json.dumps({
                "schemaVersion": 1, "id": "song", "title": "Song", "file": "song.wav", "durationSeconds": 4.0,
                "tempo": 60.0, "beatsPerBar": 4, "firstDownbeat": 0, "beats": [0.5, 1.5, 2.5, 3.5],
            }), encoding="utf-8")
            (tracks / "gone.json").write_text(json.dumps({
                "schemaVersion": 1, "id": "gone", "title": "Gone", "file": "gone.wav", "durationSeconds": 4.0,
                "tempo": None, "beatsPerBar": 4, "firstDownbeat": 0, "beats": [],
            }), encoding="utf-8")
            player = AudioPlayer({"device": "", "directory": str(root), "calibration_path": str(root / "cal.json")},
                                 tracks_dir=tracks, sounddevice=FakeSounddevice())
            loaded = {track["id"]: track["loaded"] for track in player.state()["tracks"]}
            self.assertEqual({CLICK_TRACK_ID: True, "song": False, "gone": False}, loaded)
            self.assertEqual([], player.preload())
            loaded = {track["id"]: track["loaded"] for track in player.state()["tracks"]}
            self.assertEqual({CLICK_TRACK_ID: True, "song": True, "gone": False}, loaded)


if __name__ == "__main__":
    unittest.main()
