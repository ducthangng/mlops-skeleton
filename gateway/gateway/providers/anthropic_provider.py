from collections.abc import AsyncIterator

import anthropic

from gateway.catalog import ModelSpec
from gateway.providers.base import ChatMessage, GenParams, Provider, ProviderError, StreamEvent

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(Provider):
    _client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self.cfg.resolve_api_key(), base_url=self.cfg.base_url
            )
        return self._client

    async def stream(
        self, model: ModelSpec, messages: list[ChatMessage], params: GenParams
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict = {
            "model": model.id,
            "max_tokens": params.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            # Current Claude models think adaptively; "summarized" makes the reasoning readable.
            "thinking": {"type": "adaptive", "display": "summarized" if params.show_thinking else "omitted"},
        }
        if params.system_prompt:
            kwargs["system"] = params.system_prompt
        if params.level:
            kwargs["output_config"] = {"effort": params.level}

        try:
            if model.refusal_fallback:
                # On a safety refusal the API re-runs the request on a fallback model in the same call.
                manager = self.client.beta.messages.stream(
                    betas=[FALLBACK_BETA], fallbacks=model.refusal_fallback, **kwargs
                )
            else:
                manager = self.client.messages.stream(**kwargs)

            async with manager as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield StreamEvent("text", event.delta.text)
                        elif event.delta.type == "thinking_delta" and event.delta.thinking:
                            yield StreamEvent("thinking", event.delta.thinking)
                final = await stream.get_final_message()
        except anthropic.APIStatusError as e:
            raise ProviderError(
                f"Anthropic API {e.status_code}: {e.message}",
                status=e.status_code,
                retryable=e.status_code == 429 or e.status_code >= 500,
            ) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"Cannot reach Anthropic API: {e}", retryable=True) from e

        yield StreamEvent(
            "final",
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            stop_reason=final.stop_reason,
            served_model=final.model,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
