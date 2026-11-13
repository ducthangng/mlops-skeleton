from collections.abc import AsyncIterator

from google import genai
from google.genai import errors, types

from gateway.catalog import ModelSpec
from gateway.providers.base import ChatMessage, GenParams, Provider, ProviderError, StreamEvent

# Gemini 2.5 takes a thinking token budget instead of a named level.
BUDGETS = {"minimal": 0, "low": 1024, "medium": 8192, "high": 24576}


class GeminiProvider(Provider):
    _client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self.cfg.resolve_api_key())
        return self._client

    async def stream(
        self, model: ModelSpec, messages: list[ChatMessage], params: GenParams
    ) -> AsyncIterator[StreamEvent]:
        contents = [
            types.Content(role="model" if m.role == "assistant" else "user", parts=[types.Part(text=m.content)])
            for m in messages
        ]
        thinking = None
        if params.level:
            if model.level_style == "budget":
                thinking = types.ThinkingConfig(
                    include_thoughts=params.show_thinking, thinking_budget=BUDGETS.get(params.level, 8192)
                )
            else:
                thinking = types.ThinkingConfig(
                    include_thoughts=params.show_thinking, thinking_level=params.level.upper()
                )
        config = types.GenerateContentConfig(
            system_instruction=params.system_prompt or None,
            temperature=params.temperature,
            top_p=params.top_p,
            max_output_tokens=params.max_tokens,
            thinking_config=thinking,
        )

        usage_in = usage_out = None
        stop_reason = None
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=model.id, contents=contents, config=config
            )
            async for chunk in stream:
                if chunk.usage_metadata:
                    usage_in = chunk.usage_metadata.prompt_token_count
                    usage_out = chunk.usage_metadata.candidates_token_count
                if not chunk.candidates:
                    continue
                cand = chunk.candidates[0]
                if cand.finish_reason:
                    stop_reason = str(cand.finish_reason.value if hasattr(cand.finish_reason, "value") else cand.finish_reason)
                for part in (cand.content.parts if cand.content and cand.content.parts else []):
                    if not part.text:
                        continue
                    yield StreamEvent("thinking" if part.thought else "text", part.text)
        except errors.APIError as e:
            raise ProviderError(
                f"Gemini API {e.code}: {e.message}", status=e.code, retryable=(e.code or 0) in (429, 500, 503)
            ) from e

        yield StreamEvent(
            "final", input_tokens=usage_in, output_tokens=usage_out, stop_reason=stop_reason, served_model=model.id
        )
