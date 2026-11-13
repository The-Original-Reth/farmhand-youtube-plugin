"""
Where the plugin saves (save_location.py), for its settings page:

    GET    /api/save-location                  what's set, connected or why not, and each folder as people reach it
    PUT    /api/save-location                  {address, username, password?, domain?, version?}: use this location
    DELETE /api/save-location?confirm=true     no save location: the container's own folders again
    PUT    /api/save-location/folders/{key}    {path}: the stream or clips folder moved, inside the save location
    GET    /api/save-location/browse?path=     the folders in a folder of the save location, to choose one
    POST   /api/save-location/browse           {path, name}: a new folder there

The password goes in and never comes back out (has_password says whether one is kept).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import save_location
from .errors import answer_errors
from .store import Store, store

router = APIRouter(prefix="/api/save-location", tags=["save location"])


def get_db() -> Store:
    return store()


class LocationIn(BaseModel):
    address: str = Field(max_length=300)  # \\server\share\folder, //server/share/folder or server:/export
    username: str | None = Field(None, max_length=64)
    password: str | None = Field(None, max_length=128)  # None: the one kept (the same login)
    domain: str | None = Field(None, max_length=64)
    version: str | None = Field(None, max_length=8)


class FolderIn(BaseModel):
    path: str = Field(max_length=300)  # inside the save location, like "stream" or "Farmhand/YouTube clips"


class NewFolderIn(BaseModel):
    path: str = Field("", max_length=300)
    name: str = Field(max_length=120)


@router.get("")
def where_it_saves(db: Store = Depends(get_db)):
    return save_location.describe(db)


@router.put("")
def use_location(payload: LocationIn, db: Store = Depends(get_db)):
    with answer_errors():
        return save_location.set_location(db, payload.address, payload.username, payload.password, payload.domain,
                                          payload.version)


@router.delete("")
def forget_location(confirm: bool = Query(False), db: Store = Depends(get_db)):
    if not confirm:
        raise HTTPException(status_code=409, detail="Letting the save location go needs confirm=true.")
    return save_location.forget(db)


@router.put("/folders/{key}")
def move_folder(key: str, payload: FolderIn, db: Store = Depends(get_db)):
    with answer_errors():
        return save_location.set_folder(db, key, payload.path)


@router.get("/browse")
def browse(path: str = Query(""), db: Store = Depends(get_db)):
    with answer_errors():
        return save_location.browse(db, path)


@router.post("/browse")
def new_folder(payload: NewFolderIn, db: Store = Depends(get_db)):
    with answer_errors():
        return save_location.make_folder(db, payload.path, payload.name)
