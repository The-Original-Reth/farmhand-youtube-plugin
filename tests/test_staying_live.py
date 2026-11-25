"""
Staying live: going live afresh never carries on with the broadcast it is ending; a new broadcast is seen to go live;
a sender whose connection stopped taking the video is started again; a save while the sender connects again is a
hand-over; Go live and Stop one at a time (Stop wins); a Go live that can't put a picture out is undone; a change to the
picture and the YouTube settings at once does both; the YouTube watcher outlives an odd answer; and a sender that can't
start stops everything.
"""
from __future__ import annotations

import threading
import time

import pytest

from streamer import store, youtube_live
from streamer.live_stream import broadcast, layout_picture, runner, self_check, stream
from streamer.live_stream.buffer import Buffer
from streamer.live_stream.picture import sender_command

from .conftest import KEY, grid, wait_until

pytestmark = pytest.mark.usefixtures("stream_fresh")

CHOSEN = {"youtube": {"privacy": "public"}, "title": "Printing", "description": ""}


@pytest.fixture(autouse=True)
def _nothing_being_ended():
    """No broadcast ended by an earlier test counts as being ended in this one."""
    with broadcast._ending_lock:
        broadcast._ending.clear()
        broadcast._ended_ids.clear()
    yield


# ------------------------------------------------------------------ 1: going live afresh, a broadcast being ended


def test_going_live_afresh_never_carries_on_with_the_broadcast_it_is_ending(monkeypatch):
    """Go live while live (2026-11-24): the broadcast before is ended in its own thread, and YouTube still lists it as
    live for a moment; it's waited for and never carried on with, so the new time live gets a new broadcast."""
    ending = threading.Event()
    ended = []

    def end_broadcast(creds, bid):
        ending.wait(5)  # (YouTube slow to answer)
        ended.append(bid)

    def api(method, path, token, params, body=None):
        if method == "GET" and path == "/liveBroadcasts":  # the old one still listed as live: it isn't ended yet
            return {"items": [{"id": "old", "contentDetails": {"boundStreamId": "own"}}] if params["broadcastStatus"] == "active" else []}
        if method == "POST" and path == "/liveBroadcasts":
            return {"id": "new"}
        return {}

    monkeypatch.setattr(youtube_live, "_access_token", lambda creds: "token")
    monkeypatch.setattr(youtube_live, "_api", api)
    monkeypatch.setattr(youtube_live, "end_broadcast", end_broadcast)

    old = broadcast.Broadcast({}, "own", youtube_live._links("old"))
    old.end()
    threading.Timer(0.2, ending.set).start()
    new = broadcast.Broadcast.start({}, {"id": "own"}, CHOSEN)
    assert ended == ["old"]  # (waited for)
    assert new.id == "new" and not new.carried_on
    assert new.made_message(CHOSEN)["message"].startswith("Broadcast made on YouTube")


def test_the_page_says_when_the_broadcast_still_live_was_carried_on_with(monkeypatch):
    monkeypatch.setattr(youtube_live, "_access_token", lambda creds: "token")
    monkeypatch.setattr(youtube_live, "_api", lambda method, path, token, params, body=None: {
        "items": [{"id": "J4aj", "contentDetails": {"boundStreamId": "own"}}] if params.get("broadcastStatus") == "active" else []})
    b = broadcast.Broadcast.start({}, {"id": "own"}, CHOSEN)
    assert b.carried_on and "Carried on" in b.made_message(CHOSEN)["message"]


# ------------------------------------------------------------------ 2 and 7: the broadcast seen to go live, the watcher kept


def _watch(monkeypatch, answers, auto_start=True, rounds=None):
    """broadcast.watch run against YouTube answering `answers` in turn (an Exception is raised), for as many rounds:
    (what was said, the senders started again, the transitions to live asked for, the Broadcast)."""
    asked, started, said, restarted = [], [], [], []

    def health(creds, stream_id, bid):
        got = answers[min(len(asked), len(answers) - 1)]
        asked.append(bid)
        if isinstance(got, Exception):
            raise got
        return {"state": None, "health": None, "stream_status": None, "issues": [], "viewers": None, **got}

    monkeypatch.setattr(youtube_live, "health", health)
    monkeypatch.setattr(youtube_live, "go_live", lambda creds, bid: started.append(bid))
    monkeypatch.setattr(youtube_live, "start_broadcast", lambda *a: youtube_live._links("again"))
    rounds = rounds or len(answers)
    b = broadcast.Broadcast({}, "own", youtube_live._links("b1"))
    b.watch(0, lambda seconds: len(asked) < rounds, lambda: {**CHOSEN, "youtube": {"auto_start": auto_start}},
            said.append, restarted.append)
    return said, restarted, started, b


