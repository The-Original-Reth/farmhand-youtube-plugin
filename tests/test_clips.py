"""
Clips: what went to YouTube kept for five minutes and a little more, and the last 1 second to 5 minutes of it saved
as an MP4 in the clips folder, starting at a whole frame (live_stream/clips.py, clip_routes.py).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from streamer import config
from streamer.live_stream import buffer as buffer_module
from streamer.live_stream import clips

from .conftest import wait_until
from .fake_ffmpeg import _keyframe_packet, frame_at

KEY = _keyframe_packet()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _second(clock, recent, key: bool):
    """One second of stream going out: a keyframe's packet first when `key`, then a plain frame."""
    recent.add((KEY if key else b"") + frame_at(clock.now))
    clock.now += 1


def test_what_went_to_youtube_is_kept_five_minutes_and_a_little_more():
    clock = Clock()
    recent = clips.Recent(clock=clock)
    assert recent.held() == 0 and recent.last(30) == (b"", 0.0)
    for s in range(400):
        _second(clock, recent, key=s % 2 == 0)
    assert recent.held() == pytest.approx(clips.MOST_SECONDS + clips.EXTRA, abs=1)


def test_a_clip_starts_at_the_whole_frame_at_or_just_before_the_time_asked_for():
    clock = Clock()
    recent = clips.Recent(clock=clock)
    for s in range(60):
        _second(clock, recent, key=s % 2 == 0)  # a keyframe every 2 s
    data, seconds = recent.last(5)  # (5 s back is an odd second: the keyframe a second before it)
    assert data.startswith(KEY) and seconds == 6
    data, seconds = recent.last(6)
    assert data.startswith(KEY) and seconds == 6
    data, seconds = recent.last(1000)  # longer than what's kept: all of it
    assert seconds == 60 and data.startswith(KEY)


def test_a_clip_with_no_whole_frame_before_starts_at_the_first_after():
    clock = Clock()
    recent = clips.Recent(clock=clock)
    for s in range(10):
        _second(clock, recent, key=s == 7)
    data, seconds = recent.last(8)
    assert data.startswith(KEY) and seconds == 3


def test_what_the_sender_takes_is_kept_for_clips():
    class Sink:
        def __init__(self):
            self.got = []

        def write(self, data):
            self.got.append(data)

        def flush(self):
            pass

    kept = []
    b = buffer_module.Buffer(0)
    b.sent = kept.append
    sink = Sink()
    b.set_sink(sink)
    b.write(KEY + frame_at(1.0))
    b.write(frame_at(2.0))
    try:
        assert wait_until(lambda: len(kept) == 2)
        assert kept == sink.got
    finally:
        b.close()


# ------------------------------------------------------------------ saving them


def _ffmpeg_makes_an_mp4(calls):
    def run(args, capture_output=True, timeout=None):
        calls.append(args)
        Path(args[-1]).write_bytes(b"....ftypisom" + Path(args[args.index("-i") + 1]).read_bytes())
        return subprocess.CompletedProcess(args, 0, b"", b"")
    return run


@pytest.fixture()
def kept(monkeypatch):
    clock = Clock()
    recent = clips.Recent(clock=clock)
    monkeypatch.setattr(clips, "recent", recent)
    for s in range(120):
        _second(clock, recent, key=s % 2 == 0)
    return recent


def test_a_clip_is_saved_whole_in_the_clips_folder_named_for_when(tmp_path, kept):
    calls = []
    got = clips.save("ffmpeg", tmp_path, 30, "America/New_York", run=_ffmpeg_makes_an_mp4(calls))
    assert got["seconds"] == 30 and got["size"] > 0
    assert clips.NAME.match(got["name"]) and got["name"].endswith(" (30 s).mp4")
    assert [p.name for p in tmp_path.iterdir()] == [got["name"]]  # (no .part or .ts left behind)
    args = calls[0]
    assert args[args.index("-c") + 1] == "copy" and "+faststart" in args and args[-1].endswith(".part")
    # one taken the same second gets a number
    again = clips.save("ffmpeg", tmp_path, 30, "America/New_York", run=_ffmpeg_makes_an_mp4(calls))
    assert again["name"] != got["name"]
    assert [c["name"] for c in clips.listed(tmp_path)][0] in (got["name"], again["name"])


