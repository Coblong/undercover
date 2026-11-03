"""The agent's service: the entry point that works a claim on a thread.

uv run -m uvicorn agent.service:app --port 8002 --env-file .env
"""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from agent import main, settings
from agent.settings import AgentError

logger = logging.getLogger(__name__)

SCHEMA = "undercover"
POOL_SIZE = 2

_lock = threading.Lock()
_working: set[str] = set()


class WorkRequest(BaseModel):
    thread_id: str


class WorkResult(BaseModel):
    reply: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect the checkpointer to Supabase, unless a test has given it one."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    if getattr(app.state, "checkpointer", None) is not None:
        yield
        return
    pool = ConnectionPool(
        settings.database_url(),
        min_size=1,
        max_size=POOL_SIZE,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",
        },
        check=ConnectionPool.check_connection,
        open=True,
    )
    with pool:
        with pool.connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")  # pyright: ignore[reportCallIssue, reportArgumentType]
        saver = PostgresSaver(pool)  # pyright: ignore[reportArgumentType]
        saver.setup()
        app.state.checkpointer = saver
        yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


def checkpointer(request: Request) -> BaseCheckpointSaver[Any]:
    """Give a route the checkpointer that saves the graph's progress."""
    return request.app.state.checkpointer


def language_model() -> BaseChatModel | None:
    """Give a route the model to use, or None for the configured one."""
    return None


Saver = Annotated[BaseCheckpointSaver[Any], Depends(checkpointer)]
Model = Annotated[BaseChatModel | None, Depends(language_model)]


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
    with _lock:
        if claim_id in _working:
            raise HTTPException(409, f"Claim {claim_id} is already being worked")
        _working.add(claim_id)
    try:
        return WorkResult(reply=main.run(claim_id, body.thread_id, llm, saver))
    finally:
        with _lock:
            _working.discard(claim_id)
