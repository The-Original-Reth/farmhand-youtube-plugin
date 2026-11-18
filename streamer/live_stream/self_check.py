"""
The live stream's self-check: a stream left running day and night mends itself when next to no video has come
through for a while. Part of runner.Stream.
"""
from __future__ import annotations

import collections
import time

from datetime import datetime, timezone

from . import procs
from .relay import Relay

# The self-check: while live, how much video the picture has passed on is looked at every CHECK_EVERY seconds. Less than
# ENOUGH of STALL seconds' worth in the last STALL seconds means YouTube is (or soon will be, once the delay runs out)
# getting next to nothing: the picture stopped giving video, or gives it far slower than it plays. The picture is then
# started again, and if that doesn't help within another STALL, the connection to YouTube too.
CHECK_EVERY = 5
STALL = 60
ENOUGH = 0.5
# A stall shorter than that still uses up part of the delay, and it never comes back by itself (the sender plays on at
# real time from what's left), so the next hiccup would be hidden less well. Once the delay has been TOP_UP_SHORT
# seconds or more short of what it's set to for TOP_UP_AFTER seconds, what's missing is collected again behind the
# holding screen (YouTube is shown it for about those seconds), at most once every TOP_UP_EVERY seconds.
TOP_UP_SHORT = 10
TOP_UP_AFTER = 60
TOP_UP_EVERY = 600


class SelfCheckMixin:
    """The self-check (state on runner.Stream)."""

    def _watch_flow(self, gen: int, relay: Relay) -> None:
        """The self-check, for as long as this time live lasts (see CHECK_EVERY): a stream left running day and night
        mends itself when next to no video has come through for STALL seconds, first by starting the picture again (its
        cameras' pipes opened afresh), then, if that didn't help, the connection to YouTube as well."""
        seen = collections.deque()  # (when, the time of the last frame passed on then)
        tried_picture = False
        short_since: float | None = None  # since when the delay has been TOP_UP_SHORT or more short
        topped_up: float | None = None  # when it was last topped up
        while self._wait(gen, CHECK_EVERY):
            now = time.monotonic()
            short = self._delay_short(gen)
            if short is None or short < TOP_UP_SHORT:
                short_since = None
            elif short_since is None:
                short_since = now
            elif now - short_since >= TOP_UP_AFTER and (topped_up is None or now - topped_up >= TOP_UP_EVERY):
                short_since, missing = None, self._top_up(gen)
                if missing is not None:
                    topped_up = now
                    with self._lock:
                        buffer = self._buffer if self._current(gen) else None
                        if buffer is not None:
                            self._healed = {"at": datetime.now(timezone.utc).isoformat(), "message": (
                                f"A short stall had left {buffer.delay - missing:.0f} of the {buffer.delay:.0f} s delay held, so "
                                f"the holding screen was shown for about {missing:.0f} s while the rest was collected again.")}
            seen.append((now, relay.last_time()))
            while seen and now - seen[0][0] > STALL + CHECK_EVERY / 2:
                seen.popleft()
            then, was = seen[0]
            if now - then < STALL - CHECK_EVERY / 2:
                continue  # (not a whole STALL looked at yet)
            last = seen[-1][1]
            came = (last - was) if last is not None and was is not None else (0.0 if last is None else STALL)
            if came >= ENOUGH * STALL:
                tried_picture = False
                continue
            seen.clear()  # (a whole STALL again before it's judged afresh)
            with self._lock:
                if not self._current(gen):
                    return
                picture, warming = self._proc, self._warming
                sender = self._sender if tried_picture else None
                what = ("the picture and the connection to YouTube were started again" if sender
                        else "the picture was started again")
                self._healed = {"at": datetime.now(timezone.utc).isoformat(),
                                "message": f"Next to nothing was reaching YouTube for {STALL} seconds, so {what}."}
            for proc in (warming, picture, sender):
                procs.end(proc)  # (each is started again by its own loop)
            tried_picture = True
            # the stall used the delay up: it's collected afresh behind the holding screen (else it stayed used up)
            if self._refill(gen):
                with self._lock:
                    if self._current(gen) and self._healed:
                        self._healed["message"] = self._healed["message"].rstrip(".") + (
                            "; the holding screen is on YouTube while the delay fills again.")
