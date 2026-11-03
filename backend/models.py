"""Request and response models for the API."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
SectionLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]

StepKind = Literal["tool_call", "tool_result", "message", "read", "error"]
DocumentName = Literal["wording", "manual"]

State = Literal[
    "waiting", "in_progress", "waiting_for_sign_off", "decided", "left_for_handler"
]


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
    signed_off_by: str | None
    signed_off_at: str | None


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
    needs_sign_off: bool


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


class SectionRef(BaseModel):
    number: int
    title: str


class Section(SectionRef):
    document: DocumentName
    text: str


class Step(BaseModel):
    id: int
    kind: StepKind
    text: str
    created_at: str


class Outcome(BaseModel):
    claim_id: str
    state: State
    message: str


class DecisionRequest(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(allow_inf_nan=False)
    explanation: Reason
    policy_section: SectionLabel


class PaymentRequest(BaseModel):
    amount: float = Field(allow_inf_nan=False)


class SignOffRequest(BaseModel):
    handler_id: str


class StepRequest(BaseModel):
    kind: StepKind
    text: str = Field(max_length=20000)


class ModelCallRequest(BaseModel):
    run_id: int | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0, allow_inf_nan=False)


class WorkTimeRequest(BaseModel):
    run_id: int | None
    seconds: float = Field(ge=0, allow_inf_nan=False)


RunState = Literal["running", "finished", "failed", "stopped"]
Effort = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
]


class CreateDispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    model: Label
    reasoning_effort: Effort


class EndRunRequest(BaseModel):
    state: Literal["finished", "failed"]


class EndAttemptRequest(BaseModel):
    error: str | None = Field(max_length=2000)


class RunClaims(BaseModel):
    run_id: int
    claim_ids: list[str]


class StartedClaim(BaseModel):
    claim_id: str
    thread_id: str
    resumed: bool


class StoppedRuns(BaseModel):
    stopped: int


class HeldPayout(BaseModel):
    claim_id: str
    payout: float


class DispatchSummary(BaseModel):
    approved: int
    declined: int
    referred: int
    waiting_for_sign_off: list[HeldPayout]
    left_for_handler: list[str]
    in_progress: list[str]


class DispatchClaim(BaseModel):
    claim_id: str
    state: State
    decision: Literal["approve", "decline", "refer"] | None
    payout: float | None
    started_at: str | None
    seconds: float
    cost: float


class Dispatch(BaseModel):
    run_id: int
    concurrency: int
    model: str
    reasoning_effort: str
    state: RunState
    started_at: str
    ended_at: str | None
    seconds: float
    claim_seconds: float
    cost: float
    claims: list[DispatchClaim]
    summary: DispatchSummary | None
