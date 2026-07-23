import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    app_version: str | None
    last_seen_at: dt.datetime | None
    created_at: dt.datetime


class PairingCodeRequest(BaseModel):
    name: str


class PairingCodeResponse(BaseModel):
    connector_id: uuid.UUID
    # Shown once to the admin; only its hash is stored.
    pairing_code: str


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tally_name: str
    connector_id: uuid.UUID | None
    is_active: bool
    education_mode: bool
    fy_start_date: str | None
    last_synced_at: dt.datetime | None
    # Admin-only: display name of the operator who synced this company (via the
    # connector they paired). None when viewed by a non-admin or when the company
    # was synced by an admin. Purely a reference label.
    synced_by_name: str | None = None


class CompanyActiveUpdate(BaseModel):
    is_active: bool


class EducationModeUpdate(BaseModel):
    education_mode: bool
    fy_start_date: str | None = None  # YYYYMMDD, needed only when turning it on


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: uuid.UUID | None
    detail_json: dict | None
    created_at: dt.datetime


class MasterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    name: str
    parent: str | None
