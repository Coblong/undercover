"""What the routes are given: a connection and the agents."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import Connection

from backend import agent_service
from backend.db import get_db

Db = Annotated[Connection, Depends(get_db)]
ClaimAgent = Annotated[agent_service.ClaimAgent, Depends(agent_service.claim_agent)]
DispatchStarter = Annotated[
    agent_service.DispatchStarter, Depends(agent_service.dispatch_starter)
]
EvaluationStarter = Annotated[
    agent_service.EvaluationStarter, Depends(agent_service.evaluation_starter)
]
