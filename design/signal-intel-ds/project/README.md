Signal Intel is an evidence instrument. Users open it to answer three questions: which companies match my hypothesis, how sure are we, and how do we reach them. The interface is calm, dense and exact, closer to a research terminal than a CRM. Its whole job is to make a claim and its receipt impossible to separate.

## Principles

1. **Every claim has a receipt.** A verdict, score or stage is always one click from the quotes and sources behind it. Use `EvidenceQuote` for every receipt and `DerivedValue` for every computed value.
2. **Derived looks derived.** Computed values sit on `surface-sunken` with no border and an Explain action. Anything a person can edit has a bordered input on `surface` with a `line-strong` border. Never mix the two looks.
3. **Cost before action.** Any control that spends money or AI tokens shows its estimate on the button (`Button` `cost`) and opens a `CostPreview` first.
4. **Loud failure.** Health, incomplete data and silence are shown as states (`HealthIndicator`, `MetricTile` `incomplete`), never hidden in a log.
5. **Every number links.** A figure on screen is a link to the rows behind it.

## Content fundamentals

- **Voice:** plain, exact and neutral. The platform reports what it found and how sure it is. It never sells or cheers. No exclamation marks, no emoji.
- **Person:** address the user as "you" in instructions ("You can compare any two versions"). The platform does not refer to itself as "I" or "we".
- **Casing:** sentence case for buttons, titles and menus ("Publish version"). Uppercase only for the `label` style (column headers and field labels).
- **Canonical names:** use the PRD Appendix terms exactly and capitalised as there: Brief, Brief version, Confirmed, Likely, Unverified, Contradicted, High, Medium, Low, Excluded, Untouched … Handed off, Interested, Referral, Not now. Never say "list" for a Brief, "folder" for a source, "lead" for an account, or "organisation" for a target company.
- **Numbers:** tabular mono (`value`, `metric`). Estimates carry "≈". Money shows its currency. Times are relative ("3 days ago") with the exact timestamp in a tooltip.
- **Errors and empty states** say what happened and what to do next ("No Confirmed accounts yet. The pilot found 4 Likely. Mark a few to calibrate."). An empty section always states its reason.
- **Examples of house copy:** "Expand to Germany · ≈ $186", "Rests on 2 claims", "Silent: 0 events in expected 2 h window", "Same company: merge", "Over budget: needs Admin approval".

## Visual foundations

**Colour.** The neutrals are cool and slightly blue: `paper` (page), `surface` (panels and tables), `surface-sunken` (headers, wells, derived values), `line` (hairlines) and `line-strong` (control borders). Text is `ink`, with `ink-muted` for metadata; `ink-faint` is only for icons and placeholders. There is **one accent, `signal`** (Prussian blue), used for the primary action, links, selection, focus and the High band. `signal-soft` tints selected rows. Text on a signal fill is `on-signal`.

- **Evidence colours:** `marker` and `marker-soft` mark the exact words a claim rests on, like a highlighter on a printout. They are used only inside `EvidenceQuote`.
- **Verdict colour:** `verdict-confirmed` (deep teal) for Confirmed and Likely. Contradicted uses `danger`.
- **Status colours:** `positive`, `caution` and `danger`, each with a `-soft` ground. Danger is orange, not red, and status always carries a word, glyph or shape as well, so nothing depends on red versus green.
- Every text token's usage note names the grounds it passes 4.5:1 on, in both themes.

**Type.** Three faces, each with one job:
- `Instrument Sans` (styles `display`, `title`, `heading`, `body`, `body-sm`, `label`) for everything a person operates. Body is 14px because screens are dense.
- `JetBrains Mono` (styles `metric`, `value`, `id`) for numbers, IDs and version stamps, always tabular.
- `Source Serif 4` (`quote`) is reserved for **text quoted from a source**. Seeing serif means "the outside world said this". Never set the platform's own copy in it.

**Spacing and layout.** 4px grid (`space-1` to `space-8`). Tables use `space-2` vertical by `space-3` horizontal cell padding. Panels pad at `space-4`, and sections sit `space-6` apart. Desktop gutters are `space-8`, phones `space-4`. The app shell is a left sidebar with the 11 screens, a top bar holding the workspace and Brief switchers, the ⌘K command palette and the Review count.

**Shape.** Small radii: `radius-xs` for pips and highlights, `radius-sm` for chips and inputs, `radius-md` for buttons and cards, `radius-lg` for dialogs only. Borders carry structure. `shadow-sm` lifts cards off `paper`, and `shadow-lg` is only for things that float (Explain panel, menus, dialogs, toasts).

**State.** Hover darkens a fill one step (`signal` to `signal-strong`) or tints a quiet control with `signal-soft`. Keyboard focus is always the `focus-ring` shadow: a 2px surface gap, then a solid 2px signal ring. Disabled controls use `surface-sunken` with `ink-faint` and say why.

**Motion.** Short and functional: 120–160 ms ease-out for popovers and row expansion. Nothing animates on load. Everything respects `prefers-reduced-motion`.

**Data display.** Ordered states use form as well as colour: band pips (3/2/1/none), the seven-step stage track, verdict glyphs (● ◐ ○ ✕) and health shapes (dot, diamond, square, hollow ring). Charts use `signal` for the measured series, `line` for the grid and `ink-muted` for axis text.

## Iconography

There is no icon set or logo yet. Set the name "Signal Intel" in `Instrument Sans` 600 wherever a mark would appear. In the app, use **Lucide** icons (outline, 1.5px stroke, 16px in tables, 20px in navigation), coloured with `ink-muted` and `signal` when active. This is a substitution until a brand icon set exists. Status glyphs are Unicode (● ◐ ○ ✕ ▲ ✓), never emoji.

## Components

`window.SignalIntel` (React 18): `Button`, `VerdictBadge`, `BandChip`, `StageBadge`, `ReplyClassTag`, `DerivedValue`, `EvidenceQuote`, `ScoreBreakdown`, `CostPreview`, `PushModeSwitch`, `MetricTile`, `HealthIndicator`, `ReviewQueueItem`. In the product codebase these are rebuilt as typed React components in `packages/ui` on Tailwind v4 and shadcn/ui, reading these same tokens. Each component's guidelines say what the consumer provides.
