"""AI gateway: capabilities, typed outputs, prompt registry and budget checks (S3)."""

from signal_core.ai_gateway.errors import (
    AiNotConfigured,
    AiOutputInvalid,
    AiProviderError,
    BudgetPaused,
    PromptChanged,
)
from signal_core.ai_gateway.gateway import (
    AiGateway,
    ExtractorVersions,
    MemoryExtractorVersions,
    PgExtractorVersions,
    Result,
)
from signal_core.ai_gateway.prompts import Capability, Prompt, load_prompt, render

__all__ = [
    "AiGateway",
    "AiNotConfigured",
    "AiOutputInvalid",
    "AiProviderError",
    "BudgetPaused",
    "Capability",
    "ExtractorVersions",
    "MemoryExtractorVersions",
    "PgExtractorVersions",
    "Prompt",
    "PromptChanged",
    "Result",
    "load_prompt",
    "render",
]
