"""Voyage embeddings adapter, 1024 dimensions to match memory_chunk.embedding vector(1024)."""

from typing import Any, Literal

from pydantic import SecretStr
from voyageai.client_async import AsyncClient
from voyageai.error import VoyageError

from signal_core.ai_gateway.errors import AiProviderError
from signal_core.ai_gateway.providers.base import Embeddings

DIMENSIONS = 1024
# voyageai's max_retries counts attempts (stop_after_attempt), so 3 means up to 2 retries.
MAX_ATTEMPTS = 3


class VoyageProvider:
    name = "voyage"

    def __init__(self, api_key: SecretStr, *, client: Any = None) -> None:
        self._client: Any = client or AsyncClient(
            api_key=api_key.get_secret_value(), max_retries=MAX_ATTEMPTS
        )

    async def embed(
        self, *, model: str, texts: list[str], input_type: Literal["document", "query"]
    ) -> Embeddings:
        try:
            result = await self._client.embed(
                texts, model=model, input_type=input_type, output_dimension=DIMENSIONS
            )
        except VoyageError as exc:
            raise AiProviderError(f"voyage error: {type(exc).__name__}") from None
        vectors = [[float(x) for x in v] for v in result.embeddings]
        return Embeddings(vectors=vectors, tokens=int(result.total_tokens), model=model)
