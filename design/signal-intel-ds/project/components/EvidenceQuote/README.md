# EvidenceQuote

Shows one claim's receipt: the quoted source text, the claim it supports, the source and its age.

- **Provide:** `children` (the quote, with the exact supporting words wrapped in `<mark>`), `claim` ("uses_tool = Revit"), `sourceType`, `sourceName`, `sourceHref` (the stored source record, never the live page), `observed`; set `expired` when the claim is past its decay date, and `translated` with the original language when the text is a translation.
- Only source text is set in the serif `quote` style. The platform's own words never use it.
- Every verdict and score explanation is built from these. A claim without a quote shows "No quote: structured source" in place of the text, never an empty block.
