from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    def bind_tools(self, tools, **kwargs):
        return self


def calls(*calls: tuple[str, dict[str, object]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": str(i)}
            for i, (name, args) in enumerate(calls)
        ],
    )


def decision(kind: str, payout: float) -> tuple[str, dict[str, object]]:
    args = {
        "decision": kind,
        "payout": payout,
        "explanation": "Covered by the wording.",
        "policy_section": "3. Trip Delay",
    }
    return ("decide_claim", args)


def stub(*replies: AIMessage) -> StubModel:
    return StubModel(messages=iter(replies))
