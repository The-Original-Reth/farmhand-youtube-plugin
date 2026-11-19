"""
Farmhand's Stream control: while Farmhand says its design takes this plugin's layout over, the stream sends that design
(its pictures, slideshows and music fetched into the plugin's own folder), the plugin's own layout stays saved as it
was, and comes back as soon as Farmhand says it's off (or never granted stream:read). Farmhand starts, stops and asks
the stream through /api/stream-control.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from streamer import store, stream_control, stream_media
from streamer.live_stream import own_settings, settings, stream

from .conftest import KEY, grid, wait_until

pytestmark = pytest.mark.usefixtures("stream_fresh")


def png(color="#FF0000") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def design(revision=1, **changes) -> dict:
    """A design as Farmhand's GET /stream/design gives it (stream_control/design.py's settings())."""
    d = {
        "cols": 2, "rows": 1,
        "cells": [{"camera": "2:Camera"}, {"background": "#1F3A5F", "image": "logo.png", "slideshow": None, "text": "Live"}],
        "cameras": ["2:Camera"], "show_status": True, "layout": "grid", "seconds_all": 30, "seconds_each": 20,
        "resolution": "720p", "border_color": "#000000", "background": "#112233", "font": "play", "color": "#FFFFFF",
        "music": True, "music_volume": 30, "music_shuffle": True, "delay": 0,
        "holding": {"text": "Back soon", "image": None, "background": None, "color": None, "font": None},
        "title": "The print farm, live", "description": "From Stream control", "revision": revision,
        "saved_at": "2026-11-17T18:00:00Z",
    }
    return {**d, **changes}


def take_over(farmhand, **changes):
    farmhand.stream_files = {"pictures": {"logo.png": png()}, "slideshows": {"Prints": {"frog.png": png("#00FF00")}},
                             "music": {"beat.mp3": b"ID3 not really music"}}
    farmhand.slideshow_order = {"Prints": ["blank:1", "frog.png"]}
    farmhand.design = design(**changes)


# ------------------------------------------------------------------ following, and going back


def test_while_stream_control_takes_over_its_design_is_sent_and_the_plugins_own_is_kept(api, farmhand, plugin):
    grid(api, [{"camera": "1:Bed"}, None])
    own = own_settings(plugin)
    assert stream_control.ask() is False and not stream_control.design()  # {"enabled": false}: its own layout

    take_over(farmhand, cells=[{"camera": "2:Camera"}, {"background": "#1F3A5F", "slideshow": "Prints", "text": ""}])
    assert stream_control.ask() is True
    sent = settings(plugin)
    assert sent["cells"][0] == {"camera": "2:Camera"} and sent["cells"][1]["slideshow"] == "Prints"
    assert sent["cameras"] == ["2:Camera"] and sent["title"] == "The print farm, live" and sent["background"] == "#112233"
    assert sent["key_set"] and sent["youtube"] == own["youtube"]  # what only YouTube has stays the plugin's
    assert own_settings(plugin) == own  # the plugin's own layout, untouched

    # its files, fetched into the plugin's own folder, which the stream reads from while following
    cache = stream_control.cache()
    assert stream_media.slideshow_paths(plugin, "Prints") == ["blank:1", cache / "slideshows" / "Prints" / "frog.png"]
    assert [p.name for p in stream_media.track_paths(plugin)] == ["beat.mp3"]
    assert (cache / "music" / "beat.mp3").read_bytes() == b"ID3 not really music"

    page = api.get("/api/stream").json()
    assert page["settings"]["cells"] == own["cells"]  # the page edits the plugin's own
    assert page["stream_control"]["following"] is True and page["stream_control"]["revision"] == 1
    assert api.get("/api/stream-control").json() == {"live": False, "following": True, "error": None, "watch_url": None}

    # switched off in Farmhand (or the plugin unticked): back to its own, straight away
    farmhand.design = None
    assert stream_control.ask() is True
    assert settings(plugin) == own and stream_media.track_paths(plugin) == []
    assert api.get("/api/stream").json()["stream_control"]["following"] is False


def test_saving_on_the_page_while_followed_changes_only_the_plugins_own(api, farmhand, plugin):
    grid(api, [{"camera": "1:Bed"}, None])
    take_over(farmhand)
    stream_control.ask()
    r = api.put("/api/stream", json={"title": "My own title", "border_color": "#00FF00"})
    assert r.status_code == 200, r.text
    assert settings(plugin)["title"] == "The print farm, live" and settings(plugin)["border_color"] == "#000000"
    farmhand.design = None
    stream_control.ask()
    assert settings(plugin)["title"] == "My own title" and settings(plugin)["border_color"] == "#00FF00"


def test_stream_read_not_granted_is_the_plugins_own_layout(api, farmhand, plugin):
    grid(api, [{"camera": "1:Bed"}, None])
    take_over(farmhand)
    stream_control.ask()
    farmhand.permissions.discard("stream:read")
    assert stream_control.ask() is True
    assert stream_control.design() is None and "stream:read" in stream_control.note()
    assert settings(plugin)["cameras"] == ["1:Bed"]


def test_farmhand_not_answering_keeps_what_was_sent(api, farmhand, plugin):
    grid(api, [{"camera": "1:Bed"}, None])
    take_over(farmhand)
    stream_control.ask()
    farmhand.down = True
    assert stream_control.ask() is False
    assert stream_control.design()["revision"] == 1 and "Couldn't ask Farmhand" in stream_control.note()
    assert settings(plugin)["cameras"] == ["2:Camera"]


def test_without_stamps_a_picture_is_fetched_again_with_a_new_revision_and_ones_no_longer_named_go(api, farmhand, plugin):
    take_over(farmhand)
    farmhand.send_stamps = False  # (a Farmhand from before media.stamps)
    stream_control.ask()
    logo = stream_control.cache() / "images" / "logo.png"
    assert logo.read_bytes() == farmhand.stream_files["pictures"]["logo.png"]
    farmhand.stream_files["pictures"]["logo.png"] = png("#0000FF")
    stream_control.ask()  # (the same revision: not fetched again)
    assert logo.read_bytes() != farmhand.stream_files["pictures"]["logo.png"]
    farmhand.design = design(revision=2)
    stream_control.ask()
    assert logo.read_bytes() == farmhand.stream_files["pictures"]["logo.png"]
    farmhand.design = design(revision=3, cells=[{"camera": "2:Camera"}, None])
    stream_control.ask()
    assert not logo.exists()


def downloads(farmhand) -> list[str]:
    return [c for c in farmhand.calls if c.startswith("/plugin-api/v1/stream/") and "/stream/design" not in c]


def logos(n: int) -> tuple[dict, list]:
    """n sponsor logos, each in a square of a 5x5 grid (as many as fit), and those squares."""
    files = {f"sponsor {i}.png": png(f"#{i:02X}{i:02X}{i:02X}") for i in range(n)}
    cells = [{"background": "#000000", "image": name, "slideshow": None, "text": ""} for name in files][:25]
    return files, cells + [None] * (25 - len(cells))


def test_only_files_whose_stamp_changed_are_copied_again(api, farmhand, plugin):
    take_over(farmhand)
    files, cells = logos(20)
    farmhand.stream_files["pictures"].update(files)
    farmhand.design = design(cols=5, rows=5, cells=cells)
    stream_control.ask()
    assert len(downloads(farmhand)) == 20 + 1  # (and the music)
    assert '"images/sponsor 0.png"' in (stream_control.cache() / stream_control.STAMPS_FILE).read_text(encoding="utf-8")

    farmhand.calls.clear()
    farmhand.design = design(revision=2, cols=5, rows=5, cells=cells, title="New words")  # a new revision, the same files
    stream_control.ask()
    assert downloads(farmhand) == []

    farmhand.stream_files["pictures"]["sponsor 3.png"] = png("#FF00FF")  # one replaced in Stream control's folder
    farmhand.design = design(revision=3, cols=5, rows=5, cells=cells)
    stream_control.ask()
    assert downloads(farmhand) == ["/plugin-api/v1/stream/pictures/sponsor%203.png"]
    assert (stream_control.cache() / "images" / "sponsor 3.png").read_bytes() == farmhand.stream_files["pictures"]["sponsor 3.png"]

    farmhand.calls.clear()
    (stream_control.cache() / "images" / "sponsor 4.png").unlink()  # one gone from the copy: copied again
    stream_control.ask()
    assert downloads(farmhand) == ["/plugin-api/v1/stream/pictures/sponsor%204.png"]


def test_farmhand_asking_to_slow_down_is_waited_out(api, farmhand, plugin, monkeypatch):
    waits = []
    monkeypatch.setattr(stream_control, "_sleep", waits.append)
    take_over(farmhand)
    farmhand.files_busy_for = 3
    stream_control.ask()
    assert stream_control.note() is None and waits == [1.0, 1.0, 1.0]
    assert (stream_control.cache() / "images" / "logo.png").is_file()


def test_files_that_cant_be_copied_are_said_in_one_short_line(api, farmhand, plugin, monkeypatch):
    monkeypatch.setattr(stream_control, "_sleep", lambda s: None)
    take_over(farmhand)
    _, cells = logos(12)  # named in the design, but not in Stream control's folder: 404 each
    farmhand.design = design(cols=5, rows=5, cells=cells)
    stream_control.ask()
    said = stream_control.note()
    assert said.startswith("12 of Stream control's files couldn't be copied from Farmhand: sponsor 0.png (")
    assert said.endswith(", sponsor 1.png and 10 more.") and "sponsor 2.png" not in said and len(said.splitlines()) == 1
    assert stream_control.design() is not None  # (followed all the same: those squares show their colour)

    farmhand.files_busy_for = 100  # Farmhand never stops asking: the same short line, after a few tries each
    farmhand.stream_files["pictures"].update(logos(12)[0])
    stream_control.ask()
    assert stream_control.note().startswith("12 of Stream control's files") and len(stream_control.note()) < 300


def test_tracks_from_the_plugins_own_folder_are_copied_from_there_never_fetched_back(api, farmhand, plugin):
    """Stream control's music_from naming this plugin's own folder: Farmhand passes those tracks on from this plugin,
    so fetching them was the plugin downloading its own music back from itself (20 s a track while streaming)."""
    take_over(farmhand)
    farmhand.stream_files["music"].update({"ours.mp3": b"ID3 ours, as Farmhand passes it on", "theirs.mp3": b"ID3 Twitch's"})
    farmhand.music_from = {"ours.mp3": "stream", "theirs.mp3": "twitch"}  # (this plugin's id in Farmhand is "stream")
    own = Path(plugin_media()) / "music"
    own.mkdir(parents=True, exist_ok=True)
    (own / "ours.mp3").write_bytes(b"ID3 ours, in the plugin's own folder")
    stream_control.ask()
    got = downloads(farmhand)
    assert "/plugin-api/v1/stream/music/ours.mp3" not in got
    assert "/plugin-api/v1/stream/music/theirs.mp3" in got and "/plugin-api/v1/stream/music/beat.mp3" in got
    music = stream_control.cache() / "music"
    assert (music / "ours.mp3").read_bytes() == b"ID3 ours, in the plugin's own folder"
    assert (music / "theirs.mp3").read_bytes() == b"ID3 Twitch's"
    assert stream_control.note() is None

    farmhand.calls.clear()
    stream_control.ask()  # all copied, stamps kept: nothing fetched or asked again
    assert downloads(farmhand) == [] and "/plugin-api/v1/me" not in farmhand.calls

    (own / "ours.mp3").unlink()  # gone from the plugin's own folder: fetched from Farmhand after all
    (music / "ours.mp3").unlink()
    stream_control.ask()
    assert downloads(farmhand) == ["/plugin-api/v1/stream/music/ours.mp3"]


def plugin_media() -> str:
    from streamer import config

    return str(config.settings.media_dir)


def design_asks(farmhand) -> int:
    return sum(1 for c in list(farmhand.calls) if c == "/plugin-api/v1/stream/design")


def test_files_copy_in_a_worker_of_their_own_while_farmhand_is_still_asked(api, farmhand, plugin, monkeypatch):
    """A copy round of dozens of tracks took a quarter of an hour, and the design was asked again only after it: a
    "couldn't ask" stayed on the page all that while. Now asking goes on, and the page says how far the copying is."""
    monkeypatch.setattr(stream_control, "EVERY", 0.05)
    take_over(farmhand)
    farmhand.stream_files["music"].update({f"track {i}.mp3": b"ID3 %d" % i for i in range(5)})
    farmhand.file_delay = 0.3
    farmhand.down = True
    stream_control.start()
    try:
        assert wait_until(lambda: "Couldn't ask Farmhand" in (stream_control.note() or ""))
        farmhand.down = False
        assert wait_until(lambda: stream_control.design() is not None)  # (its picture copied: followed, music to come)
        assert wait_until(lambda: "Copying Stream control's music:" in (stream_control.note() or ""))
        said = stream_control.note()
        assert "Couldn't ask" not in said and " of 6 tracks." in said
        assert stream_control.problem() is None  # (copying is no problem: Farmhand's Stream control isn't told it)
        asked = design_asks(farmhand)
        assert wait_until(lambda: design_asks(farmhand) >= asked + 3, timeout=1)  # asked on while it copies
        assert "Copying Stream control's music:" in (stream_control.note() or "")
        assert wait_until(lambda: stream_control.note() is None, timeout=10)
        assert sorted(p.name for p in (stream_control.cache() / "music").iterdir()) == sorted(farmhand.stream_files["music"])
    finally:
        stream_control.stop()


def test_switching_stream_control_off_is_followed_at_once_while_files_copy(api, farmhand, plugin, monkeypatch):
    monkeypatch.setattr(stream_control, "EVERY", 0.05)
    take_over(farmhand)
    farmhand.stream_files["music"].update({f"track {i}.mp3": b"ID3 %d" % i for i in range(20)})
    farmhand.file_delay = 0.3  # (6 s of music)
    stream_control.start()
    try:
        assert wait_until(lambda: "Copying Stream control's music:" in (stream_control.note() or ""))
        farmhand.design = None  # switched off (or this plugin unticked) in Stream control
        assert wait_until(lambda: stream_control.design() is None, timeout=1)
        assert wait_until(lambda: stream_control.note() is None, timeout=2)  # (the copying given up)
    finally:
        stream_control.stop()


# ------------------------------------------------------------------ while live


def test_while_live_the_stream_follows_a_new_revision_and_goes_back_to_its_own(api, ffmpeg, farmhand, plugin):
    grid(api, [{"camera": "1:Bed"}, None])
    assert api.post("/api/stream-control/start").json()["live"] is True
    assert wait_until(lambda: len(ffmpeg.commands) == 1)
    assert stream.status()["cameras"] == ["VT 1190"]

    # Stream control switched on, this plugin ticked: a hand-over to its design (cameras only: the fake ffmpeg takes a
    # picture card's looped picture for the holding screen)
    only_cameras = [{"camera": "2:Camera"}, None]
    take_over(farmhand, cells=only_cameras)
    stream_control.ask()
    assert wait_until(lambda: len(ffmpeg.commands) == 2)
    assert stream.status()["cameras"][0].startswith("Core One") and len(ffmpeg.senders) == 1  # (no break on YouTube)
    assert api.get("/api/stream-control").json()["following"] is True

    stream_control.ask()  # the same revision: nothing changes
    farmhand.design = design(revision=2, cells=only_cameras, title="A new title")  # only the words: no new picture
    stream_control.ask()
    assert len(ffmpeg.commands) == 2
    farmhand.design = design(revision=3, cells=only_cameras, border_color="#FF0000")  # the picture: a new one
    stream_control.ask()
    assert wait_until(lambda: len(ffmpeg.commands) == 3)

    farmhand.design = None  # back to its own layout
    stream_control.ask()
    assert wait_until(lambda: len(ffmpeg.commands) == 4)
    assert stream.status()["cameras"] == ["VT 1190"]
    assert api.post("/api/stream-control/stop").json() == {"live": False, "following": False, "error": None, "watch_url": None}
    assert store.store().get("live_wanted") is None


def test_while_live_pictures_copied_late_start_the_picture_again(api, ffmpeg, farmhand, plugin, monkeypatch):
    """A slideshow whose pictures couldn't all be copied when the design was put on the stream showed its colour until
    the stream was next started: now the picture starts again once they're copied, or one is replaced."""
    monkeypatch.setattr(stream_control, "_sleep", lambda s: None)
    grid(api, [{"camera": "1:Bed"}, None])
    assert api.post("/api/stream-control/start").json()["live"] is True
    assert wait_until(lambda: len(ffmpeg.commands) == 1)

    show = [{"camera": "2:Camera"}, {"background": "#1F3A5F", "image": None, "slideshow": "Prints", "text": ""}]
    take_over(farmhand, cells=show)
    frog = farmhand.stream_files["slideshows"]["Prints"].pop("frog.png")  # not in Stream control's folder yet: 404
    stream_control.ask()
    assert wait_until(lambda: len(ffmpeg.commands) == 2)
    assert stream_control.note().startswith("1 of Stream control's files couldn't be copied")

    stream_control.ask()  # still not there: nothing new, nothing started again
    assert len(ffmpeg.commands) == 2

    farmhand.stream_files["slideshows"]["Prints"]["frog.png"] = frog  # there now: copied, and the picture follows
    stream_control.ask()
    assert wait_until(lambda: len(ffmpeg.commands) == 3) and stream_control.note() is None
    assert len(ffmpeg.senders) == 1  # (a hand-over: no break on YouTube)

    stream_control.ask()  # all copied: nothing more
    assert len(ffmpeg.commands) == 3

    farmhand.stream_files["slideshows"]["Prints"]["frog.png"] = png("#0000FF")  # replaced, with only the words new
    farmhand.design = design(revision=2, cells=show, title="New words")
    stream_control.ask()
    assert wait_until(lambda: len(ffmpeg.commands) == 4)


