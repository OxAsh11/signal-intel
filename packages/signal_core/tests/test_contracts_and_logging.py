from typing import Any

from signal_core.contracts.enums import EventType, Stage, Verdict
from signal_core.logging import REDACTED, redact_processor


def test_enums_match_appendix() -> None:
    assert [v.value for v in Verdict] == ["Confirmed", "Likely", "Unverified", "Contradicted"]
    assert len(Stage) == 9
    assert Stage.HANDED_OFF == "Handed off"
    assert len(EventType) == 23


def test_logs_redact_personal_data() -> None:
    event: dict[str, Any] = {
        "event": "contact.verified",
        "email": "jane@example.com",
        "payload": {"phone": "+31 6 1234", "person_id": "p1", "nested": [{"token": "abc"}]},
    }
    out = redact_processor(None, "info", event)
    assert out["email"] == REDACTED
    assert out["payload"]["phone"] == REDACTED
    assert out["payload"]["person_id"] == "p1"
    assert out["payload"]["nested"][0]["token"] == REDACTED
