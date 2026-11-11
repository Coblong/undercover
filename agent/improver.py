"""The improver: an agent that proposes changes to the claim agent.

It follows a recipe: read the results, propose a change, have the change evaluated,
and improve the recipe. `HumanInTheLoopMiddleware` pauses it on the two propose
tools until a handler approves or rejects. Code, not the improver, decides whether a
version is kept, and the improver can change nothing the business owns.
"""

import json
from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    hook_config,
)
from langchain.agents.middleware.types import AgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.runtime import Runtime
from pydantic import SecretStr

from agent import improver_client as client
from agent import settings
from agent.main import OPENROUTER_BASE_URL, reply_cost

IMPROVER_MODEL = "openai/gpt-6-luna"
IMPROVER_EFFORT = "medium"
MODEL_TIMEOUT = 300
MODEL_RETRIES = 5
CALL_LIMIT = 40
EVALUATIONS = ("evaluation", "held_out", "held_out")
OUT_OF_MONEY = "the round could not afford to evaluate it"

Evaluate = Callable[[str], str]
"""Run a whole-set evaluation for the round and return the state its run ended in."""

START = "Please carry out one round of your recipe."

INSTRUCTIONS = (
    "You improve the configuration of UnderCover's claim agent, an AI agent "
    "that decides travel insurance claims.\n\n"
    "The claim agent works from its instructions, which you can change; the "
    "claim, the customer's policy and the customer's earlier claims; and tools "
    "to list and read the sections of the policy wording and the claims manual "
    "and to work out a payout. It never sees what the claims handlers decided.\n\n"
    "You are here to find a version of the claim agent that agrees with the "
    "handlers' decisions and explains them as the handlers do, costs less and "
    "runs faster, and never falls below the business objectives. A version is "
    "judged on claims you cannot see, so a change never names a claim, a "
    "customer or an amount from the results. It states a rule that holds for "
    "every claim like it.\n\n"
    "You may change the claim agent's instructions, its model and its reasoning "
    "effort, within the models allowed. Words in braces in the instructions, "
    "such as {{approval_limit}}, are filled in by the backend, so keep them as "
    "they are.\n\n"
    "A person approves each proposal before it takes effect, and may reject it "
    "with a reason. When one is rejected, read the reason and propose a better "
    "one, or stop.\n\n"
    "Your recipe says how you work. Follow it one step at a time.\n\n"
    "# Your recipe\n\n{recipe}\n"
)


class RoundStopped(Exception):
    """The round was stopped, so the improver does no more work."""


def instructions(recipe: str) -> str:
    """Wrap the recipe in the improver's instructions."""
    return INSTRUCTIONS.format(recipe=recipe)


def improver_model() -> ChatOpenAI:
    """Return the improver's model: Luna at medium, or the model in MODEL_NAME.

    Parallel tool calls are off, so a reply never holds two proposals.
    """
    name = settings.model_name()
    return ChatOpenAI(
        model=name or IMPROVER_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(settings.api_key()),
        timeout=MODEL_TIMEOUT,
        max_retries=MODEL_RETRIES,
        reasoning_effort=None if name else IMPROVER_EFFORT,
        model_kwargs={"parallel_tool_calls": False},
    )


class RoundGuard(AgentMiddleware[AgentState[Any], Any]):
    """Keeps the improver to its round.

    Before a model call it stops if the round was stopped. After one it records the
    call's cost with the round, and sends a proposal outside the models allowed back
    to the improver, so it never reaches a handler.
    """

    def __init__(self, round_id: int) -> None:
        super().__init__()
        self.round_id = round_id

    def before_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Start no model call for a round that was stopped."""
        if client.get_round(self.round_id)["state"] != "open":
            raise RoundStopped(self.round_id)
        return None

    @hook_config(can_jump_to=["model"])
    def after_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Record the call and bounce a change the models allowed do not permit."""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return None
        tokens = last.usage_metadata
        try:
            client.record_call(
                self.round_id,
                tokens["input_tokens"] if tokens else 0,
                tokens["output_tokens"] if tokens else 0,
                reply_cost(last),
            )
        except client.Refused as refused:
            if refused.round_stopped:
                raise RoundStopped(self.round_id) from refused
            raise
        allowed = {m["model"]: m["reasoning_efforts"] for m in client.models_allowed()}
        bounced = []
        for call in last.tool_calls:
            args = call["args"]
            if call["name"] == "propose_change" and args.get(
                "reasoning_effort"
            ) not in allowed.get(args.get("model"), []):
                bounced.append(
                    ToolMessage(
                        content=(
                            f"{args.get('model')} at {args.get('reasoning_effort')}"
                            " isn't allowed. Choose from the models allowed."
                        ),
                        tool_call_id=call["id"],
                        name=call["name"],
                    )
                )
        return {"messages": bounced, "jump_to": "model"} if bounced else None


