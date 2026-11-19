"""
The cameras unpacked on an NVIDIA card (camera_decoding.py): tried once, each camera's feeder then unpacking and
fitting it on the card and handing raw frames to the picture (no JPEGs either side); the processor as before when the
card doesn't work, saying why; and one camera the card can't unpack read on the processor on its own.
"""
from __future__ import annotations

import io

import pytest

from PIL import Image

from streamer.live_stream import camera_decoding, down_square, feeders, raw_frames
from streamer.live_stream import pump as pumps

from .conftest import grid, wait_until

pytestmark = pytest.mark.usefixtures("stream_fresh")

BED = "http://10.0.6.21/webcam/?action=stream"


@pytest.fixture()
def card(monkeypatch, ffmpeg):
    """An NVIDIA card that works (nothing real is tried)."""
    monkeypatch.delenv(camera_decoding.CAMERAS_SETTING, raising=False)
    camera_decoding._found.clear()
    monkeypatch.setattr(camera_decoding, "_gpu_works", lambda ffmpeg: (True, None))
    yield ffmpeg
    camera_decoding._found.clear()


def test_a_camera_is_unpacked_and_fitted_on_the_card_and_handed_over_raw():
    args = feeders.raw_feeder_command("ffmpeg", ["-rtsp_transport", "tcp", "-i", "rtsp://cam/chamber"], ["hflip", "vflip"], (1280, 720))
    assert args[args.index("-hwaccel") + 1] == "cuda" and args.index("-hwaccel") < args.index("-i")
    assert args[args.index("-hwaccel_output_format") + 1] == "cuda"
    chain = args[args.index("-vf") + 1].split(",")
    assert chain[0].startswith("scale_cuda=1280:720:force_original_aspect_ratio=decrease") and "format=yuv420p" in chain[0]
    assert chain[1:5] == ["hwdownload", "format=yuv420p", "hflip", "vflip"]  # (only the turning left to the processor)
    assert not any(p.startswith("pad=") for p in chain)  # (its own shape: the first line says the size)
    assert args[-5:] == ["-pix_fmt", "yuv420p", "-f", "yuv4mpegpipe", "pipe:1"] and "mjpeg" not in args

    # on its side: fitted the other way round on the card, then turned
    turned = feeders.raw_feeder_command("ffmpeg", ["-i", "rtsp://cam"], ["transpose=1"], (1280, 720))
    chain = turned[turned.index("-vf") + 1].split(",")
    assert chain[0].startswith("scale_cuda=720:1280:") and chain[3] == "transpose=1"

    # the processor's own, for a camera the card can't unpack: turned, fitted, the same raw frames
    cpu = feeders.raw_feeder_command("ffmpeg", ["-i", "rtsp://cam"], ["hflip"], (1280, 720), gpu=False)
    chain = cpu[cpu.index("-vf") + 1].split(",")
    assert "-hwaccel" not in cpu and chain[0] == "hflip" and chain[1].startswith("scale=1280:720")
    assert cpu[-5:] == ["-pix_fmt", "yuv420p", "-f", "yuv4mpegpipe", "pipe:1"]


def test_a_raw_cameras_first_line_says_its_size():
    assert raw_frames.y4m_size(b"YUV4MPEG2 W1280 H720 F15:1 Ip A1:1 C420mpeg2 XYSCSS=420MPEG2") == (1280, 720)
    assert raw_frames.y4m_size(b"YUV4MPEG2 W640 H480 F15:1") == (640, 480)
    assert raw_frames.y4m_size(b"YUV4MPEG2 W640 H480 C444") is None and raw_frames.y4m_size(b"\xff\xd8") is None


def test_going_live_with_the_card_hands_every_camera_to_the_picture_raw_at_its_own_shape(api, card, farmhand):
    # the sample farm's two are 4:3 to Mainsail; the Bed camera's video is really 16:9
    card.shapes["http://10.0.6.21/webcam/?action=stream"] = (1280, 720)
    card.shapes.update({u: (960, 720) for u in ("rtsp://10.0.6.33:8554/chamber",)})
    grid(api, [{"camera": "1:Bed"}, {"camera": "2:Camera"}])
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(card.commands) == 1 and len(card.feeders) == 2)
    assert all("-hwaccel" in f and "yuv4mpegpipe" in f for f in card.feeders)
    picture = card.commands[0]
    assert "image2pipe" not in picture and picture.count("rawvideo") == 2  # no JPEGs made, or unpacked
    # each pipe made for the size its camera's frames come at (going live waited for them to say), not Mainsail's shape
    sizes = [picture[i + 1] for i, a in enumerate(picture) if a == "-video_size"]
    assert sizes[0] == "1280x720"

    def status():
        return api.get("/api/stream").json()["status"]

    assert status()["camera_decoding"] == "gpu" and status()["camera_decoding_note"] is None
    assert wait_until(lambda: [x.get("decoding") for x in status()["camera_states"]] == ["gpu", "gpu"])
    # the page's preview of a camera: its latest raw frame, as a JPEG
    r = api.get("/api/cameras/1/Bed/picture")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8" and Image.open(io.BytesIO(r.content)).size == (640, 360)
    assert len(card.commands) == 1  # (nothing to hand over for)
    api.post("/api/stream/stop")
    assert api.get("/api/stream").json()["status"]["camera_decoding"] is None


