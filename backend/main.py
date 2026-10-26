"""The UnderCover backend."""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend import api_claims, api_runs, attempts, runs
from backend.db import connect, init_db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create and seed the database before serving."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    init_db()
    with closing(connect()) as conn:
        attempts.recover(conn)
        runs.recover(conn)
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Say what is wrong with a request, without echoing the input back.

    The default answer repeats the input, which is not valid JSON when it is NaN.
    """
    detail = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(sqlite3.IntegrityError)
def conflict(_: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
    """Report a change the database refused as a conflict, not a crash."""
    return JSONResponse(
        status_code=409, content={"detail": "That conflicts with a change already made"}
    )


app.include_router(api_claims.router)
app.include_router(api_runs.router)
