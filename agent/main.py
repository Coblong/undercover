"""The UnderCover Agent: the claim agent, a LangGraph graph that saves its progress."""

import contextvars
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal, TypedDict

import httpx
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field, SecretStr

from agent import settings, tracing
from agent.settings import AgentError, error_text

logger = logging.getLogger(__name__)

ClaimGraph = CompiledStateGraph["ClaimState", None, "ClaimState", "ClaimState"]

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
STEP_LIMIT = 600
CALL_LIMIT = 30
RECURSION_LIMIT = 3 * CALL_LIMIT + 20
MODEL_TRIES = 3
RETRY_DELAY = 1.0
CONNECT_TRIES = 3
clock = time.monotonic
HELD = "waiting for sign-off"
MODEL_TIMEOUT = 120
MODEL_RETRIES = 5
DOCUMENT_LABEL = {"wording": "Policy wording", "manual": "Claims manual"}


class PayoutLine(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        description="The total claimed for the line in US dollars, before any limits."
        " For several items, the total for all of them"
    )
    quantity: int = Field(
        description="How many separate items the line holds. A pair or a set is one"
        " item. Send 1 for anything else"
    )
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ] = Field(
        description="item: a belonging, or a pair or set. electronics: only a phone,"
        " tablet, laptop, camera or its lens, e-reader or smartwatch. cash: cash lost"
        " or stolen. expense: any other cost with a receipt. hotel: a hotel during a"
        " trip delay. meals_without_receipts: meals during a trip delay with no"
        " receipts"
    )
    nights: int = Field(description="For a hotel, the nights. Send 0 otherwise")
    hours: float = Field(
        description="For meals without receipts, the hours of delay. Send 0 otherwise"
    )


def backend(timeout: float = 30) -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=settings.backend_url(), timeout=timeout)


def reason(response: httpx.Response) -> str:
    """Return why the backend refused, whatever shape its answer has."""
    try:
        return str(response.json()["detail"])
    except (ValueError, KeyError, TypeError):
        return response.text or response.reason_phrase


def call(
    method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 30
) -> str:
    """Call the backend and return the answer as text, or the reason it failed.

    A call that never reached the backend is tried again. One that may have
    reached it is not, since it could then happen twice.
    """
    for attempt in range(CONNECT_TRIES):
        try:
            with backend(timeout) as client:
                response = client.request(method, path, json=body)
        except httpx.ConnectError:
            if attempt < CONNECT_TRIES - 1:
                time.sleep(RETRY_DELAY * 2**attempt)
                continue
            return "Error: the backend could not be reached"
        except httpx.TransportError as exc:
            return f"Error: the backend did not answer ({type(exc).__name__})"
        if response.is_success:
            return response.text
        return f"Error {response.status_code}: {reason(response)}"
    return "Error: the backend could not be reached"