def test_a_clip_that_cant_be_saved_says_why(tmp_path, kept, monkeypatch):
    with pytest.raises(ValueError, match="no clips folder"):
        clips.save("ffmpeg", tmp_path / "nowhere", 30)
    fails = lambda args, **kw: subprocess.CompletedProcess(args, 1, b"", b"Invalid data found when processing input\n")  # noqa: E731
    with pytest.raises(ValueError, match="Invalid data"):
        clips.save("ffmpeg", tmp_path, 30, run=fails)
    assert list(tmp_path.iterdir()) == []  # (nothing half-made left)
    kept.clear()
    with pytest.raises(ValueError, match="Nothing has gone to YouTube yet"):
        clips.save("ffmpeg", tmp_path, 30, run=_ffmpeg_makes_an_mp4([]))


# ------------------------------------------------------------------ the page's API


@pytest.fixture()
def clips_api(api, tmp_path, monkeypatch, kept):
    folder = tmp_path / "YouTube clips"
    folder.mkdir()
    monkeypatch.setattr(config.settings, "clips_dir", folder)
    monkeypatch.setattr(config.settings, "clips_shown_as", r"\\nas\3d_Printing_Elves\Farmhand\YouTube clips")
    from streamer.live_stream import procs
    monkeypatch.setattr(procs, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(clips.subprocess, "run", _ffmpeg_makes_an_mp4([]))
    return api, folder


def test_the_page_sets_how_long_a_clip_is_and_takes_one(clips_api):
    api, folder = clips_api
    page = api.get("/api/clips").json()
    assert page["seconds"] == 3 and (page["least"], page["most"]) == (1, 300) and page["held"] > 100  # (3 s unless changed)
    assert page["folder"] == {"path": str(folder), "shown_as": r"\\nas\3d_Printing_Elves\Farmhand\YouTube clips", "problem": None}
    assert page["clips"] == []

    assert api.put("/api/clips", json={"seconds": 0}).status_code == 422
    assert api.put("/api/clips", json={"seconds": 301}).status_code == 422
    assert api.put("/api/clips", json={"seconds": 90}).json()["seconds"] == 90

    r = api.post("/api/clips", json={"tz": "America/New_York"})
    assert r.status_code == 201 and r.json()["seconds"] == 90
    name = r.json()["name"]
    assert [c["name"] for c in api.get("/api/clips").json()["clips"]] == [name]
    got = api.get(f"/api/clips/{name}")
    assert got.status_code == 200 and got.headers["content-type"] == "video/mp4" and got.content.startswith(b"....ftyp")
    assert api.post("/api/clips", json={"seconds": 5}).json()["seconds"] in (5, 6)  # (from the whole frame before)

    assert api.get("/api/clips/..%2Fsettings.json").status_code == 404
    assert api.get("/api/clips/not-a-clip.mp4").status_code == 404


def test_no_clips_folder_is_said_on_the_page(clips_api, monkeypatch, tmp_path):
    api, _ = clips_api
    monkeypatch.setattr(config.settings, "clips_dir", tmp_path / "not-mounted")
    page = api.get("/api/clips").json()
    assert "no clips folder" in page["folder"]["problem"]
    r = api.post("/api/clips", json={})
    assert r.status_code == 409 and "no clips folder" in r.json()["detail"]


def test_a_one_second_clip_starts_at_the_whole_frame_before_it(tmp_path, kept):
    got = clips.save("ffmpeg", tmp_path, 1, run=_ffmpeg_makes_an_mp4([]))
    assert 1 <= got["seconds"] <= 3  # (a whole frame every 2 s: up to 2 s more than asked)


def test_a_clip_is_3_seconds_unless_changed():
    assert clips.seconds_of({}) == 3
    assert clips.seconds_of({clips.CLIP_KEY: "120"}) == 120
    assert clips.seconds_of({clips.CLIP_KEY: "nonsense"}) == 3


# ------------------------------------------------------------------ clips from a camera: the next N seconds


def test_the_clip_sources_are_the_stream_then_every_camera_farmhand_can_see(api, plugin, farmhand):
    from streamer import farm

    farm.cameras(plugin)
    from streamer.live_stream.sources import known

    assert clips.sources(known(plugin)) == [
        {"key": "stream", "label": "Stream"},
        {"key": "2:Camera", "label": "Core One · Camera"},  # (the Nozzle only plays in a browser: not listed)
        {"key": "1:Bed", "label": "VT 1190"},
    ]


def test_a_camera_is_recorded_for_the_next_seconds_copied_when_it_can_be():
    rtsp = {"kind": "rtsp", "ffmpeg_input": ["-rtsp_transport", "tcp", "-i", "rtsp://10.0.6.33:8554/chamber"], "ffmpeg_filters": []}
    args = clips.camera_command("ffmpeg", rtsp, 30, "out.part")
    assert args[args.index("-t") + 1] == "32" and args[args.index("-c:v") + 1] == "copy" and "-an" in args  # (+2: see KEYFRAME_EVERY)
    mjpeg = {"kind": "mjpeg", "ffmpeg_input": ["-f", "mpjpeg", "-i", "http://10.0.6.21/webcam/?action=stream"], "ffmpeg_filters": ["hflip"]}
    args = clips.camera_command("ffmpeg", mjpeg, 5, "out.part")
    assert args[args.index("-c:v") + 1] == "libx264" and args[args.index("-vf") + 1] == "hflip" and args[args.index("-t") + 1] == "5"


def test_a_camera_clip_is_recorded_in_the_background_and_named_for_its_camera(clips_api, plugin, farmhand, monkeypatch):
    from streamer import farm

    api, folder = clips_api
    farm.cameras(plugin)
    page = api.get("/api/clips").json()
    assert [s["key"] for s in page["sources"]] == ["stream", "2:Camera", "1:Bed"] and page["recording"] == []

    ran = []

    def slow_run(args, capture_output=True, timeout=None):
        ran.append(args)
        import time
        time.sleep(0.3)  # (recording)
        Path(args[-1]).write_bytes(b"....ftypisom")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(clips.subprocess, "run", slow_run)
    r = api.post("/api/clips", json={"source": "1:Bed", "seconds": 10, "tz": "America/New_York"})
    assert r.status_code == 202 and r.json()["recording"]["label"] == "VT 1190"
    assert api.get("/api/clips").json()["recording"][0]["source"] == "1:Bed"
    assert api.post("/api/clips", json={"source": "1:Bed"}).status_code == 409  # (one at a time per camera)
    assert wait_until(lambda: api.get("/api/clips").json()["recording"] == [], timeout=3)
    ended = api.get("/api/clips").json()["ended"][0]
    assert ended["ok"] and ended["name"].endswith(" VT 1190 (10 s).mp4") and clips.NAME.match(ended["name"])
    assert ended["name"] in [c["name"] for c in api.get("/api/clips").json()["clips"]]
    assert ran[0][ran[0].index("-t") + 1] == "10"

    assert api.post("/api/clips", json={"source": "2:Nozzle"}).status_code == 422  # (can't be recorded)
    assert api.post("/api/clips", json={"source": "9:Gone"}).status_code == 422


def test_a_camera_clip_that_fails_says_why(clips_api, plugin, farmhand, monkeypatch):
    from streamer import farm

    api, folder = clips_api
    farm.cameras(plugin)
    monkeypatch.setattr(clips.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 1, b"", b"Connection refused\n"))
    assert api.post("/api/clips", json={"source": "1:Bed", "seconds": 3}).status_code == 202
    assert wait_until(lambda: api.get("/api/clips").json()["ended"] != [], timeout=3)
    ended = api.get("/api/clips").json()["ended"][0]
    assert not ended["ok"] and "Connection refused" in ended["text"] and list(folder.iterdir()) == []


