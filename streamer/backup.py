"""
The plugin's settings kept in Farmhand too (the stream:backup permission), so a plugin rebuilt from scratch (its
volume lost, a new machine) can be given them back from Farmhand's Plugins page.

Pushing: while the plugin runs, a worker looks every EVERY seconds at what's in its store and, when that's changed
since it last reached Farmhand, PUTs it to Farmhand's /backup: {format: 1, saved_at, settings: {key: value}}, every key
of the store but NOT_KEPT. What it last pushed is remembered only in memory (as a digest), so it pushes once after
each start. Farmhand keeps it in its settings folder on the NAS. It holds the stream key and the YouTube sign-in: they
only ever travel over the plugin API, with the plugin's token, and are never printed or logged here.

Restoring: Farmhand's Plugins page has Restore, which POSTs /api/farmhand-backup/restore here with only the backup's
time in it (no secrets). The plugin then asks Farmhand for the backup itself, with its token (GET /backup), and puts
its values in place of its own, keeping NOT_KEPT as they are. A stream that's live carries on as it is; what was
restored is used from the next Go live.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone

from . import farm
from .farmhand import FarmhandError
from .live_stream.runner import LIVE_KEY
from .live_stream.sources import CAMERAS_KEY
from .save_location import PASSWORD_KEY
from .store import Store, store

FORMAT = 1
MOST = 2 * 1024 * 1024  # bytes: the biggest backup Farmhand takes
EVERY = 30  # seconds between looks at whether the settings changed
SLOW = 60  # seconds before trying again when Farmhand said no (the permission isn't granted yet: it's sent within a
# minute of the operator pressing Allow; the token refused...)

# Never backed up, and never overwritten by a restore (each kept as the plugin has it):
NOT_KEPT = (
    LIVE_KEY,  # "live_wanted": whether the stream is live now. Restored, a rebuilt plugin would go live by itself
    PASSWORD_KEY,  # the save location's NAS password: Farmhand never writes a share's password anywhere, so neither
    # does this (the share itself is kept; the password is typed again on the plugin's settings page)
    CAMERAS_KEY,  # "cameras_last_heard": Farmhand's own cameras, kept as a fallback and asked again every minute; it
    # changes by itself, so backing it up would push a new copy every time a camera came or went
)

_lock = threading.Lock()
# pushed: digest of the settings Farmhand last took; saved_at: when they were made (Farmhand's answer, or the
# restored backup's); error: why the last push didn't happen, for the plugin's own page; wait_until: a monotonic
# time before which nothing is pushed (Farmhand said no)
_state: dict = {"pushed": None, "saved_at": None, "error": None, "wait_until": 0.0}
_stop = threading.Event()
_thread: threading.Thread | None = None


def settings_of(db: Store) -> dict[str, str]:
    """What's backed up: every value in the store but NOT_KEPT."""
    return {k: v for k, v in db.snapshot().items() if k not in NOT_KEPT}


def _digest(settings: dict[str, str]) -> str:
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def status() -> dict:
    """{saved_at, error}: when Farmhand last took (or gave back) these settings, and why it couldn't since."""
    with _lock:
        return {"saved_at": _state["saved_at"], "error": _state["error"]}


def reset() -> None:
    """Nothing pushed yet (tests, and the plugin starting)."""
    with _lock:
        _state.update(pushed=None, saved_at=None, error=None, wait_until=0.0)


def _failed(error: str, wait: float = 0.0) -> str:
    with _lock:
        _state.update(error=error, wait_until=time.monotonic() + wait if wait else 0.0)
    return "failed"


