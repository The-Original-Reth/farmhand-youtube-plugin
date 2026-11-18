"""
Farmhand's Stream control: while Farmhand says its design takes this plugin's layout over, the stream sends that design
(its pictures, slideshows and music fetched into the plugin's own folder), the plugin's own layout stays saved as it
was, and comes back as soon as Farmhand says it's off (or never granted stream:read). Farmhand starts, stops and asks
the stream through /api/stream-control.
"""
from __future__ import annotations

import io

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


def test_a_new_picture_is_fetched_again_with_a_new_revision_and_ones_no_longer_named_go(api, farmhand, plugin):
    take_over(farmhand)
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


def test_the_manifest_asks_for_stream_read_and_names_its_stream_control(api):
    m = api.get("/manifest").json()
    assert "stream:read" in m["permissions"]
    assert m["stream_control"] == {"path": "/api/stream-control", "site": "YouTube"}
