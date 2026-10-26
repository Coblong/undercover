"""The UnderCover Agent."""

import json
import os
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv(override=True)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
STEP_LIMIT = 600
CALL_LIMIT = 40
RETRY_DELAY = 1.0
DOCUMENT_LABEL = {"wording": "Policy wording", "manual": "Claims manual"}


def backend(timeout: float = 30) -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=timeout)


def call(
    method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 30
) -> str:
    """Call the backend and return the answer as text, or the reason it refused."""
    with backend(timeout) as client:
        response = client.request(method, path, json=body)
    if response.is_success:
        return response.text
    return f"Error {response.status_code}: {response.json()['detail']}"


@tool
def get_claim(claim_id: str) -> str:
    """Get a claim in full: the story, items, documents and any decision."""
    return call("GET", f"/api/claims/{claim_id}")


@tool
def get_policy(policy_id: str) -> str:
    """Get a policy: its plan, add-ons, trip dates and travelers."""
    return call("GET", f"/api/policies/{policy_id}")


@tool
def get_claim_history(customer_id: str) -> str:
    """Get a customer's earlier claims."""
    return call("GET", f"/api/customers/{customer_id}/claim-history")


@tool
def list_document_sections(document: Literal["wording", "manual"]) -> str:
    """List the numbered sections of the policy wording or the claims manual."""
    return call("GET", f"/api/documents/{document}/sections")


@tool
def get_document_section(document: Literal["wording", "manual"], number: int) -> str:
    """Read one numbered section of the policy wording or the claims manual."""
    return call("GET", f"/api/documents/{document}/sections/{number}")


@tool
def decide_claim(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    explanation: str,
    policy_section: str,
) -> str:
    """Record your decision on a claim. The payout is 0 unless you approve."""
    body = {
        "decision": decision,
        "payout": payout,
        "explanation": explanation,
        "policy_section": policy_section,
    }
    return call("POST", f"/api/claims/{claim_id}/decision", body)


@tool
def pay_claim(claim_id: str, amount: float) -> str:
    """Pay an approved claim exactly its approved payout."""
    return call("POST", f"/api/claims/{claim_id}/payment", {"amount": amount})


TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    list_document_sections,
    get_document_section,
    decide_claim,
    pay_claim,
]


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


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


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Run the agent on one claim, reporting each step, and return its final reply."""
    instructions = json.loads(call("GET", "/api/instructions/claim"))["text"]
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        ModelRetryMiddleware(
            initial_delay=RETRY_DELAY, jitter=False, on_failure="error"
        ),
        ToolRetryMiddleware(initial_delay=RETRY_DELAY, jitter=False),
        ModelCallLimitMiddleware(run_limit=CALL_LIMIT),
    ]
    agent = create_agent(
        llm or model(),
        tools=TOOLS,
        system_prompt=instructions,
        middleware=middleware,
    )
    reply = ""
    for update in agent.stream(
        {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="updates",
    ):
        for node in update.values():
            for message in (node or {}).get("messages", []):
                for kind, text in steps_of(message):
                    clipped = (
                        text if len(text) <= STEP_LIMIT else text[:STEP_LIMIT] + "..."
                    )
                    call(
                        "POST",
                        f"/api/claims/{claim_id}/steps",
                        {"kind": kind, "text": clipped},
                    )
                if isinstance(message, AIMessage):
                    reply = message.text
    return reply
