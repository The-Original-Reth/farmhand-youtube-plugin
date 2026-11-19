"""
The NVIDIA card tried again when the picture fails, rather than trusted from Go live: one that won't open another
session to compress the stream leaves it to the processor (the page says why), one that takes it again is used again;
and a camera that left the card tries it again when it's next read.
"""
from __future__ import annotations

import pytest

from streamer.live_stream import camera_decoding, encoders, feeders, pictures

from .conftest import grid, wait_until

pytestmark = pytest.mark.usefixtures("stream_fresh")

BED = "http://10.0.6.21/webcam/?action=stream"
REFUSED = "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory (10): (no details)"


@pytest.fixture()
def card(monkeypatch, ffmpeg):
    """An NVIDIA card whose encoder works until `card.works` says otherwise (nothing real is tried)."""
    monkeypatch.delenv(encoders.ENCODER_SETTING, raising=False)
    monkeypatch.setattr(pictures, "RETRY_SECONDS", 0.1)
    encoders._found_encoder.clear()
    state = {"works": True}
    monkeypatch.setattr(encoders, "_nvenc_works", lambda ffmpeg: state["works"])
    ffmpeg.card = state
    yield ffmpeg
    encoders._found_encoder.clear()


def _codec(args):
    """How the picture is compressed (its last -c:v: the ones before are its inputs')."""
    return args[len(args) - 1 - args[::-1].index("-c:v") + 1]


def test_a_card_that_wont_open_a_session_leaves_the_stream_to_the_processor_and_takes_it_again_once_it_will(api, card, farmhand):
    card.lines = [REFUSED]  # (what a picture says when the card has no session free)
    grid(api, [{"camera": "1:Bed"}, {"camera": "2:Camera"}])
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(card.commands) == 1)
    assert _codec(card.commands[0]) == "h264_nvenc"

    def status():
        return api.get("/api/stream").json()["status"]

    assert status()["encoder"] == "nvenc" and status()["encoder_note"] is None

    # the card stops taking it: the picture fails, the card is tried again, and the next picture is the processor's
    card.card["works"] = False
    camera_decoding._found["/usr/bin/ffmpeg"] = ("gpu", None)
    card.procs[0].terminate()
    assert wait_until(lambda: len(card.commands) == 2, 5)
    assert _codec(card.commands[1]) == "libx264"
    assert status()["encoder"] == "cpu" and "no session free" in status()["encoder_note"]
    assert "/usr/bin/ffmpeg" not in camera_decoding._found  # (the cameras' card is tried again at the next Go live too)

    # it takes it again: the next time the picture is started, it's back on the card, and the note goes
    card.card["works"] = True
    card.procs[1].terminate()
    assert wait_until(lambda: len(card.commands) == 3, 5)
    assert _codec(card.commands[2]) == "h264_nvenc"
    assert status()["encoder"] == "nvenc" and status()["encoder_note"] is None
    assert len(card.senders) == 1  # (the connection to YouTube held throughout)
    api.post("/api/stream/stop")


def test_the_processor_asked_for_is_kept_even_when_a_picture_fails(api, ffmpeg, farmhand, monkeypatch):
    monkeypatch.setattr(pictures, "RETRY_SECONDS", 0.1)
    monkeypatch.setattr(encoders, "_nvenc_works", lambda ffmpeg: pytest.fail("the card was tried"))
    grid(api, [{"camera": "1:Bed"}], cols=1)
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(ffmpeg.commands) == 1)
    ffmpeg.procs[0].terminate()
    assert wait_until(lambda: len(ffmpeg.commands) == 2, 5)
    assert _codec(ffmpeg.commands[1]) == "libx264"
    api.post("/api/stream/stop")


def test_a_camera_that_left_the_card_tries_it_again_when_its_read_again(ffmpeg, monkeypatch):
    monkeypatch.setattr(feeders, "RETRY_FIRST", 0.1)
    monkeypatch.setattr(feeders, "RETRY_MOST", 0.2)
    monkeypatch.setattr(feeders, "DOWN_AFTER", 0.3)
    ffmpeg.gpu_trouble.add(BED)
    f = feeders.Feeder("ffmpeg", "1:Bed", ["-i", BED], [], (1280, 720), ffmpeg, aspect="16:9", decoding="gpu").start()
    try:
        assert wait_until(lambda: f.state()["state"] == "live" and f.state()["decoding"] == "cpu")
        ffmpeg.gpu_trouble.discard(BED)  # the card can unpack it again
        ffmpeg.down.add(BED)  # its reading ends (the camera dropped for a moment)
        assert wait_until(lambda: f.state()["state"] == "down")
        ffmpeg.down.discard(BED)
        assert wait_until(lambda: f.state()["state"] == "live" and f.state()["decoding"] == "gpu")
        assert "-hwaccel" in [a for a in ffmpeg.feeders if BED in a][-1]
    finally:
        f.stop()
