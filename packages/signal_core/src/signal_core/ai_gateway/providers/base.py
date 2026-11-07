"""Provider protocols and result types. No vendor imports here."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = "end_turn"  # "refusal" and "max_tokens" are handled by the gateway


@dataclass(frozen=True)
class Embeddings:
    vectors: list[list[float]]
    tokens: int
    model: str


class ChatProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        json_schema: dict[str, Any] | None,
        effort: Literal["low"] | None = None,
    ) -> Completion: ...


class EmbedProvider(Protocol):
    name: str

    async def embed(
        self, *, model: str, texts: list[str], input_type: Literal["document", "query"]
    ) -> Embeddings: ...
