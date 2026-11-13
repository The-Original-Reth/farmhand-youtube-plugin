"""
Clips, saved as MP4s in the clips folder (live_stream/clips.py), from what's chosen on the page:

- the stream: the last 1 second to 5 minutes of what went to YouTube, saved at once;
- a camera (any Farmhand can see): the next 1 second to 5 minutes from it, recorded in the background.

    GET  /api/clips          {seconds, least, most, held, sources: [{key, label}], recording: [...], ended: [...],
                             folder: {path, shown_as, problem}, clips: [{name, size, made}]}
    PUT  /api/clips          {seconds}: how long a clip is (1 to 300)
    POST /api/clips          {source?, seconds?, tz?}: the stream (the default): saved now, 201 {name, seconds, size};
                             a camera ("printer id:camera name"): recording starts, 202 {recording: {...}}
    GET  /api/clips/{name}   one of the listed clips, to download
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import save_location
from .errors import answer_errors
from .live_stream import clips, procs
from .live_stream.sources import known
from .routes import get_db
from .store import Store

router = APIRouter(prefix="/api/clips", tags=["clips"])


class ClipLengthIn(BaseModel):
    seconds: int = Field(ge=clips.LEAST_SECONDS, le=clips.MOST_SECONDS)


class ClipIn(BaseModel):
    source: str = Field(clips.STREAM, max_length=200)  # "stream", or a camera: "printer id:camera name"
    seconds: int | None = Field(None, ge=clips.LEAST_SECONDS, le=clips.MOST_SECONDS)  # (none: as set)
    tz: str | None = Field(None, max_length=64)  # the page's time zone, for the clip's name


@router.get("")
def clips_page(db: Store = Depends(get_db)):
    folder = save_location.clips_dir(db)
    return {
        "seconds": clips.seconds_of(db), "least": clips.LEAST_SECONDS, "most": clips.MOST_SECONDS,
        "held": clips.recent.held(),
        "sources": clips.sources(known(db)),  # (the cameras as last heard from Farmhand: asking it isn't waited on)
        **clips.recordings.state(),
        "folder": {"path": str(folder), "shown_as": save_location.shown_as(db, "clips"), "problem": clips.folder_problem(folder)},
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
    seconds = payload.seconds or clips.seconds_of(db)
    if payload.source == clips.STREAM:
        with answer_errors(invalid=409):
            return clips.save(ffmpeg, save_location.clips_dir(db), seconds, payload.tz)
    with answer_errors(invalid=422):
        label, cam = clips.camera_of(known(db), payload.source)
    with answer_errors(invalid=409):
        job = clips.record_camera(ffmpeg, save_location.clips_dir(db), payload.source, label, cam, seconds, payload.tz)
    return JSONResponse(status_code=202, content={"recording": job})


@router.get("/{name}")
def get_clip(name: str, db: Store = Depends(get_db)):
    try:
        path = clips.clip_path(save_location.clips_dir(db), name)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return FileResponse(path, media_type="video/mp4", filename=name)
