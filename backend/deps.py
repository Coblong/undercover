"""What the routes are given: a connection and the agents."""

import sqlite3
from typing import Annotated

from fastapi import Depends

from backend import work
from backend.db import get_db

Db = Annotated[sqlite3.Connection, Depends(get_db)]
ClaimAgent = Annotated[work.ClaimAgent, Depends(work.claim_agent)]
QueueAgent = Annotated[work.QueueAgent, Depends(work.queue_agent)]
