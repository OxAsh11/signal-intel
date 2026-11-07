"""AI gateway errors. BudgetPaused is re-exported so callers import errors from one place."""

from signal_core.metering import BudgetPaused

__all__ = ["AiNotConfigured", "AiOutputInvalid", "AiProviderError", "BudgetPaused"]


class AiNotConfigured(Exception):
    """No key or no price for the requested capability: "Not set up yet"."""


class AiProviderError(Exception):
    """The vendor call failed (after the SDK's own retries) or was refused."""


class AiOutputInvalid(Exception):
    """The model output did not satisfy the requested schema. Never coerced."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or "invalid output")
        self.errors = errors
