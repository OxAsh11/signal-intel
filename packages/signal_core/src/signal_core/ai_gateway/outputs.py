"""Typed output models, looked up by prompt name (used by the eval CLI)."""

from pydantic import BaseModel


class CanaryExtract(BaseModel):
    company: str
    uses_tool: bool


OUTPUT_MODELS: dict[str, type[BaseModel]] = {"canary_extract": CanaryExtract}
