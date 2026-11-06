# CostPreview

Shows what an edit will change and cost before it runs: accounts entering and leaving, estimated spend and duration, and whether approval is needed.

- **Provide:** `tier` (Narrow, Redefine, Expand), `entering`, `leaving`, `cost`, `duration`, `needsApproval`, `budgetLeft`, `onRun`, `onCancel`.
- The tier chip uses `positive` for Narrow (free, instant, reversible), `signal` for Redefine and `caution` for Expand (real spend).
- When `needsApproval` is set, the primary action becomes "Request approval". It never runs the edit.
- No spending action skips this component. A dry-run figure that later differs from the live result is a bug to report, not a rounding note.