def test_a_broadcast_waiting_ready_while_youtube_gets_the_video_is_started(monkeypatch):
    """A broadcast made again while the video was arriving already never gets its auto-start: Farmhand starts it."""
    said, _, started, _ = _watch(monkeypatch, [{"state": "complete"}, {"state": "ready", "stream_status": "active", "health": "good"}])
    assert started == ["again"]
    assert any("made a new one" in s["message"] for s in said) and "Farmhand started it" in said[-1]["message"]


def test_a_broadcast_started_by_hand_in_studio_is_left_waiting(monkeypatch):
    _, _, started, _ = _watch(monkeypatch, [{"state": "ready", "stream_status": "active", "health": "good"}] * 2, auto_start=False)
    assert started == []


def test_youtube_getting_no_video_for_a_while_starts_the_connection_again(monkeypatch):
    monkeypatch.setattr(broadcast, "NO_DATA_FOR", 0)
    _, restarted, _, _ = _watch(monkeypatch, [{"state": "ready", "stream_status": "inactive", "health": "noData"}] * 2)
    assert len(restarted) == 1 and "no video" in restarted[0]


def test_an_odd_answer_from_youtube_never_stops_the_watching(monkeypatch):
    """Something unexpected asking YouTube (a KeyError reading its answer) is said on the page, and asked again."""
    said, _, _, b = _watch(monkeypatch, [KeyError("access_token"), {"state": "live", "health": "good"}], rounds=1)
    assert "unexpected" in b.status()["issues"][0]
    said, _, _, b = _watch(monkeypatch, [KeyError("access_token"), {"state": "live", "health": "good"}])
    assert b.status()["state"] == "live" and b.status()["issues"] == []


def test_the_sender_gives_up_on_a_connection_that_stopped_taking_the_video():
    args = sender_command("ffmpeg", "rtmps://example/live2/k")
    assert args[-1] == "rtmps://example/live2/k" and "-rw_timeout" in args
    assert int(args[args.index("-rw_timeout") + 1]) > 0


def test_the_buffer_tells_a_sender_that_takes_nothing():
    """A sender that still runs but takes nothing (its connection stopped taking the video): what's held is trimmed, so
    how much is held never shows it, but the buffer does."""
    release = threading.Event()

    class Stuck:
        def write(self, data):
            release.wait(5)

        def flush(self):
            pass

    b = Buffer(0)
    b.set_sink(Stuck())
    assert not b.sender_stuck(0.1)
    b.write(b"G" + bytes(187))
    assert wait_until(lambda: b.sender_stuck(0.1), timeout=3)
    release.set()
    assert wait_until(lambda: not b.sender_stuck(0.1), timeout=3)
    b.close()


def test_the_self_check_starts_a_stuck_sender_again(api, ffmpeg, farmhand, monkeypatch):
    monkeypatch.setattr(self_check, "CHECK_EVERY", 0.05)
    grid(api, [{"camera": "1:Bed"}, None])
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(ffmpeg.sender_procs) == 1 and stream._sender is not None)
    buffer = stream._buffer
    monkeypatch.setattr(buffer, "sender_stuck", lambda seconds: True)
    assert not stream.status()["on_air"]
    assert wait_until(lambda: ffmpeg.sender_procs[0].poll() is not None)
    assert "connection to YouTube was started again" in stream.status()["self_check"]["message"]


# ------------------------------------------------------------------ 3: a save while the sender connects again


def test_a_save_while_the_sender_connects_again_is_a_hand_over(api, ffmpeg, farmhand):
    grid(api, [{"camera": "1:Bed"}, None])
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(ffmpeg.sender_procs) == 1 and len(ffmpeg.commands) == 1)
    gen, buffer = stream._generation, stream._buffer
    ffmpeg.sender_procs[0].terminate()  # the connection to YouTube dropped: connected again in RETRY_SECONDS
    assert wait_until(lambda: "dropped" in (stream.status()["error"] or ""))
    grid(api, [{"camera": "1:Bed"}, {"camera": "2:Camera"}])
    assert wait_until(lambda: len(ffmpeg.commands) == 2)
    assert stream._generation == gen and stream._buffer is buffer  # not started afresh
    assert len(ffmpeg.sender_procs) == 1


# ------------------------------------------------------------------ 4: Go live and Stop one at a time


def _held_prepare(monkeypatch):
    """layout_picture.prepare held until the event returned is set (going live takes a while)."""
    going, real = threading.Event(), layout_picture.prepare
    reached = threading.Event()

    def prepare(*a, **k):
        reached.set()
        going.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(layout_picture, "prepare", prepare)
    return going, reached


