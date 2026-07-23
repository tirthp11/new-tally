import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class VoucherCreate(BaseModel):
    document_id: uuid.UUID


class VoucherUpdate(BaseModel):
    edited_json: dict
    company_id: uuid.UUID | None = None


class VoucherPush(BaseModel):
    company_id: uuid.UUID
    # The date the user confirmed after seeing any education-mode adjustment.
    confirmed_date: str | None = None


class VoucherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    company_id: uuid.UUID | None
    voucher_type: str
    party_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    amount: float | None
    status: str
    tally_voucher_number: str | None
    pushed_at: dt.datetime | None
    created_at: dt.datetime
    # Admin-only: display name of the operator who uploaded the source document.
    # None when viewed by a non-admin or when uploaded by an admin.
    uploaded_by_name: str | None = None


class VoucherDetail(VoucherOut):
    edited_json: dict | None
    error_log: dict | None


class DraftResponse(BaseModel):
    """Returned when the review screen opens: the draft plus suggestions."""

    voucher: VoucherDetail
    suggestions: dict


class DatePreview(BaseModel):
    """What push would do to the invoice date, shown to the user before push."""

    extracted_date: str | None
    final_date: str | None
    changed: bool
    reason: str | None = None
