"""Deterministic provider for tests. Implements both ChatProvider and EmbedProvider."""

import hashlib
import random
from collections.abc import Callable
from typing import Any, Literal

from signal_core.ai_gateway.providers.base import Completion, Embeddings

DIMENSIONS = 1024


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        responses: dict[str, str] | Callable[[str], str] | None = None,
        tokens: tuple[int, int] = (100, 20),
        *,
        stop_reason: str = "end_turn",
    ) -> None:
        self._responses = responses if responses is not None else {}
        self._tokens = tokens
        self._stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    def _respond(self, user: str) -> str:
        if callable(self._responses):
            return self._responses(user)
        for key, text in self._responses.items():
            if key == "*" or key in user:
                return text
        raise KeyError("FakeProvider has no response for this prompt")

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        json_schema: dict[str, Any] | None,
        effort: Literal["low"] | None = None,
    ) -> Completion:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "json_schema": json_schema,
                "effort": effort,
            }
        )
        return Completion(
            text=self._respond(user),
            input_tokens=self._tokens[0],
            output_tokens=self._tokens[1],
            model=model,
            stop_reason=self._stop_reason,
        )

    async def embed(
        self, *, model: str, texts: list[str], input_type: Literal["document", "query"]
    ) -> Embeddings:
        self.calls.append({"model": model, "texts": texts, "input_type": input_type})
        vectors = []
        for text in texts:
            rng = random.Random(int.from_bytes(hashlib.sha256(text.encode()).digest(), "big"))
            vectors.append([rng.uniform(-1.0, 1.0) for _ in range(DIMENSIONS)])
        tokens = sum(max(1, len(t) // 4) for t in texts)
        return Embeddings(vectors=vectors, tokens=tokens, model=model)
