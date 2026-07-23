import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime: str | None
    status: str
    error: str | None
    created_at: dt.datetime


class DocumentDetail(DocumentOut):
    extracted_json: dict | None
