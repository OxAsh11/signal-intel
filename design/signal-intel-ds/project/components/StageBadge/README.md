# StageBadge

Shows an account's derived stage, with its position on the seven-step path from Untouched to Handed off.

- **Provide:** `stage`; for the exits Dormant and Disqualified, `lastStage` (the stage it left from).
- Exits keep the track filled to where the account left, greyed out; Disqualified text is `danger`.
- Stage comes from activity only. The badge is never a dropdown. People move an account by logging what happened (a reply class, "Mark qualified").
