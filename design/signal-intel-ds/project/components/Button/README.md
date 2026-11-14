# Button

One primary button per view, for the thing the screen is for. Labels are verbs in sentence case ("Start pilot", "Draft email").

- **Provide:** `children` (the label) and `onClick`. `variant` is `primary`, `secondary` (default), `quiet` or `danger`; `size` is `md` (40px) or `sm` (28px).
- **Primary:** a #1F2023 pill (40px radius) with white text and the right-pointing chevron, added automatically.
- **Secondary:** a transparent button with a 1px #1F2023 outline and a 4px radius.
- **Quiet:** text only, underlined on hover.
- **Cost before action:** any button that spends money or AI tokens passes `cost` ("≈ $42"), and the action opens a `CostPreview` before it runs.
- **Danger** is only for irreversible actions: deleting data, writing to an external system, going live. Put it on the confirming button, never on the button that opens the dialog.
- A disabled button says why in a tooltip or helper line. A disabled button with no reason is a bug.