def test_pictures_copied_while_not_live_start_nothing(api, ffmpeg, farmhand, plugin, monkeypatch):
    monkeypatch.setattr(stream_control, "_sleep", lambda s: None)
    take_over(farmhand, cells=[{"camera": "2:Camera"}, {"background": "#1F3A5F", "image": None, "slideshow": "Prints", "text": ""}])
    frog = farmhand.stream_files["slideshows"]["Prints"].pop("frog.png")
    stream_control.ask()
    farmhand.stream_files["slideshows"]["Prints"]["frog.png"] = frog
    stream_control.ask()
    assert (stream_control.cache() / "slideshows" / "Prints" / "frog.png").is_file()
    assert ffmpeg.commands == [] and stream.status()["live"] is False


# ------------------------------------------------------------------ Farmhand's Go live and Stop


def test_go_live_from_stream_control_says_why_it_cant(api, ffmpeg, farmhand):
    r = api.post("/api/stream-control/start")
    assert r.status_code == 422 and "stream key" in r.json()["detail"]
    assert api.get("/api/stream-control").json()["live"] is False


def test_go_live_from_stream_control_with_its_design(api, ffmpeg, farmhand, plugin):
    api.put("/api/stream", json={"stream_key": KEY})  # a key, and no cameras of its own: the design brings them
    take_over(farmhand, cells=[{"camera": "2:Camera"}, None])
    stream_control.ask()
    r = api.post("/api/stream-control/start")
    assert r.status_code == 200, r.text
    assert r.json() == {"live": True, "following": True, "error": None, "watch_url": None}
    assert wait_until(lambda: len(ffmpeg.commands) == 1)
    assert api.post("/api/stream-control/start").status_code == 200  # live already: carries on
    assert len(ffmpeg.senders) == 1
    assert api.post("/api/stream-control/stop").json()["live"] is False


def test_the_plugins_own_switch_is_set_aside_while_following(api, ffmpeg, farmhand, plugin):
    api.put("/api/stream", json={"stream_key": KEY, "enabled": False})
    take_over(farmhand, cells=[{"camera": "2:Camera"}, None])
    stream_control.ask()
    r = api.post("/api/stream-control/start")  # the switch is off, and hidden while following: Go live still works
    assert r.status_code == 200, r.text
    assert r.json()["live"] is True
    assert wait_until(lambda: len(ffmpeg.commands) == 1)
    farmhand.design = None  # let go: the plugin's own switch again (off: stopped, as turning it off does)
    stream_control.ask()
    assert api.get("/api/stream").json()["settings"]["enabled"] is False
    assert api.get("/api/stream-control").json()["live"] is False


def test_the_manifest_asks_for_stream_read_and_names_its_stream_control(api):
    m = api.get("/manifest").json()
    assert "stream:read" in m["permissions"]
    assert m["stream_control"] == {"path": "/api/stream-control", "site": "YouTube"}
