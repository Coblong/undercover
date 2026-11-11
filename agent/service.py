"""The agent's service: works a claim on a thread, and starts dispatches.

uv run -m uvicorn agent.service:app --port 8002 --env-file .env
"""

import contextvars
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from agent import dispatcher, improver, main, settings, tracing
from agent import improver_client as client
from agent.dispatcher import WorkClaim
from agent.settings import AgentError, error_text

logger = logging.getLogger(__name__)

SCHEMA = "undercover"
POOL_SIZE = 4
POOL_TIMEOUT = 120
BACKEND_WAIT = 20
DISPATCH_RUNNING = "A dispatch is already running"
EVALUATION_RUNNING = "An evaluation is already running"
IMPROVER_RUNNING = "The improver is already working"

# One dispatch, evaluation or stretch of the improver's work runs at a time.
_gate = threading.Lock()
_holder = [""]


def take(holder: str) -> None:
    """Hold the one-run rule for some work, or refuse and say what is running."""
    if not _gate.acquire(blocking=False):
        raise HTTPException(409, _holder[0])
    _holder[0] = holder


def give_back() -> None:
    """Let the next piece of work start."""
    _gate.release()


class WorkRequest(BaseModel):
    thread_id: str


class WorkResult(BaseModel):
    reply: str


class DispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


class EvaluationRequest(DispatchRequest):
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool


class Started(BaseModel):
    run_id: int


def make_pool(url: str, open: bool = True) -> ConnectionPool:
    """Build the one pool every claim agent's saver shares.

    It holds at most 4 connections, and a claim agent that finds them in use
    waits for one for up to two minutes instead of failing.
    """
    return ConnectionPool(
        url,
        min_size=1,
        max_size=POOL_SIZE,
        timeout=POOL_TIMEOUT,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",
        },
        check=ConnectionPool.check_connection,
        open=open,
    )


def stop_stale_runs() -> None:
    """Mark every run the backend still has going as stopped, once it answers.

    This service is the only thing that works runs, so any run going when it
    starts was stopped with the process before it. `npm run dev` starts the
    backend beside it, so it waits a little for the backend.
    """
    for _ in range(BACKEND_WAIT):
        if not main.call("GET", "/api/health").startswith("Error"):
            break
        time.sleep(1)
    else:
        raise AgentError("The backend could not be reached")
    answer = main.call("POST", "/api/runs/stop")
    if answer.startswith("Error"):
        raise AgentError(answer)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect the checkpointer to Supabase, unless a test has given it one."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    if getattr(app.state, "checkpointer", None) is not None:
        stop_stale_runs()
        yield
        return
    pool = make_pool(settings.database_url())
    with pool:
        with pool.connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")  # pyright: ignore[reportCallIssue, reportArgumentType]
        saver = PostgresSaver(pool)  # pyright: ignore[reportArgumentType]
        saver.setup()
        app.state.checkpointer = saver
        app.state.new_saver = lambda: PostgresSaver(pool)  # pyright: ignore[reportArgumentType]
        stop_stale_runs()
        yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


def checkpointer(request: Request) -> BaseCheckpointSaver[Any]:
    """Give a route the checkpointer that saves the graph's progress."""
    return request.app.state.checkpointer


def language_model() -> BaseChatModel | None:
    """Give a route the model to use, or None for the configured one."""
    return None


def new_saver(request: Request) -> Callable[[], BaseCheckpointSaver[Any]]:
    """Give a route what builds a saver of its own for each claim agent."""
    state = request.app.state
    return getattr(state, "new_saver", None) or (lambda: state.checkpointer)


Saver = Annotated[BaseCheckpointSaver[Any], Depends(checkpointer)]
Model = Annotated[BaseChatModel | None, Depends(language_model)]
NewSaver = Annotated[Callable[[], BaseCheckpointSaver[Any]], Depends(new_saver)]


def claim_worker(saver: NewSaver, llm: Model) -> WorkClaim:
    """Give a route the one function that works a claim for a run. Tests replace it."""
    return lambda claim_id, run_id: main.work_claim(claim_id, run_id, saver, llm)


Worker = Annotated[WorkClaim, Depends(claim_worker)]


@app.exception_handler(Exception)
def failed(_: Request, exc: Exception) -> JSONResponse:
    """Tell the backend what stopped the claim, with the traceback in the log."""
    logger.error("The claim agent failed", exc_info=exc)
    detail = str(exc) if isinstance(exc, AgentError) else type(exc).__name__
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/health")
def health() -> dict[str, str]:
    """Say that the service is running."""
    return {"status": "ok"}


