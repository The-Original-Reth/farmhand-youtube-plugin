"""
The YouTube side of a time live, when Farmhand is signed in to the channel: Farmhand's own stream there (where the
video goes, its key from YouTube) and the broadcast on it (made on Go live from the YouTube settings, changed when they
change, watched while live, made again if YouTube ends it, ended on Stop). The runner (runner.py) holds one Broadcast
per time live; with a pasted key there is none, and YouTube makes the broadcast itself.
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Callable

from ..store import Store

from .. import app_settings, youtube_live

YOUTUBE_EVERY = 120  # seconds between asking YouTube about the broadcast (its health, viewers): ~2,200 of the 10,000 units a day
YOUTUBE_FIRST = 20  # seconds after the delay has passed (the video reaching YouTube) before asking it the first time
YOUTUBE_SOON = 30  # seconds between asking while the broadcast isn't live yet (just made again, or waiting "ready")
NO_DATA_FOR = 90  # seconds YouTube may say it gets no video from Farmhand before the connection to it is started again
START_TRIES = 4  # times a broadcast waiting "ready" with the video arriving is started by Farmhand (then left to Studio)
ENDED = ("complete", "revoked", "gone")  # a broadcast YouTube has ended (auto-stop after a long drop, or ended in Studio)
WAITING = ("created", "ready")  # a broadcast made but not live yet
NOTHING_YET = {"state": None, "health": None, "stream_status": None, "issues": [], "viewers": None}
END_WAIT = 20  # seconds going live afresh waits for the broadcast just stopped to be ended on YouTube

# Broadcasts Farmhand has ended (or is ending) this run: never carried on with. Going live afresh ends the broadcast
# before in a thread of its own; straight after, YouTube still lists it as live, and carrying on with it (as after the
# plugin's own restart) left the new time live on a broadcast that YouTube then completed (2026-11-24's outage).
_ending_lock = threading.Lock()
_ending: dict[str, threading.Event] = {}  # its id -> set once YouTube has been told to end it
_ended_ids: collections.deque[str] = collections.deque(maxlen=50)


def _being_ended() -> set[str]:
    with _ending_lock:
        return set(_ending) | set(_ended_ids)


def _wait_for_ends(seconds: float) -> None:
    """Wait (up to `seconds` in all) for the broadcasts being ended to be ended on YouTube."""
    until = time.monotonic() + seconds
    with _ending_lock:
        events = list(_ending.values())
    for e in events:
        e.wait(max(0.0, until - time.monotonic()))


def _problem(e: Exception) -> str:
    """What went wrong asking YouTube, in words: its own message, or (something unexpected in its answer) what kind."""
    return str(e) if isinstance(e, youtube_live.YouTubeError) else f"an unexpected answer ({type(e).__name__}: {e})"


def own_stream(db: Store, creds: dict) -> dict:
    """Farmhand's own stream on the signed-in channel, {id, key, url}, made the first time (its id kept, so the same one
    is used every time). ValueError when YouTube won't give it."""
    kept = app_settings.get(db, youtube_live.STREAM_KEY)
    try:
        own = youtube_live.own_stream(creds, kept)
    except youtube_live.YouTubeError as e:
        raise ValueError(f"YouTube wouldn't give Farmhand its stream: {e}") from None
    if kept != own["id"]:
        app_settings.put(db, youtube_live.STREAM_KEY, own["id"])
        db.commit()
    return own


def title_on_active(db: Store, chosen: dict) -> dict:
    """Live on a pasted key (YouTube made the broadcast): the chosen title and description set on the channel's active
    broadcast, {state, message}. ValueError when it can't be tried (not signed in, or nothing to set)."""
    creds = youtube_live.credentials(db)
    if not creds:
        raise ValueError("Sign in to YouTube first.")
    if not (chosen["title"] or chosen["description"]):
        raise ValueError("Write a title or description first, and save it.")
    try:
        done = youtube_live.set_broadcast_text(creds, chosen["title"], chosen["description"])
        return {"state": "done", "message": f"Title and description set on “{done['title']}”."}
    except youtube_live.YouTubeError as e:
        return {"state": "failed", "message": f"The YouTube title wasn't set: {e}"}


def _title_of(chosen: dict) -> str:
    return (chosen["title"] or "Live")[:100]