def improver_tools(round_id: int, evaluate: Evaluate) -> list[BaseTool]:
    """Build the improver's four tools for one round.

    The round is fixed here, so the model has no id to change.
    """

    @tool
    def read_results() -> str:
        """Read the current version of the claim agent's configuration, its scores,
        its mistakes and weakest explanations on the evaluation set, the earlier
        rounds, the objectives, the spending left and the models allowed."""
        return json.dumps(client.improver_view(round_id), indent=2, default=str)

    @tool(parse_docstring=True)
    def propose_change(
        instructions: str, model: str, reasoning_effort: str, reason: str
    ) -> str:
        """Propose a new version of the claim agent's configuration.

        A handler reviews it before it's saved.

        Args:
            instructions: The claim agent's complete instructions
            model: One of the models allowed
            reasoning_effort: A reasoning level allowed for that model
            reason: Why this change should improve the scores
        """
        version = client.get_round(round_id)["tried_version"]
        return (
            f"Approved by a handler and saved as version {version}."
            " Run the evaluation next."
        )

    @tool
    def run_evaluation() -> str:
        """Run the newest version once on the evaluation set and twice on the
        held-out set, then keep it or roll it back."""
        current = client.get_round(round_id)
        if current["tried_version"] is None or current["verdict"] is not None:
            return "There's no new version to evaluate. Propose a change first."
        for position, eval_set in enumerate(EVALUATIONS, start=1):
            check = client.check_evaluation(round_id, eval_set)
            if not check["allowed"]:
                client.give_verdict(round_id, OUT_OF_MONEY)
                return (
                    f"{check['message']}. Version {current['tried_version']}"
                    " is rolled back."
                )
            client.record_stage(round_id, "evaluating", position, len(EVALUATIONS))
            if evaluate(eval_set) != "finished":
                client.give_verdict(round_id, "its evaluations were stopped")
                return (
                    f"An evaluation was stopped. Version {current['tried_version']}"
                    " is rolled back."
                )
        client.record_stage(round_id, "verdict", 0, 0)
        done = client.give_verdict(round_id, None)
        kept = "Kept" if done["verdict"] == "kept" else "Rolled back"
        scores = {
            "held_out_before": done["held_out_before"],
            "held_out_after": done["held_out_after"],
        }
        return (
            f"{kept} version {done['tried_version']}: {done['why']}.\n"
            f"{json.dumps(scores, indent=2)}"
        )

    @tool(parse_docstring=True)
    def propose_recipe(recipe: str, reason: str) -> str:
        """Propose a new version of your recipe for improving the claim agent.

        A handler reviews it before it's saved.

        Args:
            recipe: The complete new recipe
            reason: What this round showed, and why the new recipe should lead
                to better changes
        """
        return "Approved by a handler and saved as your new recipe."

    return [read_results, propose_change, run_evaluation, propose_recipe]


def build(
    round_id: int,
    recipe: str,
    llm: BaseChatModel,
    checkpointer: BaseCheckpointSaver[Any],
    evaluate: Evaluate,
) -> Any:
    """Build the improver for one round with a recipe as its instructions.

    The guard comes after the approval in the list, so its check of a proposal runs
    first and an invalid one is sent back before a handler is asked.
    """
    approval = HumanInTheLoopMiddleware(
        interrupt_on={
            "propose_change": {"allowed_decisions": ["approve", "reject"]},
            "propose_recipe": {"allowed_decisions": ["approve", "reject"]},
        }
    )
    middleware: list[Any] = [
        ModelCallLimitMiddleware(thread_limit=CALL_LIMIT, exit_behavior="end"),
        approval,
        RoundGuard(round_id),
    ]
    return create_agent(
        model=llm,
        tools=improver_tools(round_id, evaluate),
        system_prompt=instructions(recipe),
        middleware=middleware,
        checkpointer=checkpointer,
    )


def proposal_of(request: dict[str, Any]) -> dict[str, Any]:
    """Turn the tool call the improver is paused on into the proposal to record."""
    args = request["args"]
    if request["name"] == "propose_change":
        return {
            "kind": "change",
            "instructions": args["instructions"],
            "model": args["model"],
            "reasoning_effort": args["reasoning_effort"],
            "recipe": None,
            "reason": args["reason"],
        }
    return {
        "kind": "recipe",
        "instructions": None,
        "model": None,
        "reasoning_effort": None,
        "recipe": args["recipe"],
        "reason": args["reason"],
    }