def test_the_clip_list_follows_cameras_added_and_removed_in_farmhand(api, plugin, farmhand):
    """The watcher asks Farmhand for the cameras (every minute while the plugin runs): a camera added there shows up
    in the Clip drop-down, one removed (or its printer) goes; a printer that's only off keeps what it had."""
    from streamer import camera_watch

    from .fake_farmhand import camera

    keys = lambda: [s["key"] for s in api.get("/api/clips").json()["sources"]]  # noqa: E731
    camera_watch.refresh()
    assert keys() == ["stream", "2:Camera", "1:Bed"]

    farmhand.cameras[3] = {"printer_id": 3, "printer_name": "VT 1026", "error": None,
                           "cameras": [camera("Front", "http://10.0.6.40/webcam/?action=stream")]}
    camera_watch.refresh()
    assert keys() == ["stream", "2:Camera", "3:Front", "1:Bed"]  # added (in order of printer name)

    farmhand.cameras[1]["cameras"] = []
    camera_watch.refresh()
    assert "1:Bed" not in keys()  # its camera removed in Mainsail

    farmhand.cameras[3] = {"printer_id": 3, "printer_name": "VT 1026", "error": "Moonraker didn't answer.", "cameras": []}
    camera_watch.refresh()
    assert "3:Front" in keys()  # only off: kept

    del farmhand.cameras[3]
    camera_watch.refresh()
    assert "3:Front" not in keys()  # taken off Farmhand
