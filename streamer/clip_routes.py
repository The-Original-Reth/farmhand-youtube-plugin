"""
Clips: the last 1 second to 5 minutes of what went to YouTube, saved as MP4s in the clips folder (live_stream/clips.py).

    GET  /api/clips          {seconds, least, most, held, folder: {path, shown_as, problem}, clips: [{name, size, made}]}
    PUT  /api/clips          {seconds}: how long a clip is (1 to 300)
    POST /api/clips          {seconds?, tz?}: save one now, {name, seconds, size}
    GET  /api/clips/{name}   one of the listed clips, to download
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config
from .errors import answer_errors
from .live_stream import clips, procs
from .store import Store, store

router = APIRouter(prefix="/api/clips", tags=["clips"])


def get_db() -> Store:
    return store()


class ClipLengthIn(BaseModel):
    seconds: int = Field(ge=clips.LEAST_SECONDS, le=clips.MOST_SECONDS)


class ClipIn(BaseModel):
    seconds: int | None = Field(None, ge=clips.LEAST_SECONDS, le=clips.MOST_SECONDS)  # (none: as set)
    tz: str | None = Field(None, max_length=64)  # the page's time zone, for the clip's name


def _folder():
    return config.settings.clips_dir


@router.get("")
def clips_page(db: Store = Depends(get_db)):
    folder = _folder()
    return {
        "seconds": clips.seconds_of(db), "least": clips.LEAST_SECONDS, "most": clips.MOST_SECONDS,
        "held": clips.recent.held(),
        "folder": {"path": str(folder), "shown_as": config.settings.clips_shown_as, "problem": clips.folder_problem(folder)},
        "clips": clips.listed(folder),
    }


@router.put("")
def set_length(payload: ClipLengthIn, db: Store = Depends(get_db)):
    with answer_errors():
        clips.set_seconds(db, payload.seconds)
    db.commit()
    return clips_page(db)


@router.post("", status_code=201)
def take_clip(payload: ClipIn, db: Store = Depends(get_db)):
    ffmpeg = procs.ffmpeg_path()
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg isn't installed where the plugin runs.")
    with answer_errors(invalid=409):
        return clips.save(ffmpeg, _folder(), payload.seconds or clips.seconds_of(db), payload.tz)


@router.get("/{name}")
def get_clip(name: str):
    try:
        path = clips.clip_path(_folder(), name)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return FileResponse(path, media_type="video/mp4", filename=name)
