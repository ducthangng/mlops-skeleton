from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from gateway.catalog import ModelSpec, ProviderConfig


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class GenParams(BaseModel):
    """Generation parameters after they've been fitted to the chosen model."""

    max_tokens: int
    temperature: float | None = None
    top_p: float | None = None
    level: str | None = None
    system_prompt: str | None = None
    show_thinking: bool = True


@dataclass
class StreamEvent:
    """One normalized event from any provider.

    text      - a chunk of the answer
    thinking  - a chunk of the (summarized) reasoning
    final     - end of stream: usage, stop reason, and the model that actually served it
    """

    type: Literal["text", "thinking", "final"]
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    served_model: str | None = None


class ProviderError(Exception):
    """A provider call failed. `message` is safe to show in the UI."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


class Provider(ABC):
    def __init__(self, name: str, cfg: ProviderConfig):
        self.name = name
        self.cfg = cfg

    @property
    def available(self) -> bool:
        return self.cfg.resolve_api_key() is not None

    @abstractmethod
    def stream(
        self, model: ModelSpec, messages: list[ChatMessage], params: GenParams
    ) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None:
        pass