def claim_tools(claim_id: str) -> list[BaseTool]:
    """Build the claim agent's tools for one claim.

    The claim is fixed here, so the model has no id to change and can only
    ever read or write the claim it was given.
    """

    @tool
    def get_claim() -> str:
        """Get your claim in full: the story, items, documents and any decision."""
        return call("GET", f"/api/claims/{claim_id}")

    @tool
    def get_policy() -> str:
        """Get the policy for your claim: plan, add-ons, trip dates and travelers."""
        claim = call("GET", f"/api/claims/{claim_id}")
        if claim.startswith("Error"):
            return claim
        return call("GET", f"/api/policies/{json.loads(claim)['policy_id']}")

    @tool
    def get_claim_history() -> str:
        """Get the customer's claims filed before your claim, with their outcomes."""
        return call("GET", f"/api/claims/{claim_id}/claim-history")

    @tool
    def list_document_sections(document: Literal["wording", "manual"]) -> str:
        """List the numbered sections of the policy wording or the claims manual."""
        return call("GET", f"/api/documents/{document}/sections")

    @tool
    def get_document_section(
        document: Literal["wording", "manual"], number: int
    ) -> str:
        """Read one numbered section of the policy wording or the claims manual."""
        return call("GET", f"/api/documents/{document}/sections/{number}")

    @tool
    def decide_claim(
        decision: Literal["approve", "decline", "refer"],
        payout: float,
        explanation: str,
        policy_section: str,
    ) -> str:
        """Record your decision on your claim. The payout is 0 unless you approve.

        policy_section is one section's number and title exactly as listed, with
        nothing added, such as "3. Trip Delay".
        """
        body = {
            "decision": decision,
            "payout": payout,
            "explanation": explanation,
            "policy_section": policy_section,
        }
        return call("POST", f"/api/claims/{claim_id}/decision", body)

    @tool
    def calculate_payout(
        benefit: Literal[
            "trip_cancellation",
            "trip_interruption",
            "trip_delay",
            "baggage_delay",
            "baggage",
            "passport",
            "emergency_medical",
        ],
        lines: list[PayoutLine],
        paid_by_others: float,
    ) -> str:
        """Work out the payout on an approved claim with UnderCover's payout rules.

        Send each covered item or expense as a line and leave out the excluded
        ones. paid_by_others is anything a carrier, hotel or other party has paid or
        refunded for the same loss, or 0. It returns the calculation and the payout.
        """
        body = {
            "benefit": benefit,
            "lines": [line.model_dump() for line in lines],
            "paid_by_others": paid_by_others,
        }
        return call("POST", f"/api/claims/{claim_id}/payout", body)

    return [
        get_claim,
        get_policy,
        get_claim_history,
        list_document_sections,
        get_document_section,
        calculate_payout,
        decide_claim,
    ]


def model(name: str, effort: str) -> ChatOpenAI:
    """Return a model on OpenRouter. The effort `default` leaves it to the model."""
    return ChatOpenAI(
        model=name,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(settings.api_key()),
        timeout=MODEL_TIMEOUT,
        max_retries=MODEL_RETRIES,
        reasoning_effort=None if effort == "default" else effort,
    )


def reply_cost(message: BaseMessage) -> float:
    """Return what a model reply cost, as OpenRouter reports it.

    That is `usage.cost`. On a key of UnderCover's own (`is_byok`), OpenRouter
    charges only its fee there and reports what the provider charged in
    `cost_details`, so the two are added. Otherwise `cost` already includes it.
    """
    usage = message.response_metadata.get("token_usage") or {}
    cost = usage.get("cost") or 0.0
    if usage.get("is_byok"):
        cost += (usage.get("cost_details") or {}).get("upstream_inference_cost") or 0.0
    return cost


def steps_of(message: BaseMessage) -> list[tuple[str, str]]:
    """Turn one message into the steps (kind, text) the team sees."""
    steps: list[tuple[str, str]]
    if isinstance(message, ToolMessage):
        steps = [("tool_result", str(message.text))]
        if message.name == "get_document_section" and not message.text.startswith(
            "Error"
        ):
            section = json.loads(message.text)
            label = DOCUMENT_LABEL[section["document"]]
            read = f"{label}, {section['number']}. {section['title']}"
            steps.insert(0, ("read", read))
        return steps
    if not isinstance(message, AIMessage):
        return []
    steps = [("message", str(message.text))] if message.text else []
    for call_ in message.tool_calls:
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call_["args"].items())
        steps.append(("tool_call", f"{call_['name']}({args})"))
    return steps


class ClaimState(TypedDict):
    """What the graph keeps for one claim. The checkpointer saves it after each step."""

    messages: Annotated[list[AnyMessage], add_messages]
    claim_id: str
    model_calls: int
    stage: Literal["undecided", "done", "sign_off", "pay"]
    payout: float
    approved_by: str
    reply: str


