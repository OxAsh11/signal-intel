"""Typed output models, looked up by prompt name (used by the eval CLI)."""

from pydantic import BaseModel, Field, field_validator


class CanaryExtract(BaseModel):
    company: str
    uses_tool: bool


class BriefExamples(BaseModel):
    positive: list[str]
    negative: list[str]

    @field_validator("positive")
    @classmethod
    def at_least_3_positive(cls, v: list[str]) -> list[str]:
        if len(v) < 3:
            raise ValueError("at least 3 positive examples required")
        return v

    @field_validator("negative")
    @classmethod
    def at_least_2_negative(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("at least 2 negative examples required")
        return v


class BriefSources(BaseModel):
    radar_types: list[str]

    @field_validator("radar_types")
    @classmethod
    def at_least_1(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least 1 radar type required")
        return v


class BriefIcp(BaseModel):
    verticals: list[str] = []
    geographies: list[str] = []
    size_bands: list[str] = []
    commercial_roles: list[str] = []


class BriefPersonas(BaseModel):
    buyer: list[str] = []
    user: list[str] = []
    influencer: list[str] = []


class BriefDraft(BaseModel):
    offer: str
    hypothesis: str
    examples: BriefExamples
    sources: BriefSources
    icp: BriefIcp = Field(default_factory=BriefIcp)
    personas: BriefPersonas = Field(default_factory=BriefPersonas)


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "brief_intake": BriefDraft,
    "canary_extract": CanaryExtract,
}
