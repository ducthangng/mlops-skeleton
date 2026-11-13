import asyncio
from collections.abc import AsyncIterator

from gateway.catalog import ModelSpec
from gateway.providers.base import ChatMessage, GenParams, Provider, StreamEvent


class MockProvider(Provider):
    """Offline provider that streams the prompt back. Handy for UI, cache and router tests."""

    delay_s = 0.02

    @property
    def available(self) -> bool:
        return True

    async def stream(
        self, model: ModelSpec, messages: list[ChatMessage], params: GenParams
    ) -> AsyncIterator[StreamEvent]:
        prompt = messages[-1].content if messages else ""
        if params.level and params.show_thinking:
            for word in f"(mock thinking at level={params.level}) The user said something; echo it.".split():
                yield StreamEvent("thinking", word + " ")
                await asyncio.sleep(self.delay_s)
        answer = (
            f"**Echo** from `{model.id}` (turn {sum(m.role == 'user' for m in messages)}, "
            f"temperature={params.temperature}, max_tokens={params.max_tokens}):\n\n{prompt}"
        )
        words = answer.split(" ")
        out = 0
        for i, word in enumerate(words):
            if out >= params.max_tokens:
                yield StreamEvent("final", input_tokens=len(prompt.split()), output_tokens=out,
                                  stop_reason="max_tokens", served_model=model.id)
                return
            yield StreamEvent("text", word if i == len(words) - 1 else word + " ")
            out += 1
            await asyncio.sleep(self.delay_s)
        yield StreamEvent(
            "final", input_tokens=len(prompt.split()), output_tokens=out, stop_reason="end_turn", served_model=model.id
        )