def record(claim_id: str, messages: list[BaseMessage]) -> None:
    """Post the steps of some messages to the claim for the team to see."""
    for message in messages:
        for kind, text in steps_of(message):
            clipped = text if len(text) <= STEP_LIMIT else text[:STEP_LIMIT] + "..."
            note(claim_id, kind, clipped)


def note(claim_id: str, kind: str, text: str) -> None:
    """Post one step to the claim."""
    answer = call("POST", f"/api/claims/{claim_id}/steps", {"kind": kind, "text": text})
    if answer.startswith("Error"):
        logger.warning("Step not recorded on %s: %s", claim_id, answer)


def money(amount: float) -> str:
    """Format an amount in US dollars."""
    return f"${amount:,.2f}"


def fetch_claim(claim_id: str) -> dict[str, Any]:
    """Read the claim from the backend, or stop with the reason it refused."""
    answer = call("GET", f"/api/claims/{claim_id}")
    if answer.startswith("Error"):
        raise AgentError(answer)
    return json.loads(answer)


class ClaimNodes:
    """The nodes and edges of the claim agent's graph, for one claim.

    The agent and tools nodes are the loop `create_agent` runs. The check node
    reads the claim from the backend, which alone knows whether a payout needs a
    sign-off. The approval node waits for a handler and the pay node pays.
    """

    def __init__(
        self,
        claim_id: str,
        instructions: str,
        llm: BaseChatModel,
        run_id: int | None = None,
    ) -> None:
        self.claim_id = claim_id
        self.run_id = run_id
        self.tools = claim_tools(claim_id)
        self.model = llm.bind_tools(self.tools)
        self.tool_node = ToolNode(self.tools, handle_tool_errors=True)
        self.system_message = SystemMessage(instructions)

    def agent(self, state: ClaimState) -> dict[str, Any]:
        """Call the model with the conversation so far."""
        message = self.model.invoke([self.system_message, *state["messages"]])
        self.record_usage(message)
        record(self.claim_id, [message])
        return {"messages": [message], "model_calls": state["model_calls"] + 1}

    def record_usage(self, message: BaseMessage) -> None:
        """Record the tokens and OpenRouter's cost of a model reply with its run."""
        tokens = message.usage_metadata if isinstance(message, AIMessage) else None
        body = {
            "run_id": self.run_id,
            "input_tokens": tokens["input_tokens"] if tokens else 0,
            "output_tokens": tokens["output_tokens"] if tokens else 0,
            "cost": reply_cost(message),
        }
        answer = call("POST", f"/api/claims/{self.claim_id}/model-calls", body)
        if answer.startswith("Error"):
            logger.warning("Model call not recorded on %s: %s", self.claim_id, answer)

    def run_tools(self, state: ClaimState) -> dict[str, Any]:
        """Run the tools the model asked for. A refusal comes back to it as text."""
        result = self.tool_node.invoke(state)
        record(self.claim_id, result["messages"])
        return result

    def check(self, state: ClaimState) -> dict[str, Any]:
        """Read where the claim stands and decide what the graph does next."""
        claim = fetch_claim(self.claim_id)
        decision = claim["decision"]
        if decision is None:
            return {"stage": "undecided"}
        payout = decision["payout"]
        found = {"payout": payout, "reply": f"{decision['decision']} {payout:.2f}"}
        if decision["decision"] != "approve" or claim["payment"] is not None:
            return {"stage": "done", **found}
        if claim["needs_sign_off"]:
            note(
                self.claim_id,
                "message",
                f"Waiting for a handler to sign off {money(payout)}.",
            )
            return {"stage": "sign_off", **found}
        return {"stage": "pay", **found}

    def approval(self, state: ClaimState) -> dict[str, Any]:
        """Wait for a handler's sign-off. The thread resumes with the handler."""
        answer = interrupt({"claim_id": self.claim_id, "payout": state["payout"]})
        note(self.claim_id, "message", f"Signed off by {answer['handler_name']}.")
        return {"approved_by": answer["handler_name"]}

    def pay(self, state: ClaimState) -> dict[str, Any]:
        """Pay the approved payout. The backend treats a repeat as done."""
        body = {"amount": state["payout"]}
        answer = call("POST", f"/api/claims/{self.claim_id}/payment", body)
        if answer.startswith("Error"):
            raise AgentError(f"the payment was refused: {answer}")
        note(self.claim_id, "message", f"Paid {money(state['payout'])}.")
        return {"reply": f"{state['reply']}, paid"}

    @staticmethod
    def after_agent(state: ClaimState) -> str:
        """Run the tools the model asked for, or end if it asked for none."""
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    @staticmethod
    def after_check(state: ClaimState) -> str:
        """Go back to the model until the claim is decided, then pay or wait."""
        if state["stage"] != "undecided":
            return {"done": END, "sign_off": "approval", "pay": "pay"}[state["stage"]]
        if state["model_calls"] >= CALL_LIMIT:
            raise AgentError(f"the model was called {CALL_LIMIT} times")
        return "agent"

    def compile(self, checkpointer: BaseCheckpointSaver[Any]) -> ClaimGraph:
        """Put the nodes and edges in a graph that saves its state after each step."""
        retry = RetryPolicy(
            max_attempts=MODEL_TRIES,
            initial_interval=RETRY_DELAY,
            jitter=False,
            retry_on=Exception,
        )
        builder = StateGraph(ClaimState)
        builder.add_node("agent", self.agent, retry_policy=retry)
        builder.add_node("tools", self.run_tools)
        builder.add_node("check", self.check)
        builder.add_node("approval", self.approval)
        builder.add_node("pay", self.pay)
        builder.add_edge(START, "check")
        builder.add_conditional_edges(
            "check", self.after_check, ["agent", "approval", "pay", END]
        )
        builder.add_conditional_edges("agent", self.after_agent, ["tools", END])
        builder.add_edge("tools", "check")
        builder.add_edge("approval", "pay")
        builder.add_edge("pay", END)
        return builder.compile(checkpointer=checkpointer)


