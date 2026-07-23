"""Serve the desktop connector installer and its version."""
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.models import User

router = APIRouter(prefix="/download", tags=["download"])

CONNECTOR_EXE_NAME = "ABS-Tally-Connector.exe"
VERSION_FILE = "version.txt"


def _dist_path(*parts: str) -> str:
    settings = get_settings()
    return os.path.join(settings.connector_dist_dir, *parts)


@router.get("/connector")
def download_connector(user: User = Depends(get_current_user)):
    path = _dist_path(CONNECTOR_EXE_NAME)
    if not os.path.isfile(path):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "The connector installer has not been uploaded to the server yet.",
        )
    return FileResponse(path, filename=CONNECTOR_EXE_NAME,
                        media_type="application/vnd.microsoft.portable-executable")


@router.get("/connector/version")
def connector_version():
    path = _dist_path(VERSION_FILE)
    if not os.path.isfile(path):
        return {"version": None}
    with open(path, encoding="utf-8") as f:
        return {"version": f.read().strip()}
