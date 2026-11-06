# ReviewQueueItem

Shows one human decision from the Review queue, with both candidates side by side and differing fields flagged.

- **Provide:** `kind` ("Identity match", "Verdict conflict", "Unmatched meeting"), `confidence` (0–100), `waiting` ("waiting 2 days"), `candidates` (two, each `{name, fields:[[label, value, differs]]}`).
- Values that differ between candidates are set in `caution`.
- Actions are the real choices: merge (reversible), keep separate, or record a parent/subsidiary relationship (never a merge). Each choice is stored as `feedback`.
- On phones the candidates stack, and the queue stays fully usable there.
