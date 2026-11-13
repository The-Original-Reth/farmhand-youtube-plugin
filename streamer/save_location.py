"""
Where the plugin saves, set on its settings page (shown on Farmhand's Settings page under the plugin's name), as
Farmhand sets its own: a save location on the NAS, typed as \\server\share\folder with its login, which the plugin
mounts itself (mounts.py), and a folder inside it for each kind of thing it keeps:

    stream   the stream folder: its pictures, slideshows and music ("stream" unless changed)
    clips    where Save clip puts its MP4s ("YouTube clips" unless changed)

Each folder can be changed to another one inside the save location (its Change…). Every folder is shown as people
reach it on the network: \\server\share\folder\stream.

Until a save location is set, the plugin uses the folders its container is given (STREAM_FOLDER and STREAM_CLIPS,
/media and /clips), as before, so a machine set up that way carries on unchanged. The password is kept in the plugin's
store under a key of its own and never comes back out of its API.

The mount lives in the container's own mount namespace, so it's gone when the container restarts: it's mounted again
when the plugin starts, and whenever a folder is asked for and it isn't mounted (a NAS that was off, say).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from . import config, mounts
from .store import Store

LOCATION_KEY = "save_location"  # {address, username, domain, version, folders: {stream, clips}}
PASSWORD_KEY = "save_location_password"  # (never answered)
FOLDERS = {"stream": ("Stream folder", "stream"), "clips": ("YouTube clips", "YouTube clips")}  # key: (label, default)
MOUNT_POINT = Path(os.environ.get("STREAM_NAS_MOUNT", "/nas/save-location"))
_lock = threading.Lock()
_problem: dict = {"text": None}  # why the save location isn't mounted now, or None


def chosen(db: Store) -> dict | None:
    """The save location as saved (no password), or None while there's none."""
    found = db.json(LOCATION_KEY)
    return found if isinstance(found, dict) and found.get("address") else None


def _spec(found: dict, password: str | None) -> mounts.ShareSpec:
    protocol, server, share = mounts.parse_location(found["address"])
    return mounts.validate_fields(protocol, server, share, found.get("username"), password, found.get("domain"), found.get("version"))


def network_path(found: dict, rest: str = "") -> str:
    """The save location (and `rest` inside it) as people reach it on the network: \\\\server\\share\\folder\\rest."""
    protocol, server, share = mounts.parse_location(found["address"])
    parts = [server, *share.strip("/").split("/"), *rest.strip("/").split("/")]
    return "\\\\" + "\\".join(p for p in parts if p)


def ensure(db: Store) -> bool:
    """The save location mounted (mounting it again when it isn't); False, with problem() saying why, when it can't be.
    False with no problem while there's no save location."""
    found = chosen(db)
    if not found:
        _problem["text"] = None
        return False
    with _lock:
        if mounts.is_mounted(MOUNT_POINT):
            _problem["text"] = None
            return True
        try:
            mounts.mount(_spec(found, db.get(PASSWORD_KEY)), MOUNT_POINT)
        except (mounts.MountError, ValueError) as e:
            _problem["text"] = str(e)
            return False
        _problem["text"] = None
        return True


def problem() -> str | None:
    return _problem["text"]


def folder_path(db: Store, key: str) -> Path | None:
    """Where a folder is inside the container while the save location is set and mounted (made if it isn't there);
    None while there's no save location (the container's own folder is used), or it can't be mounted."""
    found = chosen(db)
    if not found or not ensure(db):
        return None
    path = MOUNT_POINT / _relative(found, key)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _relative(found: dict, key: str) -> str:
    return ((found.get("folders") or {}).get(key) or FOLDERS[key][1]).strip("/")


def stream_dir(db: Store) -> Path:
    """The stream folder: inside the save location, or the container's own (/media) while there's none."""
    found = chosen(db)
    if found:
        return folder_path(db, "stream") or MOUNT_POINT / _relative(found, "stream")
    return Path(config.settings.media_dir)


def clips_dir(db: Store) -> Path:
    """Where clips go: inside the save location, or the container's own (/clips) while there's none."""
    found = chosen(db)
    if found:
        return folder_path(db, "clips") or MOUNT_POINT / _relative(found, "clips")
    return Path(config.settings.clips_dir)


def shown_as(db: Store, key: str) -> str | None:
    """A folder as people reach it: \\\\server\\share\\folder\\stream; with no save location, what the container was told
    (STREAM_FOLDER_SHOWN_AS / STREAM_CLIPS_SHOWN_AS), or None."""
    found = chosen(db)
    if found:
        return network_path(found, _relative(found, key))
    return config.settings.media_shown_as if key == "stream" else config.settings.clips_shown_as