def test_a_camera_that_comes_up_at_another_shape_than_guessed_has_the_picture_handed_over_to_one_made_for_it(api, card, farmhand, monkeypatch):
    from streamer.live_stream import pictures

    monkeypatch.setattr(feeders, "SHAPE_WAIT", 0.3)
    monkeypatch.setattr(feeders, "RETRY_FIRST", 0.1)
    monkeypatch.setattr(feeders, "RETRY_MOST", 0.2)
    monkeypatch.setattr(pictures, "RESHAPE_SETTLE", 0)
    bed = "http://10.0.6.21/webcam/?action=stream"
    card.down.add(bed)  # down while going live: its pipe made for Mainsail's shape (4:3)
    card.shapes[bed] = (1280, 720)  # but it's 16:9
    grid(api, [{"camera": "1:Bed"}, {"camera": "2:Camera"}])
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(card.commands) == 1)
    first = card.commands[0]
    assert first[first.index("-video_size") + 1] == "960x720"
    card.down.discard(bed)
    assert wait_until(lambda: len(card.commands) == 2, 10)  # handed over, with no break (relay.py)
    second = card.commands[1]
    assert second[second.index("-video_size") + 1] == "1280x720"
    assert len(card.senders) == 1 and len([f for f in card.feeders if bed in f]) >= 2  # (the camera's own retries)
    api.post("/api/stream/stop")


def test_without_a_card_that_works_the_cameras_are_read_on_the_processor_as_before_and_the_page_says_why(api, ffmpeg, farmhand, monkeypatch):
    monkeypatch.delenv(camera_decoding.CAMERAS_SETTING, raising=False)
    camera_decoding._found.clear()
    said = "[Parsed_scale_cuda_0] cu->cuModuleLoadData failed -> CUDA_ERROR_JIT_COMPILER_NOT_FOUND: PTX JIT compiler library not found"
    monkeypatch.setattr(camera_decoding, "_gpu_works", lambda ffmpeg: (False, camera_decoding._why(said)))
    try:
        grid(api, [{"camera": "1:Bed"}, {"camera": "2:Camera"}])
        assert api.post("/api/stream/start").status_code == 200
        assert wait_until(lambda: len(ffmpeg.commands) == 1 and len(ffmpeg.feeders) == 2)
        assert not any("-hwaccel" in f for f in ffmpeg.feeders) and ffmpeg.commands[0].count("image2pipe") == 2
        status = api.get("/api/stream").json()["status"]
        assert status["camera_decoding"] == "cpu" and "libnvidia-ptxjitcompiler.so.1" in status["camera_decoding_note"]
    finally:
        camera_decoding._found.clear()


def test_the_processor_asked_for_is_never_tried_on_the_card(monkeypatch):
    monkeypatch.setenv(camera_decoding.CAMERAS_SETTING, "cpu")
    monkeypatch.setattr(camera_decoding, "_gpu_works", lambda ffmpeg: pytest.fail("the card was tried"))
    assert camera_decoding.decoding("ffmpeg") == ("cpu", None)


def test_why_the_card_did_not_work_is_said_plainly():
    assert "libnvcuvid.so.1" in camera_decoding._why("Cannot load libnvcuvid.so.1")
    assert "no NVIDIA card" in camera_decoding._why("Cannot load libcuda.so.1")
    assert "(Something else)" in camera_decoding._why("Something else")


def test_a_camera_the_card_cannot_unpack_is_read_on_the_processor_the_others_staying_on_the_card(card, monkeypatch):
    monkeypatch.setattr(feeders, "RETRY_FIRST", 0.1)
    card.gpu_trouble.add(BED)
    f = feeders.Feeder("ffmpeg", "1:Bed", ["-i", BED], [], (1280, 720), card, aspect="16:9", decoding="gpu").start()
    g = feeders.Feeder("ffmpeg", "2:Camera", ["-i", "rtsp://other"], [], (1280, 720), card, aspect="16:9", decoding="gpu").start()
    try:
        assert wait_until(lambda: f.state()["state"] == "live" and g.state()["state"] == "live")
        assert f.state()["decoding"] == "cpu" and g.state()["decoding"] == "gpu"
        bed = [a for a in card.feeders if BED in a]
        assert "-hwaccel" in bed[0] and "-hwaccel" not in bed[-1] and "yuv4mpegpipe" in bed[-1]  # (still raw: same pipes)
        assert any("processor" in line for line in f.said)
    finally:
        f.stop()
        g.stop()


def test_a_raw_cameras_down_square_is_a_raw_frame_of_its_pipes_size(card, tmp_path, monkeypatch):
    f = feeders.Feeder("ffmpeg", "1:Bed", [], [], (1280, 720), card, aspect="4:3", decoding="gpu")  # no address: down
    downs = down_square.DownFrames("#112233", "/no/font.ttf", "#FFFFFF", "VT 1190", raw=True)
    pump = pumps.Pump([(f, downs)], ["/p"])
    frame, _ = pump.frame_for(f, downs, None, pump.sizes[0])
    assert f.box == (960, 720) and len(frame) == raw_frames.frame_bytes((960, 720)) == 960 * 720 * 3 // 2
    assert pump.input_args(0) == ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-video_size", "960x720", "-framerate", "15", "-i", "/p"]
    # a frame of another size never goes in its pipe: its "Camera down" square does, and the pump says it's stale
    f._keep(bytearray(raw_frames.frame_bytes((1280, 720))), first=True)
    f.box, f.box_known = (1280, 720), True
    frame, _ = pump.frame_for(f, downs, None, pump.sizes[0])
    assert len(frame) == raw_frames.frame_bytes((960, 720)) and pump.stale()
    # its colour at video levels, and back to the same colour in a preview
    picture = Image.open(io.BytesIO(raw_frames.to_jpeg(frame, (960, 720))))
    assert picture.size == (480, 360)
    r, g, b = picture.convert("RGB").getpixel((5, 5))
    assert abs(r - 0x11) < 6 and abs(g - 0x22) < 6 and abs(b - 0x33) < 6
