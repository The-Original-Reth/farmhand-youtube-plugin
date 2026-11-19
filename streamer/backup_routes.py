"""
Restoring the plugin's settings from Farmhand's copy (backup.py), asked by Farmhand's Plugins page:

    POST /api/farmhand-backup/restore   {saved_at}: the copy Farmhand means (only its time: no secrets travel here)

The plugin fetches the copy itself, with its own token, and answers {restored: true, saved_at, note}; or
{detail: "<why not, in plain words>"} with 404 (Farmhand has none yet), 403 (no stream:backup), 422 (a copy it can't
read) or 502 (Farmhand couldn't be asked).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import backup

router = APIRouter(prefix="/api/farmhand-backup", tags=["settings backup"])


class RestoreIn(BaseModel):
    saved_at: str | None = None  # (what Farmhand's page showed; the copy is fetched from Farmhand whatever it says)


@router.post("/restore")
def restore(payload: RestoreIn | None = None):
    try:
        return backup.restore()
    except backup.RestoreError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from None