def describe(db: Store) -> dict:
    """What the settings page shows: {address, username, domain, version, has_password, mounted, problem, capability,
    folders: [{key, label, path (as people reach it), relative, problem}]}; address None while there's none (the
    folders are then the container's own)."""
    found = chosen(db) or {}
    mounted = ensure(db) if found else False
    cap = mounts.mount_capability()
    folders = []
    for key, (label, default) in FOLDERS.items():
        if found:
            rel = _relative(found, key)
            folders.append({"key": key, "label": label, "path": network_path(found, rel), "relative": rel,
                            "problem": None if mounted else (problem() or "The save location isn't connected.")})
        else:
            local = Path(config.settings.media_dir if key == "stream" else config.settings.clips_dir)
            folders.append({"key": key, "label": label, "path": shown_as(db, key) or str(local), "relative": None,
                            "problem": None if local.is_dir() else f"There's no folder at {local} in the plugin's container."})
    return {
        "address": network_path(found) if found else None, "username": found.get("username"), "domain": found.get("domain"),
        "version": found.get("version") or "", "has_password": bool(db.get(PASSWORD_KEY)), "mounted": mounted,
        "problem": problem() if found else None,
        "capability": {"ok": cap["ok"], "message": cap["message"], "smb": cap["smb"], "nfs": cap["nfs"]},
        "folders": folders,
    }


def set_location(db: Store, address: str, username: str | None, password: str | None, domain: str | None,
                 version: str | None, keep_password: bool = True) -> dict:
    """Use this save location: checked, then mounted (the old one let go first; put back if the new one can't be),
    then saved. `password` None with `keep_password`: the saved one, for the same login. ValueError (in plain words)
    when it can't be used."""
    protocol, server, share = mounts.parse_location(address)
    if password is None and keep_password:
        password = db.get(PASSWORD_KEY)
    spec = mounts.validate_fields(protocol, server, share, username, password, domain, version)
    old = chosen(db)
    with _lock:
        try:
            mounts.unmount(MOUNT_POINT, lazy=True)
        except mounts.MountError:
            pass
        try:
            mounts.mount(spec, MOUNT_POINT)
        except mounts.MountError:
            if old:
                try:
                    mounts.mount(_spec(old, db.get(PASSWORD_KEY)), MOUNT_POINT)
                except (mounts.MountError, ValueError):
                    pass
            raise
    folders = (old or {}).get("folders") or {}
    db.put_json(LOCATION_KEY, {"address": address.strip(), "username": spec.username, "domain": spec.domain,
                               "version": spec.version or "", "folders": folders})
    db.put(PASSWORD_KEY, spec.password)
    db.commit()
    _problem["text"] = None
    for key in FOLDERS:
        folder_path(db, key)  # (made, if they aren't there yet)
    return describe(db)


def set_folder(db: Store, key: str, relative: str) -> dict:
    """A folder moved to another one inside the save location (made if it isn't there). ValueError when it can't be."""
    if key not in FOLDERS:
        raise ValueError("There's no such folder.")
    found = chosen(db)
    if not found:
        raise ValueError("Set the save location first: the folders are inside it.")
    rel = _safe_relative(relative)
    if not rel:
        raise ValueError("Choose a folder inside the save location, not the save location itself.")
    found = {**found, "folders": {**(found.get("folders") or {}), key: rel}}
    db.put_json(LOCATION_KEY, found)
    db.commit()
    folder_path(db, key)
    return describe(db)


def forget(db: Store) -> dict:
    """No save location: the container's own folders again (the share is let go; nothing on it is touched)."""
    db.put(LOCATION_KEY, None)
    db.put(PASSWORD_KEY, None)
    db.commit()
    with _lock:
        try:
            mounts.unmount(MOUNT_POINT, lazy=True)
        except mounts.MountError:
            pass
    _problem["text"] = None
    return describe(db)


def browse(db: Store, relative: str = "") -> dict:
    """The folders in a folder of the save location, for choosing one: {path (relative), shown_as, folders: [names]}."""
    found = chosen(db)
    if not found or not ensure(db):
        raise ValueError(problem() or "Set the save location first: the folders are inside it.")
    rel = _safe_relative(relative)
    here = MOUNT_POINT / rel if rel else MOUNT_POINT
    if not here.is_dir():
        raise ValueError("That folder isn't there.")
    names = sorted((p.name for p in here.iterdir() if p.is_dir() and not p.name.startswith(".")), key=str.lower)
    return {"path": rel, "shown_as": network_path(found, rel), "folders": names}


def make_folder(db: Store, relative: str, name: str) -> dict:
    """A new folder in a folder of the save location; then that folder browsed."""
    name = (name or "").strip()
    if not name or name in (".", "..") or any(ch in name for ch in '/\\:*?"<>|') or len(name) > 120:
        raise ValueError("Give the new folder a name without / \\ : * ? \" < > |.")
    rel = _safe_relative(relative)
    if not chosen(db) or not ensure(db):
        raise ValueError(problem() or "Set the save location first: the folders are inside it.")
    (MOUNT_POINT / rel / name if rel else MOUNT_POINT / name).mkdir(parents=False, exist_ok=True)
    return browse(db, f"{rel}/{name}" if rel else name)


def _safe_relative(relative: str | None) -> str:
    """A folder inside the save location: no .., no leading slash."""
    parts = [p for p in str(relative or "").replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("Choose a folder inside the save location.")
    return "/".join(parts)
