# VerdictBadge

Shows whether an entity matches a Brief's hypothesis: Confirmed, Likely, Unverified or Contradicted.

- **Provide:** `verdict`. Nothing else: the badge never takes a custom label or colour.
- The four states differ by fill, outline and glyph as well as colour (● ◐ ○ ✕), so they survive greyscale and colour-blind viewing.
- A verdict is derived. Put it inside a `DerivedValue` or next to an Explain action that opens its quotes. Never next to an edit control.
- Contradicted uses `danger` (orange). Confirmed uses `verdict-confirmed` (teal), never `positive` green, so a verdict never reads as a health status.
