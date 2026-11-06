"""Structured JSON logging with tenant tags and personal-data redaction (S7)."""

import logging
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

# Keys whose values never reach the logs (PRD S7: redaction excludes personal data).
REDACTED_KEYS = frozenset(
    {
        "email",
        "phone",
        "contact_value",
        "value_encrypted",
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "access_token",
        "refresh_token",
        "service_role_key",
    }
)
REDACTED = "[redacted]"


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if str(k).lower() in REDACTED_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "info", *, json: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
