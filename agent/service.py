"""The agent's service: works a claim on a thread, and starts dispatches.

uv run -m uvicorn agent.service:app --port 8002 --env-file .env
"""

import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from agent import dispatcher, main, settings
from agent.dispatcher import WorkClaim
from agent.settings import AgentError

logger = logging.getLogger(__name__)

SCHEMA = "undercover"
POOL_SIZE = 4
POOL_TIMEOUT = 120
BACKEND_WAIT = 20
BUSY = "A dispatch or an evaluation is already running"

_gate = threading.Lock()


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
            _gate.release()


def start_run(
    path: str, body: dict[str, Any], concurrency: int, work: WorkClaim
) -> Started:
    """Create a run and work it in the background, unless one is already going."""
    if not _gate.acquire(blocking=False):
        raise HTTPException(409, BUSY)
    try:
        run_id = create_run(path, body)
    except BaseException:
        _gate.release()
        raise
    threading.Thread(
        target=work_run, args=(run_id, concurrency, work), daemon=True
    ).start()
    return Started(run_id=run_id)


@app.post("/dispatches", status_code=202)
def start_dispatch(body: DispatchRequest, work: Worker) -> Started:
    """Start a dispatch with this many claim agents at once and say its id."""
    run = {"concurrency": body.concurrency}
    return start_run("/api/runs/dispatch", run, body.concurrency, work)


@app.post("/evaluations", status_code=202)
def start_evaluation(body: EvaluationRequest, work: Worker) -> Started:
    """Start an evaluation of a labelled set and say its id."""
    run = {
        "concurrency": body.concurrency,
        "eval_set": body.eval_set,
        "tiny": body.tiny,
    }
    return start_run("/api/runs/evaluation", run, body.concurrency, work)
