"""Request and response models for the API."""

from typing import Annotated, Any, Literal

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
    approval_limit: float


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
    evaluation_id: int | None


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
    can_approve_over_limit: bool


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


class CreateDispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


class StartDispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


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
    evaluation: bool


class StoppedRuns(BaseModel):
    stopped: int
    stopped_rounds: int


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
    config_version: int
    state: RunState
    started_at: str
    ended_at: str | None
    seconds: float
    claim_seconds: float
    cost: float
    claims: list[DispatchClaim]
    summary: DispatchSummary | None


EvalSet = Literal["evaluation", "held_out"]


class StartEvaluationRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    eval_set: EvalSet
    tiny: bool


class CreateEvaluationRequest(StartEvaluationRequest):
    round_id: int | None


class Measures(BaseModel):
    decision_accuracy: float
    accuracy: float
    cost_of_mistakes: float
    straight_through: float
    cost_per_claim: float
    seconds_per_claim: float
    run_seconds: float | None
    grade: float | None
    judge_cost: float


class Evaluation(BaseModel):
    run_id: int
    eval_set: EvalSet
    tiny: bool
    concurrency: int
    model: str
    reasoning_effort: str
    config_version: int
    state: RunState
    started_at: str
    ended_at: str | None
    claim_count: int
    measures: Measures


class EvaluationClaim(BaseModel):
    claim_id: str
    source_id: str
    state: State
    handler_decision: Literal["approve", "decline", "refer"]
    handler_payout: float
    agent_decision: Literal["approve", "decline", "refer"] | None
    agent_payout: float | None
    reason: str | None
    decision_matches: bool
    right: bool
    mistake_cost: float
    cost: float
    seconds: float
    grade: float | None
    letter: str | None
    same_grounds: bool | None
    same_section: bool | None


class EvaluationDetail(Evaluation):
    results: list[EvaluationClaim]


class DiffLine(BaseModel):
    kind: Literal["add", "remove", "context"]
    text: str


class ConfigurationVersion(BaseModel):
    version: int
    instructions: str
    model: str
    reasoning_effort: str
    reason: str
    proposed_by: str
    approved_by: str | None
    approved_at: str | None
    created_at: str
    current: bool
    previous_model: str | None
    previous_reasoning_effort: str | None
    diff: list[DiffLine]


class RecipeVersion(BaseModel):
    version: int
    recipe: str
    reason: str
    proposed_by: str
    approved_by: str | None
    approved_at: str | None
    created_at: str
    current: bool
    diff: list[DiffLine]


class RunConfiguration(BaseModel):
    version: int
    instructions: str
    model: str
    reasoning_effort: str
    round_id: int | None


class Thresholds(BaseModel):
    accuracy: float = Field(ge=0, allow_inf_nan=False)
    grade: float = Field(ge=0, allow_inf_nan=False)
    cost_per_claim: float = Field(ge=0, allow_inf_nan=False)
    seconds_per_claim: float = Field(ge=0, allow_inf_nan=False)


class Objectives(BaseModel):
    accuracy: float = Field(ge=0, le=1, allow_inf_nan=False)
    grade: float = Field(ge=0, le=3, allow_inf_nan=False)


class SettingsValues(BaseModel):
    approval_limit: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    objectives: Objectives
    noise: Thresholds
    tolerance: Thresholds
    spending_limit: float = Field(gt=0, le=1000, allow_inf_nan=False)
    agents_at_once: int = Field(ge=1, le=25)


class SettingChange(BaseModel):
    key: str
    changed_by: str | None
    changed_at: str


class AllowedModel(BaseModel):
    model: str
    reasoning_efforts: list[str]
    input_price: float
    output_price: float


class SettingsView(BaseModel):
    values: SettingsValues
    changes: list[SettingChange]
    models: list[AllowedModel]


RoundState = Literal["open", "finished", "stopped"]
RoundStage = Literal[
    "first_evaluations",
    "improver_working",
    "change_waiting",
    "evaluating",
    "verdict",
    "recipe_waiting",
    "finished",
    "stopped",
]
Verdict = Literal["kept", "rolled_back"]


class Scores(BaseModel):
    runs: int
    accuracy: float
    grade: float | None
    cost_per_claim: float
    seconds_per_claim: float


class Needs(BaseModel):
    evaluation: int
    held_out: int


class Proposal(BaseModel):
    kind: Literal["change", "recipe"]
    version: int
    reason: str
    model: str | None
    reasoning_effort: str | None
    previous_model: str | None
    previous_reasoning_effort: str | None
    diff: list[DiffLine]


class RoundDecision(BaseModel):
    kind: Literal["change", "recipe"]
    outcome: Literal["approved", "rejected"]
    handler: str
    reason: str | None
    version: int | None
    decided_at: str


class Round(BaseModel):
    id: int
    state: RoundState
    stage: RoundStage
    done: int
    of: int
    settings: SettingsValues
    recipe_version: int
    started_by: str
    started_at: str
    ended_at: str | None
    stopped_by: str | None
    tried_version: int | None
    reason: str | None
    held_out_before: Scores | None
    held_out_after: Scores | None
    verdict: Verdict | None
    why: str | None
    pending: Proposal | None
    decisions: list[RoundDecision]
    cost: float
    improver_cost: float
    spending_left: float
    needs: Needs
    thread_id: str


class CreateRoundRequest(BaseModel):
    handler_id: str


class StageRequest(BaseModel):
    stage: Literal["first_evaluations", "improver_working", "evaluating", "verdict"]
    done: int = Field(ge=0)
    of: int = Field(ge=0)


class ProposalRequest(BaseModel):
    kind: Literal["change", "recipe"]
    instructions: str | None = Field(max_length=60000)
    model: str | None = Field(max_length=300)
    reasoning_effort: str | None = Field(max_length=40)
    recipe: str | None = Field(max_length=20000)
    reason: Reason


class ProposalDecisionRequest(BaseModel):
    outcome: Literal["approved", "rejected"]
    handler_id: str
    reason: str | None = Field(max_length=2000)


class RejectRequest(BaseModel):
    reason: Reason


class RoundCallRequest(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0, allow_inf_nan=False)


class EndRoundRequest(BaseModel):
    state: Literal["finished", "stopped"]
    why: str | None = Field(max_length=2000)


class CheckRequest(BaseModel):
    eval_set: EvalSet


class Check(BaseModel):
    allowed: bool
    estimate: float
    left: float
    message: str


class VerdictRequest(BaseModel):
    reason: str | None = Field(max_length=2000)


class ImproverView(BaseModel):
    report: dict[str, Any]
