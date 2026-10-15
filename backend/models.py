"""Request and response models for the API."""

from typing import Literal

from pydantic import BaseModel

State = Literal["waiting", "in_progress", "waiting_for_sign_off", "decided"]


class Item(BaseModel):
    description: str
    category: str
    amount: float


class Document(BaseModel):
    name: str
    type: str
    content: str


class Decision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float
    explanation: str
    policy_section: str
    decided_at: str


class Payment(BaseModel):
    amount: float
    paid_at: str


class SignOff(BaseModel):
    handler_id: str
    handler_name: str
    signed_off_at: str


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    claim_type: str
    summary: str
    amount_claimed: float
    submitted_on: str
    state: State


class ClaimDetail(ClaimSummary):
    policy_id: str
    customer_id: str
    incident_date: str
    incident_location: str
    story: str
    items: list[Item]
    documents: list[Document]
    decision: Decision | None
    payment: Payment | None
    sign_off: SignOff | None


class Trip(BaseModel):
    destination: str
    departs_on: str
    returns_on: str
    trip_cost: float


class Policy(BaseModel):
    policy_id: str
    customer_id: str
    plan: Literal["economy", "premium", "first"]
    add_ons: list[str]
    purchased_on: str
    sold_through: str
    trip: Trip
    travelers: list[str]
    status: str


class PreviousClaim(BaseModel):
    claim_id: str
    filed_on: str
    incident_date: str
    claim_type: str
    summary: str
    outcome: str
    amount_paid: float


class ClaimHistory(BaseModel):
    customer_id: str
    previous_claims: list[PreviousClaim]


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str


class Text(BaseModel):
    text: str


class Step(BaseModel):
    id: int
    kind: Literal["tool_call", "tool_result", "message", "error"]
    text: str
    created_at: str


class Outcome(BaseModel):
    claim_id: str
    state: State
    message: str


class DecisionRequest(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float
    explanation: str
    policy_section: str


class PaymentRequest(BaseModel):
    amount: float


class SignOffRequest(BaseModel):
    handler_id: str


class StepRequest(BaseModel):
    kind: Literal["tool_call", "tool_result", "message", "error"]
    text: str
