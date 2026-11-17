Signal Intel is an evidence instrument. Users open it to answer three questions: which companies match my hypothesis, how sure are we, and how do we reach them. The interface is light, calm and exact. Its whole job is to make a claim and its receipt impossible to separate.

The visual system follows the structure of the Mixpanel brand guidelines (brand.mixpanel.com, April 2026): white page, grey cards, near-black type, pill primary buttons and colour-family gradients. It is Signal Intel's own system. It never uses Mixpanel's purple, logo, illustrations or licensed fonts.

## Principles

1. **Every claim has a receipt.** A verdict, score or stage is always one click from the quotes and sources behind it. Use `EvidenceQuote` for every receipt and `DerivedValue` for every computed value.
2. **Colour names the kind of thing.** Each kind of component has one colour family, used the same way on every screen. A card's family tells you what it is before you read it, and a `FamilyTag` says it in words as well.
3. **Derived looks derived.** Computed values sit on `surface-sunken` with no border and an Explain action. Anything a person can edit has a bordered input with a `line-strong` border.
4. **Cost before action.** Any control that spends money or AI tokens shows its estimate on the button (`Button` `cost`) and opens a `CostPreview` first.
5. **Loud failure.** Health, incomplete data and silence are shown as states (`HealthIndicator`, `MetricTile` `incomplete`), never hidden in a log.
6. **Every number links.** A figure on screen is a link to the rows behind it.
7. **AI drafts, people decide.** Inputs the AI can fill are shown as the AI's draft with "Change", never as a blank form.

## Content fundamentals

- **Voice:** plain, exact and neutral. The platform reports what it found and how sure it is. It never sells or cheers. No exclamation marks, no emoji.
- **Person:** address the user as "you". The platform never calls itself "I" or "we".
- **Casing:** sentence case for buttons, titles and menus ("Start pilot"). Uppercase only for labels, column headers and eyebrows.
- **Canonical names:** use the PRD Appendix terms exactly and capitalised as there: Brief, Brief version, Confirmed, Likely, Unverified, Contradicted, High, Medium, Low, Excluded, Untouched … Handed off, Interested, Referral, Not now. Never say "list" for a Brief, "lead" for an account or "organisation" for a target company.
- **Numbers:** tabular mono. Estimates carry "≈". Money shows its currency (₹, $). Times are relative ("3 days ago"), with the exact time in a tooltip.
- **Empty states** say why the section is empty and what fills it ("No radars yet. Publishing a Brief creates them from its sources.").
- **Examples of house copy:** "9 hotel groups match your Brief. 3 are Confirmed.", "Rests on 3 claims", "Silent: 0 posts in 26 h", "Same company: merge", "Over budget: needs Admin approval".

## Visual foundations

**Colour modes.** Every surface is in one of three modes:
- **Light White:** the page (`paper`, #FFFFFF).
- **Light Gray:** cards and insets (`surface-sunken`, #F4F4F4; table headers #FAFAFA).
- **Tinted:** a family's shade 02 with its shade 09 text, used for tags.

Lines are `line` (#EAEAEA); control borders are `line-strong`. Headlines are `ink` (#1F2023), body text is `ink-body` (black at 70%), metadata is `ink-muted`. **Light only:** there is no dark theme, and the page never follows the computer's dark mode.

**Accent.** `signal` (#39729A, from the pool family) is for links, selection, focus and the High band only. Primary actions are near-black, not accent.

**Colour families and gradients.** These are the exact gradient values from the guidelines. Never purple.

| Family | Meaning | Used for |
| --- | --- | --- |
| pool | discovery | pages, radars, sources |
| mint | judgement | matches, evidence, verdicts, scores |
| grass | engagement | sequences, replies, people, next steps, deals |
| mustard | needs you | review queues, approvals |
| lava | cost and alerts | spend, failures |

- **Feature cards** use `--gradient-product-card-<family>`: 24px radius and 32px padding, with copy in the light top of the card. Use one or two per screen.
- **Content cards and metric tiles** use `--gradient-bg-sm-<family>`, a fade at the bottom of a #F4F4F4 card: 8px radius and 24px padding.
- **Tags** use the tinted mode, family shade 02 with shade 09 text.

**Evidence colours.** `marker` and `marker-soft` mark the exact words a claim rests on, only inside `EvidenceQuote`. Verdicts use `verdict-confirmed` (deep mint) for Confirmed and Likely, and `danger` for Contradicted. Status always carries a word, glyph or shape as well as colour.

**Type.**
- **DM Sans:** everything a person operates. 400 for body, 500 for headings.
- **DM Mono:** numbers, IDs, dates and version stamps. Always tabular.
- **Source Serif 4:** reserved for text quoted from a source. Seeing serif means "the outside world said this".

Garnett and Apercu Mono, the guideline fonts, are licensed, so DM Sans and DM Mono stand in.

**Spacing and layout.**
- An 8px scale.
- The page container is at most 1440px wide and centred, with side margins of 48px (wide screens), 40px, 32px and 16px (phones).
- A right-hand rail sits beside the content on wide screens and drops below it under 1180px.
- The sidebar becomes a scrolling row on phones.
- No page scrolls sideways; wide tables scroll inside their own box.

**Shape.**
- 4px: inputs and secondary buttons.
- 6px: tags and chips.
- 8px: content cards.
- 16px: dialogs.
- 24px: feature cards.
- 40px: primary buttons (pill).

Borders carry structure. Shadows are only for things that float.

**Buttons.**
- **Primary:** a #1F2023 pill with white text and a right chevron, 40px high (28px small).
- **Secondary:** a 1px outline with a 4px radius.
- **Quiet:** text only.

**Motion.** Short and functional, 120–160 ms. Nothing animates on load. Everything respects `prefers-reduced-motion`.

## Iconography

There is no icon set or logo yet. Set the name "Signal Intel" in DM Sans 500 next to a small pool-to-mint gradient dot. In the app, use Lucide outline icons (1.5px stroke). Status glyphs are Unicode (● ◐ ○ ✕ ▲ ✓), never emoji.

## Components

`window.SignalIntel` (React 18):
- **Surfaces:** `Card`, `FamilyTag`.
- **Actions:** `Button`, `CostPreview`, `PushModeSwitch`.
- **Status:** `VerdictBadge`, `BandChip`, `StageBadge`, `ReplyClassTag`, `HealthIndicator`.
- **Evidence:** `DerivedValue`, `EvidenceQuote`, `ScoreBreakdown`.
- **Reporting:** `MetricTile` (takes a `family`).
- **Review:** `ReviewQueueItem`.

In the product codebase they are typed React components in `packages/ui`, reading these same tokens. `design/signal-intel-ds/project/tokens.json` in the repo is the source the code is generated from, and a lint rule rejects colours outside the system.
