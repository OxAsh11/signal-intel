# DerivedValue

Displays any value the platform computes (verdict, score, stage, label, metric) on a sunken, borderless ground with an Explain action and its version stamp.

- **Provide:** `children` (the value), `label`, `versions` ("brief v14 · rubric v3"), `explanation` (the claims, gates and inputs behind it).
- A DerivedValue never contains an input. If a person disagrees, the Explain panel offers "Record feedback", which writes `feedback` and never edits the value.
- Editable fields use a bordered input on `surface`. The difference between the sunken derived ground and the bordered input is the rule a user learns once and relies on everywhere.
