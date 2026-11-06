import pytest

from signal_core.contracts.enums import GateOutcome
from signal_core.gate import Thresholds, gate


@pytest.mark.parametrize(
    ("confidence", "reversible", "expected"),
    [
        (95, True, GateOutcome.APPLIED),
        (90, True, GateOutcome.APPLIED),  # boundary: 90 and above is automatic
        (95, False, GateOutcome.REVIEW_QUEUE),  # irreversible never auto-applies
        (89, True, GateOutcome.REVIEW_QUEUE),
        (60, False, GateOutcome.REVIEW_QUEUE),
        (40, True, GateOutcome.REVIEW_QUEUE),  # boundary: 40 to 89 is review
        (39, True, GateOutcome.DIGEST),
        (0, False, GateOutcome.DIGEST),
    ],
)
def test_gate_matches_prd_table(confidence: int, reversible: bool, expected: GateOutcome) -> None:
    assert gate(confidence, reversible) is expected


def test_gate_uses_workspace_thresholds() -> None:
    assert gate(85, True, Thresholds(auto=80, review=30)) is GateOutcome.APPLIED
    assert gate(25, True, Thresholds(auto=80, review=30)) is GateOutcome.DIGEST


@pytest.mark.parametrize("bad", [-1, 101])
def test_gate_rejects_out_of_range_confidence(bad: int) -> None:
    with pytest.raises(ValueError):
        gate(bad, True)


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError):
        Thresholds(auto=40, review=40)
