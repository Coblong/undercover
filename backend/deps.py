"""What the routes are given: a connection and the agents."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import Connection

from backend import work
from backend.db import get_db

Db = Annotated[Connection, Depends(get_db)]
ClaimAgent = Annotated[work.ClaimAgent, Depends(work.claim_agent)]
QueueAgent = Annotated[work.QueueAgent, Depends(work.queue_agent)]