@app.post("/claims/{claim_id}/work")
def work_claim(
    claim_id: str, body: WorkRequest, saver: Saver, llm: Model
) -> WorkResult:
    """Start, resume or carry on a claim's thread and say how far it got."""
    try:
        with main.working(claim_id):
            return WorkResult(reply=main.run(claim_id, body.thread_id, llm, saver))
    except main.AlreadyWorking:
        raise HTTPException(409, f"Claim {claim_id} is already being worked") from None


def create_run(path: str, body: dict[str, Any]) -> int:
    """Have the backend create the run, and return its id."""
    with main.backend() as client:
        response = client.post(path, json=body)
    if not response.is_success:
        raise HTTPException(response.status_code, main.reason(response))
    return response.json()["run_id"]


def work_run(run_id: int, concurrency: int, work: WorkClaim) -> None:
    """Work a run's claims, then end the run and let the next one start."""
    state = "finished"
    try:
        dispatcher.run_dispatch(run_id, concurrency, work)
    except Exception:  # the run is in the background, so nobody else sees this
        logger.exception("Run %s failed", run_id)
        state = "failed"
    finally:
        try:
            main.call("POST", f"/api/runs/{run_id}/end", {"state": state})
        finally:
            give_back()


def start_run(
    holder: str, path: str, body: dict[str, Any], concurrency: int, work: WorkClaim
) -> Started:
    """Create a run and work it in the background, unless one is already going."""
    take(holder)
    try:
        run_id = create_run(path, body)
    except BaseException:
        give_back()
        raise
    threading.Thread(
        target=work_run, args=(run_id, concurrency, work), daemon=True
    ).start()
    return Started(run_id=run_id)


@app.post("/dispatches", status_code=202)
def start_dispatch(body: DispatchRequest, work: Worker) -> Started:
    """Start a dispatch with this many claim agents at once and say its id."""
    run = {"concurrency": body.concurrency}
    return start_run(
        DISPATCH_RUNNING, "/api/runs/dispatch", run, body.concurrency, work
    )


@app.post("/evaluations", status_code=202)
def start_evaluation(body: EvaluationRequest, work: Worker) -> Started:
    """Start an evaluation of a labelled set and say its id."""
    run = {
        "concurrency": body.concurrency,
        "eval_set": body.eval_set,
        "tiny": body.tiny,
        "round_id": None,
    }
    return start_run(
        EVALUATION_RUNNING, "/api/runs/evaluation", run, body.concurrency, work
    )


# --- the improver's rounds ----------------------------------------------------------


class RoundStart(BaseModel):
    handler_id: str
    handler_name: str


class RoundDecisionBody(BaseModel):
    outcome: Literal["approved", "rejected"]
    handler_id: str
    reason: str | None


def improver_llm() -> BaseChatModel | None:
    """Give a route the improver's model, or None for the configured one."""
    return None


ImproverLlm = Annotated[BaseChatModel | None, Depends(improver_llm)]


@dataclass
class Job:
    """What one stretch of the improver's work needs."""

    round_id: int
    thread_id: str
    recipe_version: int
    concurrency: int
    work: WorkClaim
    new_saver: Callable[[], BaseCheckpointSaver[Any]]
    llm: BaseChatModel | None


def refusal(refused: client.Refused) -> HTTPException:
    """Pass the backend's refusal on."""
    return HTTPException(refused.status, refused.reason)


def evaluate_set(job: Job, eval_set: str) -> str:
    """Work a whole-set evaluation for the round, and return the state it ended in.

    The claims run in a context of their own, since a graph with no checkpointer
    would otherwise save into the improver's thread.
    """
    run_id = client.create_evaluation(job.round_id, eval_set, job.concurrency)
    state = "finished"
    try:
        contextvars.Context().run(
            dispatcher.run_dispatch, run_id, job.concurrency, job.work
        )
    except Exception:  # the round goes on to roll the version back
        logger.exception("Evaluation %s failed", run_id)
        state = "failed"
    return client.end_run(run_id, state)


