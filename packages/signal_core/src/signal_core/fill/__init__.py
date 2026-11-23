"""Fill methods beyond P3's extract and formula columns.

A fill module never ends a run itself. It returns a :class:`FillResult`, which the column
executor writes with the same finish path it already uses for P3. Keeping that boundary
means a provider or research failure is a normal run outcome, not an unhandled worker error.
"""

from __future__ import annotations

from signal_core.fill.base import FillResult, context_for_run

__all__ = ["FillResult", "context_for_run"]
