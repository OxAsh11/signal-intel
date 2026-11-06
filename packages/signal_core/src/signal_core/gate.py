"""S1 automation gate: routes every automated action by confidence and reversibility.

| Confidence | Reversible action  | Irreversible action |
|------------|--------------------|---------------------|
| >= auto    | applied, logged    | review queue        |
| review..   | review queue       | review queue        |
| < review   | digest             | digest              |

Defaults auto=90, review=40 are workspace settings (workspace_settings.gate_*_threshold).
Sending a message, writing to an external system and deleting data are irreversible.
Merges are reversible (F2).
"""

from dataclasses import dataclass

from signal_core.contracts.enums import GateOutcome


@dataclass(frozen=True, slots=True)
class Thresholds:
    auto: int = 90
    review: int = 40

    def __post_init__(self) -> None:
        if not (0 <= self.review < self.auto <= 100):
            raise ValueError(f"invalid gate thresholds: review={self.review} auto={self.auto}")


DEFAULT_THRESHOLDS = Thresholds()


def gate(
    confidence: int, reversible: bool, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> GateOutcome:
    if not 0 <= confidence <= 100:
        raise ValueError(f"confidence must be 0-100, got {confidence}")
    if confidence < thresholds.review:
        return GateOutcome.DIGEST
    if confidence >= thresholds.auto and reversible:
        return GateOutcome.APPLIED
    return GateOutcome.REVIEW_QUEUE