def test_stop_pressed_while_going_live_wins(api, ffmpeg, farmhand, plugin, monkeypatch):
    grid(api, [{"camera": "1:Bed"}, None])
    going, reached = _held_prepare(monkeypatch)
    answers = {}
    t = threading.Thread(target=lambda: answers.update(start=api.post("/api/stream/start")))
    t.start()
    assert reached.wait(5)
    s = threading.Thread(target=lambda: answers.update(stop=api.post("/api/stream/stop")))
    s.start()
    time.sleep(0.2)
    going.set()
    t.join(10)
    s.join(10)
    assert answers["start"].status_code == 422 and "Stop was pressed" in answers["start"].text
    assert not stream.status()["live"] and plugin.get(runner.LIVE_KEY) is None
    assert all(p.poll() is not None for p in ffmpeg.sender_procs + ffmpeg.feeder_procs)


def test_two_go_lives_at_once_start_one_time_live(api, ffmpeg, farmhand, monkeypatch):
    grid(api, [{"camera": "1:Bed"}, None])
    going, reached = _held_prepare(monkeypatch)
    started = []
    real = stream._start_session
    monkeypatch.setattr(stream, "_start_session", lambda *a, **k: (started.append(1), real(*a, **k)))
    threads = [threading.Thread(target=lambda: api.post("/api/stream/start")) for _ in range(2)]
    for t in threads:
        t.start()
    assert reached.wait(5)
    time.sleep(0.2)
    going.set()
    for t in threads:
        t.join(10)
    assert len(started) == 1 and stream.status()["live"]  # (the second was a hand-over)


# ------------------------------------------------------------------ 5: a Go live that can't put a picture out


def test_a_go_live_whose_picture_cant_be_made_is_undone(api, ffmpeg, farmhand, plugin, monkeypatch):
    grid(api, [{"camera": "1:Bed"}, None])

    def prepare(*a, **k):
        raise ValueError("A camera can't be streamed.")

    monkeypatch.setattr(layout_picture, "prepare", prepare)
    r = api.post("/api/stream/start")
    assert r.status_code == 422
    assert not stream.status()["live"] and plugin.get(runner.LIVE_KEY) is None
    assert stream._broadcast is None and stream._buffer is None
    assert all(p.poll() is not None for p in ffmpeg.sender_procs + ffmpeg.feeder_procs)


def test_going_live_again_after_a_restart_keeps_trying_when_the_picture_cant_be_made(api, ffmpeg, farmhand, plugin, monkeypatch):
    grid(api, [{"camera": "1:Bed"}, None])
    plugin.put(runner.LIVE_KEY, "1")
    calls = []

    def prepare(*a, **k):
        calls.append(1)
        raise ValueError("A camera can't be streamed.")

    monkeypatch.setattr(layout_picture, "prepare", prepare)
    with pytest.raises(ValueError):
        stream.go_live(store.store(), store.session_factory, resumed=True)
    assert not stream.status()["live"] and plugin.get(runner.LIVE_KEY) == "1"  # (resume() tries again)


# ------------------------------------------------------------------ 6: the picture and the YouTube settings at once


def test_a_save_changing_the_picture_and_the_title_does_both(api, ffmpeg, farmhand, monkeypatch):
    grid(api, [{"camera": "1:Bed"}, None])
    assert api.post("/api/stream/start").status_code == 200
    assert wait_until(lambda: len(ffmpeg.commands) == 1)
    sent = []
    monkeypatch.setattr(stream, "send_to_youtube", lambda db, chosen, text_changed=True, asked=False: sent.append(chosen["title"]))
    r = api.put("/api/stream", json={"cells": [{"camera": "1:Bed"}, {"camera": "2:Camera"}], "title": "Night shift"})
    assert r.status_code == 200
    assert wait_until(lambda: len(ffmpeg.commands) == 2)  # the picture handed over
    assert sent == ["Night shift"]  # and the title set


# ------------------------------------------------------------------ 8: a sender that can't start


def test_a_sender_that_cant_start_stops_everything(api, ffmpeg, farmhand, plugin, monkeypatch):
    real = ffmpeg.__call__

    def popen(args, feed=False, out=False):
        if args[-1] == f"rtmps://a.rtmps.youtube.com/live2/{KEY}" and "-filter_complex" not in args:
            raise OSError(2, "No such file or directory")
        return real(args, feed=feed, out=out)

    from streamer.live_stream import procs

    monkeypatch.setattr(procs, "popen", popen)
    grid(api, [{"camera": "1:Bed"}, None])
    api.post("/api/stream/start")
    assert wait_until(lambda: not stream.status()["live"] and stream.status()["error"])
    assert "couldn't be started" in stream.status()["error"]
    assert plugin.get(runner.LIVE_KEY) is None and stream._buffer is None and stream._player is None
    assert wait_until(lambda: all(p.poll() is not None for p in ffmpeg.procs + ffmpeg.feeder_procs))
    stream._error = None  # (the one stream: its message would still be there for the next test)