class Broadcast:
    """This time live's broadcast on YouTube: its links, and what YouTube last said of it. Its own lock guards both, as
    the broadcast may be made again (a new id and links) while the page reads it."""

    def __init__(self, creds: dict, stream_id: str, made: dict):
        self._lock = threading.Lock()
        self.creds, self.stream_id = creds, stream_id
        self._made = made  # {id, watch_url, studio_url}
        self._health: dict | None = None
        self.ended = False  # Stop: nothing more is asked of YouTube, and none is made again
        self.carried_on = False  # it was already live on the stream (after the plugin's own restart), not made now

    @classmethod
    def start(cls, creds: dict, own: dict, chosen: dict) -> "Broadcast":
        """The broadcast on Farmhand's own stream `own`: the one still live on it (going live again after the plugin's
        own restart carries on with it), else a new one set from the chosen settings. One Farmhand has ended (or is
        ending: going live afresh ends the one before) is never carried on with; going live afresh first waits a little
        for it to be ended, as a new broadcast waits "ready" for as long as another is live on the stream. ValueError
        says why not."""
        _wait_for_ends(END_WAIT)
        try:
            made = youtube_live.live_on_stream(creds, own["id"], skip=_being_ended())
            carried_on = made is not None
            if made is None:
                made = youtube_live.start_broadcast(creds, own["id"], chosen["youtube"], chosen["title"], chosen["description"])
        except Exception as e:  # noqa: BLE001 - YouTube's refusal, or an answer it couldn't be read from
            raise ValueError(f"YouTube wouldn't make the broadcast: {_problem(e)}") from None
        b = cls(creds, own["id"], made)
        b.carried_on = carried_on
        return b

    def made_message(self, chosen: dict) -> dict:
        """What going live did on YouTube, for the page: made a new broadcast, or carried on with the one live."""
        if self.carried_on:
            return {"state": "done", "message": "Carried on with the broadcast that was still live on YouTube."}
        return {"state": "done", "message": f"Broadcast made on YouTube ({chosen['youtube']['privacy']}): “{_title_of(chosen)}”."}

    @property
    def id(self) -> str:
        with self._lock:
            return self._made["id"]

    def status(self) -> dict:
        """Its links and what YouTube last said of it: {watch_url, studio_url, state, health, issues, viewers}."""
        with self._lock:
            return {"watch_url": self._made["watch_url"], "studio_url": self._made["studio_url"], **(self._health or NOTHING_YET)}

    def update(self, chosen: dict) -> dict:
        """The video's settings changed while live: changed on the broadcast going out straight away. {state, message}."""
        try:
            youtube_live.update_broadcast(self.creds, self.id, chosen["youtube"], chosen["title"], chosen["description"])
            return {"state": "done", "message": "The broadcast on YouTube has the new title, description, tags and privacy."}
        except youtube_live.YouTubeError as e:
            return {"state": "failed", "message": f"The broadcast on YouTube wasn't changed: {e}"}

    def watch(self, delay: float, wait: Callable[[float], bool], read_settings: Callable[[], dict],
              said: Callable[[dict], None], restart_sender: Callable[[str], None] | None = None) -> None:
        """For as long as `wait(seconds)` says this time live goes on, page open or not: once the delay has passed and
        YouTube has had the video a few seconds (before that it has nothing to say), ask YouTube about the broadcast
        every YOUTUBE_EVERY seconds. One YouTube has ended while Farmhand is still sending (its auto-stop after a long
        drop, or ended in Studio) is replaced by a new one on the same stream, from the saved settings (`read_settings`),
        so a stream left running day and night never ends up with nowhere to show; `said` gets what came of it.

        A broadcast that isn't live yet (just made again, or left "ready") is asked about every YOUTUBE_SOON seconds:
        while YouTube gets the video it's started (its auto-start only starts it when the video arrives, and the video
        was arriving already); while YouTube says it gets no video for NO_DATA_FOR seconds, live or not,
        `restart_sender(why)` starts the connection to YouTube again. Something unexpected asking YouTube is said on the
        page, and asked again next time: the watching never stops while live."""
        if not wait(delay + YOUTUBE_FIRST):
            return
        no_data_since: float | None = None
        waiting = 0  # times running it was found waiting to start ("ready") while starting by itself
        while not self.ended:
            next_ask = YOUTUBE_EVERY
            try:
                got = youtube_live.health(self.creds, self.stream_id, self.id)
            except Exception as e:  # noqa: BLE001 - said on the page; asked again next time
                got = {**NOTHING_YET, "issues": [f"Couldn't ask YouTube: {_problem(e)}"]}
            with self._lock:
                self._health = got
            state = got.get("state")
            if state in ENDED and not self.ended:
                outcome = self._make_again(read_settings)
                said(outcome)
                no_data_since, waiting = None, 0
                if outcome["state"] == "done":
                    next_ask = YOUTUBE_SOON  # (the new one is seen to go live)
            elif state in WAITING and not self.ended and self._starts_by_itself(read_settings):
                waiting += 1
                if got.get("stream_status") == "active" and waiting <= START_TRIES:
                    said(self._go_live_now())
                if waiting <= START_TRIES:
                    next_ask = YOUTUBE_SOON
            else:
                waiting = 0
            if got.get("health") == "noData" and state not in ENDED:
                now = time.monotonic()
                if no_data_since is None:
                    no_data_since = now
                    next_ask = min(next_ask, NO_DATA_FOR)  # (asked again once it's been that long, to be sure)
                elif now - no_data_since >= NO_DATA_FOR - 1 and restart_sender and not self.ended:
                    no_data_since = None
                    restart_sender(f"YouTube was getting no video from Farmhand for {NO_DATA_FOR} seconds")
                    next_ask = min(next_ask, YOUTUBE_SOON)
            elif got.get("health") is not None:
                no_data_since = None
            if not wait(next_ask):
                return

    @staticmethod
    def _starts_by_itself(read_settings: Callable[[], dict]) -> bool:
        """The YouTube settings say the broadcast starts when the video arrives (not started by hand in Studio)."""
        try:
            return read_settings()["youtube"].get("auto_start", True) is not False
        except Exception:  # noqa: BLE001 - as YouTube's own default
            return True

    def _go_live_now(self) -> dict:
        """YouTube gets the video, but the broadcast waits "ready" (made while the video was arriving already, so its
        auto-start never came): started now."""
        try:
            youtube_live.go_live(self.creds, self.id)
        except Exception as e:  # noqa: BLE001 - tried again next time
            return {"state": "failed", "message": f"The broadcast on YouTube is waiting to start and wouldn't be started ({_problem(e)}); trying again shortly."}
        return {"state": "done", "message": "The broadcast on YouTube was waiting to start though it had the video, so Farmhand started it."}

    def _make_again(self, read_settings: Callable[[], dict]) -> dict:
        try:
            chosen = read_settings()
            made = youtube_live.start_broadcast(self.creds, self.stream_id, chosen["youtube"], chosen["title"], chosen["description"])
        except Exception as e:  # noqa: BLE001 - said on the page, and tried again
            return {"state": "failed", "message": f"YouTube ended the broadcast and wouldn't make a new one ({_problem(e)}); trying again in {YOUTUBE_EVERY // 60} minutes."}
        with self._lock:
            self._made, self._health = made, None
            stopped = self.ended
        if stopped:  # (stopped meanwhile: the new one isn't needed)
            self.end()
        return {"state": "done", "message": f"YouTube had ended the broadcast, so Farmhand made a new one: “{_title_of(chosen)}”."}

    def end(self) -> None:
        """Stop: ended on YouTube in a thread of its own (stopping never waits on YouTube); it becomes the channel's saved
        video, or is deleted if it never went live."""
        self.ended = True
        bid = self.id
        with _ending_lock:
            _ending.setdefault(bid, threading.Event())
        threading.Thread(target=self._end_quietly, args=(bid,), name="stream-youtube-end", daemon=True).start()

    def _end_quietly(self, broadcast_id: str) -> None:
        try:
            youtube_live.end_broadcast(self.creds, broadcast_id)
        except Exception:  # noqa: BLE001 - it ends by itself when the stream stops, auto-stop being on
            pass
        finally:
            with _ending_lock:
                _ended_ids.append(broadcast_id)
                done = _ending.pop(broadcast_id, None)
            if done:
                done.set()
