# Light design system across the product

Approved by the user on 2026-11-14 ("implement this design system in whole product"). Reference mockups: https://claude.ai/artifact/Enpaa1ZYDqnmzDhQ3Aim1b ("Light system" page: Today, Account). Rules: see memory `ui-quality-and-ai-first-input`.

## System
- Mixpanel brand-guideline structure: white page (#FFFFFF), #F4F4F4 cards with #EAEAEA borders (8px radius, 24px padding), #FAFAFA table headers/insets, headlines #1F2023, body rgba(0,0,0,.70), DM Sans / DM Mono, Source Serif 4 only for quoted source text.
- Light only. No dark theme block in the generated tokens.
- Gradients identify components. Families: pool = discovery, mint = judgement, grass = engagement, mustard = needs you, lava = cost/alerts. Never purple. Feature cards use gradient-product-card-* (24px radius, 32px padding), content cards and metric tiles gradient-bg-sm-*, tags tinted (shade 02 bg, shade 09 text).
- Buttons: primary = #1F2023 pill (40/28px) with right chevron; secondary = 1px outline, 4px radius; quiet = text.
- Layout: container max 1440px, margins 48/40/32/16px at >=1440/1200/808/<808; right rail drops below at <1180px; sidebar becomes a top scroller at <860px.
- Lint: no raw Tailwind palette colours, no classes absent from the theme.

## Phases (one PR each)
1. Foundation (tokens.json, generator, components.css, fonts, shell, lint rule, fix violations) + Today + Account page. Verify with Playwright screenshots at 1920, 1366, 390 wide.
2. Brief Studio AI-first (describe -> review the draft, advanced folded) + Discovery + Review.
3. Remaining screens: Accounts list, Campaigns/Sequences, Inbox, Meetings, Reports, Settings, Brief radars/versions/pilot.
Then update the Signal Intel design-system artifact to match.
