# MetricTile

Shows one canonical metric with its definition version. The whole tile links to the rows behind the number.

- **Provide:** `label` (the canonical metric name from the PRD, e.g. "Companies met"), `value`, `href` (the filtered accounts or events), `version`, `delta`, `incomplete`, and `family`.
- `family` tints the tile with its family's small gradient and colours its label: pool for pages read, mint for matches, grass for replies, deals and pipeline, lava for spend and failures. Keep a row of tiles in at most three families.
- Set `incomplete` when the period had a health incident. The tile then says so in `caution` instead of showing a delta.
- Put Companies met first on any reporting surface. Raw meeting counts are a supporting figure, never a headline.