def run_first_evaluations(job: Job, needs: dict[str, int]) -> bool:
    """Evaluate the current version where it has no scores yet, within the limit."""
    plan = ["evaluation"] * needs["evaluation"] + ["held_out"] * needs["held_out"]
    for position, eval_set in enumerate(plan, start=1):
        check = client.check_evaluation(job.round_id, eval_set)
        if not check["allowed"]:
            client.end_round(job.round_id, "stopped", check["message"])
            return False
        client.record_stage(job.round_id, "first_evaluations", position, len(plan))
        if evaluate_set(job, eval_set) != "finished":
            client.end_round(job.round_id, "stopped", "An evaluation was stopped")
            return False
    return True


def drive(job: Job, resume: Command | None) -> None:
    """Run the improver until it pauses for a handler or finishes."""
    if resume is None:
        found = client.get_round(job.round_id)
        if not run_first_evaluations(job, found["needs"]):
            return
    client.record_stage(job.round_id, "improver_working", 0, 0)
    graph = improver.build(
        job.round_id,
        client.recipe_text(job.recipe_version),
        job.llm or improver.improver_model(),
        job.new_saver(),
        lambda eval_set: evaluate_set(job, eval_set),
    )
    inputs = resume or {"messages": [{"role": "user", "content": improver.START}]}
    result = graph.invoke(
        inputs, tracing.round_config(job.thread_id, job.round_id), durability="sync"
    )
    waiting = result.get("__interrupt__")
    if waiting:
        request = waiting[0].value["action_requests"][0]
        client.record_proposal(job.round_id, improver.proposal_of(request))
    else:
        client.end_round(job.round_id, "finished", None)


def stop_round_after(job: Job, why: str) -> None:
    """End the round as stopped with the reason, unless it already is."""
    try:
        client.end_round(job.round_id, "stopped", why)
    except client.Refused:
        logger.info("Round %s had already ended", job.round_id)


def stretch(job: Job, resume: Command | None) -> None:
    """Do one stretch of the improver's work, then let the next piece of work start."""
    try:
        drive(job, resume)
    except improver.RoundStopped:
        logger.info("Round %s was stopped", job.round_id)
    except client.Refused as refused:
        if not refused.round_stopped:
            logger.error("Round %s was refused: %s", job.round_id, refused.reason)
            stop_round_after(job, refused.reason)
    except Exception as exc:  # the round is in the background, so nobody else sees this
        logger.exception("Round %s failed", job.round_id)
        stop_round_after(job, error_text(exc))
    finally:
        tracing.flush()
        give_back()


def begin(round_id: int, work: WorkClaim, saver: Any, llm: Any) -> Job:
    """Build the job for a round from what the backend holds."""
    found = client.get_round(round_id)
    return Job(
        round_id=round_id,
        thread_id=found["thread_id"],
        recipe_version=found["recipe_version"],
        concurrency=found["settings"]["agents_at_once"],
        work=work,
        new_saver=saver,
        llm=llm,
    )


@app.post("/rounds", status_code=202)
def start_round(
    body: RoundStart, work: Worker, saver: NewSaver, llm: ImproverLlm
) -> Started:
    """Open a round for a handler and start the improver on it."""
    take(IMPROVER_RUNNING)
    try:
        created = client.create_round(body.handler_id)
        job = begin(created["id"], work, saver, llm)
    except client.Refused as refused:
        give_back()
        raise refusal(refused) from refused
    except BaseException:
        give_back()
        raise
    threading.Thread(target=stretch, args=(job, None), daemon=True).start()
    return Started(run_id=job.round_id)


@app.post("/rounds/{round_id}/decision", status_code=202)
def decide_round(
    round_id: int,
    body: RoundDecisionBody,
    work: Worker,
    saver: NewSaver,
    llm: ImproverLlm,
) -> Started:
    """Record a handler's decision on the proposal waiting, then carry the improver on.

    The decision and the handler are recorded first. The backend records nothing of
    its own.
    """
    take(IMPROVER_RUNNING)
    try:
        client.record_decision(round_id, body.outcome, body.handler_id, body.reason)
        job = begin(round_id, work, saver, llm)
    except client.Refused as refused:
        give_back()
        raise refusal(refused) from refused
    except BaseException:
        give_back()
        raise
    decision = (
        {"type": "approve"}
        if body.outcome == "approved"
        else {"type": "reject", "message": body.reason}
    )
    resume = Command(resume={"decisions": [decision]})
    threading.Thread(target=stretch, args=(job, resume), daemon=True).start()
    return Started(run_id=round_id)
