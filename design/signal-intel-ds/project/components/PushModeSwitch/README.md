# PushModeSwitch

Sets how a campaign or integration writes to an external system: Off, Dry run or Live.

- **Provide:** `value` (default `dry run`) and `onChange`.
- Every new destination starts in Dry run. Live is the only segment with a fill, in `danger`, because it sends real messages.
- Switching to Live opens a confirmation that shows the dry-run diff. The switch never goes live on a single click.