def advance(graph: ClaimGraph, claim_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Start, resume or carry on a thread, as its saved state says."""
    snapshot = graph.get_state(config)  # pyright: ignore[reportArgumentType]
    if not snapshot.values:
        first: dict[str, Any] = {
            "messages": [HumanMessage(f"Decide claim {claim_id}.")],
            "claim_id": claim_id,
            "model_calls": 0,
            "stage": "undecided",
            "payout": 0.0,
            "approved_by": "",
            "reply": "",
        }
        return graph.invoke(first, config, durability="sync")  # pyright: ignore[reportArgumentType]
    if snapshot.interrupts:
        sign_off = fetch_claim(claim_id)["sign_off"]
        if sign_off is None:
            return {"__interrupt__": snapshot.interrupts}
        resume = Command(resume={"handler_name": sign_off["handler_name"]})
        return graph.invoke(resume, config, durability="sync")  # pyright: ignore[reportArgumentType]
    if snapshot.next:
        return graph.invoke(None, config, durability="sync")  # pyright: ignore[reportArgumentType]
    return snapshot.values


def record_time(claim_id: str, run_id: int | None, seconds: float) -> None:
    """Record the seconds the agent worked on a claim with its run."""
    body = {"run_id": run_id, "seconds": seconds}
    answer = call("POST", f"/api/claims/{claim_id}/work-times", body)
    if answer.startswith("Error"):
        logger.warning("Work time not recorded on %s: %s", claim_id, answer)


def run(
    claim_id: str,
    thread_id: str,
    llm: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    run_id: int | None = None,
) -> str:
    """Work a claim on a thread, and return a line saying how far it got.

    A new thread starts the claim. A thread that was stopped carries on from
    its last saved step. A thread waiting for a sign-off resumes once the
    backend has the sign-off, and otherwise stays as it is. The graph's work is
    timed and recorded with the run, a failed stretch included. A pause for a
    sign-off ends the stretch, so the wait is never counted.
    """
    path = (
        "/api/configuration/current"
        if run_id is None
        else f"/api/runs/{run_id}/configuration"
    )
    answer = call("GET", path)
    if answer.startswith("Error"):
        raise AgentError(answer)
    configuration = json.loads(answer)
    nodes = ClaimNodes(
        claim_id,
        configuration["instructions"],
        llm or model(configuration["model"], configuration["reasoning_effort"]),
        run_id,
    )
    graph = nodes.compile(checkpointer or InMemorySaver())
    config = {
        **tracing.config_for(thread_id, run_id),
        "recursion_limit": RECURSION_LIMIT,
    }
    started = clock()
    try:
        result = advance(graph, claim_id, config)
    finally:
        record_time(claim_id, run_id, clock() - started)
        tracing.flush()
    return HELD if "__interrupt__" in result else result["reply"]


class AlreadyWorking(Exception):
    """This process is already working the claim."""


_lock = threading.Lock()
_working: set[str] = set()


@contextmanager
def working(claim_id: str) -> Iterator[None]:
    """Hold a claim while this process works it, or raise AlreadyWorking."""
    with _lock:
        if claim_id in _working:
            raise AlreadyWorking(claim_id)
        _working.add(claim_id)
    try:
        yield
    finally:
        with _lock:
            _working.discard(claim_id)


def work_claim(
    claim_id: str,
    run_id: int,
    new_saver: Callable[[], BaseCheckpointSaver[Any]],
    llm: BaseChatModel | None = None,
) -> str:
    """Work a claim for a run and return the state it rests in.

    This is the one function the dispatcher calls. A waiting claim starts a new
    attempt, and a claim in progress carries on its open attempt on its thread.
    The claim gets a checkpointer of its own, so claims never take turns on one.
    A rate limit or a timeout passes through with the attempt left open, for a
    retry to carry on as the same attempt. Any other error fails the attempt. An
    evaluation's copy that stopped at a held approval counts as finished, and its
    saved progress is cleared since nobody can sign it off.
    A claim this process is already working is left alone.
    """
    try:
        with working(claim_id):
            # In a context of its own, so the claim is not a part of the dispatcher's
            # run: it keeps its own trace, session and run tag in Langfuse.
            return contextvars.Context().run(settle, claim_id, run_id, new_saver, llm)
    except AlreadyWorking:
        return "in_progress"


def settle(
    claim_id: str,
    run_id: int,
    new_saver: Callable[[], BaseCheckpointSaver[Any]],
    llm: BaseChatModel | None,
) -> str:
    """Start the claim in the run, work it and end its attempt."""
    started = time.monotonic()
    begun = call("POST", f"/api/runs/{run_id}/claims/{claim_id}/start")
    if begun.startswith("Error"):
        raise AgentError(begun)
    thread_id = json.loads(begun)["thread_id"]
    saver = new_saver()
    error = None
    reply = ""
    try:
        reply = run(claim_id, thread_id, llm, saver, run_id)
    except (ModelRateLimitError, ModelTimeoutError):
        raise
    except Exception as exc:  # the dispatch goes on, so the claim fails alone
        logger.exception("The agent failed on %s", claim_id)
        error = error_text(exc)
    ended = call("POST", f"/api/claims/{claim_id}/attempt/end", {"error": error})
    if ended.startswith("Error"):
        raise AgentError(ended)
    state = json.loads(ended)["state"]
    if json.loads(begun)["evaluation"] and reply == HELD:
        saver.delete_thread(thread_id)  # a copy's held approval counts as finished
    logger.info(
        "Attempt ended: claim=%s run=%s state=%s failed=%s seconds=%.1f",
        claim_id,
        run_id,
        state,
        error is not None or state in ("waiting", "left_for_handler"),
        time.monotonic() - started,
    )
    return state
