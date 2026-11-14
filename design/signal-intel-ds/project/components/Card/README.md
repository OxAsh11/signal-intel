# Card

Groups one topic or action, and shows by its colour which kind of thing it is.

- **Provide:** `children`, `family` (`pool`, `mint`, `grass`, `mustard` or `lava`) and `kind`.
- **`kind="content"` (default):** a #F4F4F4 card with a 1px #EAEAEA border, 8px radius and 24px padding. Its family's small gradient fades in at the bottom (`--gradient-bg-sm-<family>`).
- **`kind="feature"`:** the family's product-card gradient (`--gradient-product-card-<family>`), 24px radius and 32px padding. Use one or two per screen for the thing the screen is about (Today's summary, an account's "why now", the next step). Keep the copy in the light top of the card.
- **No family:** a plain white card with a hairline, for neutral content such as activity lists or tables.
- **Families:**
  - pool: discovery (pages, radars, sources);
  - mint: judgement (matches, evidence, verdicts, scores);
  - grass: engagement (sequences, replies, people, next steps, deals);
  - mustard: needs you (review queues, approvals);
  - lava: cost and alerts.

  Use the same family for the same kind of thing everywhere. Never purple.
- Open a family card with a `FamilyTag` that names it in words, so the meaning never rests on colour alone.
- Avoid nesting cards inside cards.
