"""OpenAI and any OpenAI-compatible server (Ollama, vLLM, LM Studio, OpenRouter, ...)."""

from collections.abc import AsyncIterator

import openai

from gateway.catalog import ModelSpec
from gateway.providers.base import ChatMessage, GenParams, Provider, ProviderError, StreamEvent


class OpenAIProvider(Provider):
    _client: openai.AsyncOpenAI | None = None

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI(api_key=self.cfg.resolve_api_key(), base_url=self.cfg.base_url)
        return self._client

    async def stream(
        self, model: ModelSpec, messages: list[ChatMessage], params: GenParams
    ) -> AsyncIterator[StreamEvent]:
        msgs: list[dict] = []
        if params.system_prompt:
            msgs.append({"role": "system", "content": params.system_prompt})
        msgs += [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict = {"model": model.id, "messages": msgs, "stream": True}
        kwargs["max_tokens" if self.cfg.legacy_max_tokens else "max_completion_tokens"] = params.max_tokens
        if not self.cfg.legacy_max_tokens:
            kwargs["stream_options"] = {"include_usage": True}
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature
        if params.top_p is not None:
            kwargs["top_p"] = params.top_p
        if params.level:
            kwargs["reasoning_effort"] = params.level

        usage_in = usage_out = None
        stop_reason = served = None
        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                served = chunk.model or served
                if chunk.usage:
                    usage_in, usage_out = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                stop_reason = choice.finish_reason or stop_reason
                delta = choice.delta
                # Some compatible servers stream reasoning in a non-standard field.
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning and params.show_thinking:
                    yield StreamEvent("thinking", reasoning)
                if delta.content:
                    yield StreamEvent("text", delta.content)
        except openai.APIStatusError as e:
            raise ProviderError(
                f"{self.cfg.label} API {e.status_code}: {e.message}",
                status=e.status_code,
                retryable=e.status_code == 429 or e.status_code >= 500,
            ) from e
        except openai.APIConnectionError as e:
            raise ProviderError(f"Cannot reach {self.cfg.label}: {e}", retryable=True) from e

        yield StreamEvent(
            "final", input_tokens=usage_in, output_tokens=usage_out, stop_reason=stop_reason, served_model=served
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
