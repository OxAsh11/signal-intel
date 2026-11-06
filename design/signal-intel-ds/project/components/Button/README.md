# Button

One primary button per view, for the thing the screen is for. Labels are verbs in sentence case ("Publish version", "Push to sequencer").

- **Provide:** `children` (the label), `onClick`; `variant` is `primary`, `secondary` (default), `quiet` or `danger`; `size` is `md` or `sm`.
- **Cost before action:** any button that spends money or AI tokens passes `cost` ("≈ $42"), and the action opens a `CostPreview` before it runs.
- **Danger** is only for irreversible actions: deleting data, writing to an external system, going live. Use the fill on the confirming button, never on the button that opens the dialog.
- Disabled buttons say why in a tooltip or helper line. A disabled button with no reason is a bug.
