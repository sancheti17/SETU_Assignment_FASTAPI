"""Pydantic request/response contracts, also used to generate OpenAPI/Swagger."""
from decimal import Decimal
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .domain import normalize_event

Status = Literal['unknown', 'initiated', 'processed', 'failed', 'conflicted', 'settled']
Currency = Literal['INR', 'USD', 'EUR']
EventType = Literal['payment_initiated', 'payment_processed', 'payment_failed', 'settled']
Reason = Literal['processed_not_settled', 'settled_failed_payment', 'settled_without_processed', 'conflicting_payment_outcomes', 'multiple_settlement_events', 'ingestion_conflict']
ID = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]


class EventIn(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'example': {
        'event_id': 'demo-event-1', 'event_type': 'payment_initiated',
        'transaction_id': 'demo-tx-1', 'merchant_id': 'merchant_2',
        'merchant_name': 'FreshBasket', 'amount': '15248.29',
        'currency': 'INR', 'timestamp': '2026-01-08T12:11:58.085567+00:00'}})
    event_id: ID
    event_type: EventType
    transaction_id: ID
    merchant_id: ID
    merchant_name: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, le=Decimal('9999999999.99'), decimal_places=2,
        description='Positive exact money; a decimal string is recommended.')
    currency: Currency
    timestamp: str = Field(description='Timezone-aware ISO-8601 datetime, year 1970–2100.')

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, value):
        # Shared validation means HTTP and direct seed have the same semantics.
        return normalize_event(value)[0]


class EventReceipt(BaseModel):
    event_id: str
    transaction_id: str
    duplicate: bool


class ErrorInfo(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, Any]] | None = None
    event_id: str | None = None
    transaction_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


class TransactionQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    merchant_id: ID | None = None
    status: Status | None = None
    currency: Currency | None = None
    from_: str | None = Field(default=None, alias='from', description='Inclusive earliest-event timestamp.')
    to: str | None = Field(default=None, description='Exclusive earliest-event timestamp.')
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=50, ge=1, le=200)


class ListQuery(TransactionQuery):
    sort_by: Literal['created_at', 'last_event_at', 'amount', 'transaction_id'] = 'created_at'
    sort_order: Literal['asc', 'desc'] = 'desc'


class ReconciliationQuery(TransactionQuery):
    as_of: str | None = Field(default=None, description='Aging clock only; NOT historical state replay.')
    grace_hours: int | None = Field(default=None, ge=0, le=8760, description='Defaults to SETTLEMENT_GRACE_HOURS (24).')


class SummaryQuery(ReconciliationQuery):
    group_by: str = Field(default='merchant,date,status', description='Distinct comma-separated merchant,date,status. Currency is always included.')


class DiscrepancyQuery(ReconciliationQuery):
    reason: Reason | None = None


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    history_page: int = Field(default=1, ge=1, le=1000000)
    history_page_size: int = Field(default=100, ge=1, le=200)
    conflict_page: int = Field(default=1, ge=1, le=1000000)
    conflict_page_size: int = Field(default=100, ge=1, le=200)


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int


class Merchant(BaseModel):
    merchant_id: str
    merchant_name: str


class TransactionOut(BaseModel):
    transaction_id: str
    merchant_id: str
    merchant: Merchant
    amount: str
    amount_minor: int
    currency: Currency
    created_at: str
    last_event_at: str
    processed_at: str | None
    settled_at: str | None
    initiated_count: int
    processed_count: int
    failed_count: int
    settled_count: int
    conflict_count: int
    event_count: int
    status: Status
    payment_status: Literal['unknown', 'initiated', 'processed', 'failed', 'conflicted']
    settlement_status: Literal['unsettled', 'settled']


class TransactionList(BaseModel):
    data: list[TransactionOut]
    pagination: Pagination


class EventRecord(BaseModel):
    event_id: str
    transaction_id: str
    event_type: EventType
    occurred_at: str
    received_at: str
    payload_hash: str
    payload: dict[str, Any]


class ConflictRecord(BaseModel):
    conflict_id: int
    event_id: str
    transaction_id: str
    requested_transaction_id: str
    reason: Literal['idempotency_key_reuse', 'transaction_mismatch']
    payload_hash: str
    payload: dict[str, Any]
    detected_at: str


class TransactionDetail(TransactionOut):
    events: list[EventRecord]
    conflicts: list[ConflictRecord]
    history_pagination: Pagination
    conflict_pagination: Pagination


class DiscrepancyOut(TransactionOut):
    reasons: list[Reason]


class DiscrepancyList(BaseModel):
    data: list[DiscrepancyOut]
    pagination: Pagination
    as_of: str
    grace_hours: int
    cutoff: str


class SummaryRow(BaseModel):
    merchant: str | None = None
    date: str | None = None
    status: Status | None = None
    currency: Currency
    transaction_count: int
    discrepancy_count: int
    total_amount_minor: int
    processed_amount_minor: int
    settled_amount_minor: int
    outstanding_amount_minor: int
    total_amount: str
    processed_amount: str
    settled_amount: str
    outstanding_amount: str


class GroupPagination(BaseModel):
    page: int
    page_size: int
    total_groups: int


class SummaryOut(BaseModel):
    data: list[SummaryRow]
    pagination: GroupPagination
    group_by: list[str]
    as_of: str
    grace_hours: int
    cutoff: str