def push(db: Store | None = None, force: bool = False) -> str:
    """
    Push the settings to Farmhand when they changed since it last took them. "pushed", "same" (nothing changed),
    "waiting" (Farmhand said no a little while ago: not asked again yet) or "failed" (status() says why). Farmhand being
    down or busy is tried again at the next look; a refusal (no stream:backup, the token) only every SLOW seconds.
    """
    db = db or store()
    settings = settings_of(db)
    digest = _digest(settings)
    with _lock:
        if digest == _state["pushed"]:
            return "same"
        if not force and time.monotonic() < _state["wait_until"]:
            return "waiting"
    backup = {"format": FORMAT, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "settings": settings}
    if len(json.dumps(backup).encode("utf-8")) > MOST:
        return _failed("These settings are too big for Farmhand to keep a copy (over 2 MB): too many saved layouts?", SLOW)
    try:
        answer = farm.client(timeout=15).save_backup(backup)
    except FarmhandError as e:
        if e.kind == "forbidden":
            return _failed("Farmhand isn't keeping a copy of these settings: this plugin wasn't granted stream:backup. "
                           "Add it on Farmhand's Plugins page (Allow).", SLOW)
        if e.code == 404:
            return _failed("This Farmhand doesn't keep copies of plugins' settings yet: update Farmhand.", SLOW)
        if e.kind in ("down", "busy"):
            return _failed(f"Couldn't give Farmhand a copy of these settings: {e.message}")
        return _failed(f"Couldn't give Farmhand a copy of these settings: {e.message}", SLOW)
    with _lock:
        _state.update(pushed=digest, saved_at=(answer or {}).get("saved_at") or backup["saved_at"], error=None, wait_until=0.0)
    return "pushed"


# ------------------------------------------------------------------ restoring


class RestoreError(Exception):
    """Restoring didn't happen: `status` is the HTTP answer's code, the message plain words for the operator."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def restore(db: Store | None = None) -> dict:
    """Farmhand's copy put in place of the plugin's settings (NOT_KEPT kept as they are): {restored, saved_at, note}."""
    db = db or store()
    try:
        got = farm.client(timeout=15).backup()
    except FarmhandError as e:
        if e.code == 404:
            raise RestoreError(404, "Farmhand has no copy of this plugin's settings yet. It keeps one once the plugin "
                                    "has run for a minute with the stream:backup permission.") from None
        if e.kind == "forbidden":
            raise RestoreError(403, "This plugin wasn't granted stream:backup, so it can't read Farmhand's copy of its "
                                    "settings: add it on Farmhand's Plugins page with Allow.") from None
        raise RestoreError(502, f"Couldn't get the copy of these settings from Farmhand: {e.message}") from None
    if not isinstance(got, dict) or got.get("format") != FORMAT:
        raise RestoreError(422, "Farmhand's copy of these settings is in a form this plugin doesn't know (made by a "
                                "newer version?), so nothing was changed.")
    settings = got.get("settings")
    if not isinstance(settings, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in settings.items()):
        raise RestoreError(422, "Farmhand's copy of these settings isn't readable, so nothing was changed.")
    db.replace(settings, keep=NOT_KEPT)
    db.commit()
    _restored(db, got.get("saved_at"))
    return {"restored": True, "saved_at": got.get("saved_at"), "note": _note(db)}


def _restored(db: Store, saved_at: str | None) -> None:
    """What the store holds now is Farmhand's copy: not pushed back (it has it), and things read once read again."""
    from . import stream_media

    with _lock:
        _state.update(pushed=_digest(settings_of(db)), saved_at=saved_at, error=None, wait_until=0.0)
    stream_media.forget()  # (the stream folder may be another one now: the restored save location's)


def _note(db: Store) -> str:
    from .live_stream import stream
    from .save_location import chosen

    if stream.status()["live"]:
        words = "Restored. A stream that's live keeps going as it is; the restored settings are used from the next Go live."
    else:
        words = "Restored. The restored settings are used from the next Go live."
    location = chosen(db)
    if location and location.get("username") and not db.get(PASSWORD_KEY):
        words += " Type the save location's password again on this plugin's settings: passwords aren't kept in the copy."
    return words


# ------------------------------------------------------------------ the worker


def start() -> None:
    """Look every EVERY seconds, pushing when the settings changed, from now until stop()."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def run() -> None:
        while not _stop.is_set():
            try:
                push()
            except Exception as e:  # noqa: BLE001 - a hiccup here never stops the plugin: tried again next time
                _failed(f"Couldn't give Farmhand a copy of these settings ({type(e).__name__}).")
            _stop.wait(EVERY)

    _thread = threading.Thread(target=run, name="stream-backup", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
