import { expect, test, type Locator, type Page } from '@playwright/test';
import { FLYTBASE_DESCRIPTION, FLYTBASE_DRAFT, newUser, onboard, signIn, sql } from './support';

/** Step 1 of Brief Studio through the real form; returns the new Brief's id. */
async function createBrief(page: Page): Promise<string> {
  await page.goto('/briefs/new');
  await page.locator('textarea[name="description"]').fill(FLYTBASE_DESCRIPTION);
  await page.locator('input[name="website"]').fill('https://flytbase.com/');
  await page.getByRole('button', { name: /Draft my Brief/ }).click();
  await page.waitForURL(/\/briefs\/[0-9a-f-]{36}$/);
  return page.url().split('/').pop()!;
}

/** Plays the worker: saves the FlytBase draft exactly as the AI intake job does, then reloads. */
async function draftBrief(page: Page, id: string): Promise<void> {
  await sql('select app.save_brief_draft($1, $2::jsonb)', [id, JSON.stringify(FLYTBASE_DRAFT)]);
  await page.reload();
  await expect(page.getByText('Drafted by AI from your description.')).toBeVisible();
}

const columnsPart = (page: Page) => page.getByRole('region', { name: 'Columns the AI fills' });
const rulesPart = (page: Page) => page.getByRole('region', { name: 'How a verdict is decided' });

/**
 * A crafted saveBriefDraft call through the page's own form: the open editor's hidden config is
 * swapped for one with a rule the database refuses (at_least on a yes/no column).
 */
async function craftInvalidRule(part: Locator): Promise<void> {
  await part.locator('input[name="config"]').evaluate((el: HTMLInputElement) => {
    const cfg = JSON.parse(el.value);
    cfg.evidence_rules = [
      { predicate: 'drone_programme', kind: 'requires', op: 'at_least', value: 1, min_sources: 1, max_age_days: null },
    ];
    el.value = JSON.stringify(cfg);
  });
}

/** The 22023 message from save_brief_draft_ui, as friendlyError shows it. */
const INVALID_RULE_MESSAGE = 'Column "drone_programme": a boolean column has no rule "at_least".';

test.describe('Brief Studio', () => {
  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('brief');
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Briefs');
  });

  test('the description, website and name reach the AI intake request', async ({ page }) => {
    const id = await createBrief(page);
    await expect(page.getByText('The AI is drafting this Brief from your description.')).toBeVisible();

    const [brief] = await sql<{ name: string; intake_status: string }>('select name, intake_status from public.brief where id = $1', [id]);
    expect(brief.intake_status).toBe('pending');
    expect(brief.name.length).toBeGreaterThan(0);

    const [event] = await sql<{ description: string }>(
      `select payload->>'description' as description from public.outbox
        where event = 'brief.intake_requested' and aggregate_id = $1`,
      [id],
    );
    expect(event.description).toContain('drone autonomy platform');
    expect(event.description).toContain('Our website: https://flytbase.com/');
  });

  test('a drafted Brief shows every part and starts a pilot', async ({ page }) => {
    const id = await createBrief(page);
    await draftBrief(page, id);

    await expect(page.getByText(FLYTBASE_DRAFT.hypothesis)).toBeVisible();
    await expect(page.getByText('Head of Security')).toBeVisible();
    await expect(columnsPart(page).getByText('Runs or plans a drone programme', { exact: true })).toBeVisible();
    await expect(columnsPart(page).getByText('Signal (decides the verdict)', { exact: true })).toBeVisible();
    await expect(
      rulesPart(page).getByText('Requires "Runs or plans a drone programme" = yes, from 1 source, in the last 730 days', { exact: true }),
    ).toBeVisible();
    await expect(rulesPart(page).getByText('Rules out when "Industry" is one of: drone vendor', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'What I assumed' }).getByText('Assumed energy includes transmission.')).toBeVisible();

    const start = page.getByRole('button', { name: 'Start pilot' });
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page.getByText('Brief · Pilot')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Pilot results' })).toBeVisible();

    const [row] = await sql<{ state: string; versions: number; radars: number }>(
      `select b.state::text as state,
              (select count(*) from public.brief_version v where v.brief_id = b.id)::int as versions,
              (select count(*) from public.radar r where r.brief_id = b.id)::int as radars
         from public.brief b where b.id = $1`,
      [id],
    );
    expect(row).toEqual({ state: 'Pilot', versions: 1, radars: expect.any(Number) });

    // Publishing the version made its columns workspace predicates.
    const preds = await sql<{ value_type: string }>(
      `select p.value_type from public.predicate p
         join public.brief b on b.workspace_id = p.workspace_id
        where b.id = $1 and p.key = 'drone_programme'`,
      [id],
    );
    expect(preds).toEqual([{ value_type: 'boolean' }]);
  });

  test('adding a column and a rule saves to the draft', async ({ page }) => {
    const id = await createBrief(page);
    await draftBrief(page, id);

    const columns = columnsPart(page);
    await columns.getByRole('button', { name: 'Change', exact: true }).click();
    await columns.getByRole('button', { name: 'Add a column', exact: true }).click();
    const column = columns.getByRole('group', { name: 'Column 4', exact: true });
    await column.getByLabel('Label', { exact: true }).fill('Hiring drone pilots');
    await expect(column.getByText('Key: hiring_drone_pilots')).toBeVisible();
    await column.getByLabel('Type', { exact: true }).selectOption('boolean');
    await column.getByLabel('Role', { exact: true }).selectOption('signal');
    await columns.getByRole('button', { name: 'Save', exact: true }).click();
    // A successful save closes the editor.
    await expect(columns.getByRole('button', { name: 'Change', exact: true })).toBeVisible();
    await expect(columns.getByText('Hiring drone pilots', { exact: true })).toBeVisible();

    const rules = rulesPart(page);
    await rules.getByRole('button', { name: 'Change', exact: true }).click();
    await rules.getByRole('button', { name: 'Add a rule', exact: true }).click();
    const rule = rules.getByRole('group', { name: 'Rule 3', exact: true });
    await rule.getByLabel('Column', { exact: true }).selectOption({ label: 'Hiring drone pilots' });
    await rule.getByLabel('Effect', { exact: true }).selectOption('requires');
    await rule.getByLabel('Test', { exact: true }).selectOption('is_true');
    await rules.getByRole('button', { name: 'Save', exact: true }).click();
    // Once the editor is closed, the sentence is the saved rule, not the editor's preview.
    await expect(rules.getByRole('button', { name: 'Change', exact: true })).toBeVisible();
    await expect(rules.getByText('Requires "Hiring drone pilots" = yes, from 1 source', { exact: true })).toBeVisible();
    await expect(columns.getByText('Hiring drone pilots', { exact: true })).toBeVisible();

    const [draft] = await sql<{ keys: string[]; rules: { predicate: string; kind: string; op: string }[] }>(
      `select array(select e ->> 'key' from jsonb_array_elements(draft_config -> 'predicates') e) as keys,
              draft_config -> 'evidence_rules' as rules
         from public.brief where id = $1`,
      [id],
    );
    expect(draft.keys).toContain('hiring_drone_pilots');
    expect(draft.rules).toContainEqual(expect.objectContaining({ predicate: 'hiring_drone_pilots', kind: 'requires', op: 'is_true' }));
  });

  test('an invalid rule is refused with the database message', async ({ page }) => {
    const id = await createBrief(page);
    await draftBrief(page, id);

    const rules = rulesPart(page);
    await rules.getByRole('button', { name: 'Change', exact: true }).click();
    await craftInvalidRule(rules);
    await rules.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(rules.getByText(INVALID_RULE_MESSAGE)).toBeVisible();

    // Nothing was saved: the draft keeps its two rules.
    const [draft] = await sql<{ n: number }>(
      `select jsonb_array_length(draft_config -> 'evidence_rules')::int as n from public.brief where id = $1`,
      [id],
    );
    expect(draft.n).toBe(2);
  });

  test('renaming a Brief', async ({ page, browser }) => {
    const id = await createBrief(page);
    await draftBrief(page, id);

    await page.getByRole('button', { name: 'Rename', exact: true }).click();
    await page.getByLabel('Brief name', { exact: true }).fill('FlytBase site operators');
    await page.getByRole('button', { name: 'Save name', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'FlytBase site operators' })).toBeVisible();

    const [brief] = await sql<{ name: string; name_auto: boolean; org_id: string; workspace_id: string }>(
      'select name, name_auto, org_id, workspace_id from public.brief where id = $1',
      [id],
    );
    expect(brief).toMatchObject({ name: 'FlytBase site operators', name_auto: false });

    // A Viewer of the same workspace sees the name but no Rename.
    const viewer = await newUser('viewer');
    const [member] = await sql<{ id: string }>(
      'insert into public.member (org_id, user_id, email) values ($1, $2, $3) returning id',
      [brief.org_id, viewer.id, viewer.email],
    );
    await sql(
      `insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, $2, $3, 'Viewer')`,
      [brief.org_id, member.id, brief.workspace_id],
    );
    const ctx = await browser.newContext();
    await signIn(ctx, viewer.email);
    const viewerPage = await ctx.newPage();
    await viewerPage.goto(`/briefs/${id}`);
    await expect(viewerPage.getByRole('heading', { level: 1, name: 'FlytBase site operators' })).toBeVisible();
    await expect(viewerPage.getByRole('button', { name: 'Rename' })).toHaveCount(0);
    await ctx.close();
  });

  test('a Brief created without a name is marked for an AI name', async ({ page }) => {
    const id = await createBrief(page);
    const [brief] = await sql<{ name_auto: boolean }>('select name_auto from public.brief where id = $1', [id]);
    expect(brief.name_auto).toBe(true);
  });

  test('a failed AI draft says so and blocks the pilot', async ({ page }) => {
    const id = await createBrief(page);
    await sql('select app.fail_brief_intake($1)', [id]);

    await page.reload();
    await expect(page.getByText('The AI draft failed.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start pilot' })).toBeDisabled();
  });
});

// The Brief page with drafted columns and rules, an open editor and an error, on desktop and phone.
// Screenshots land in test-results/brief-*.png; CI uploads them as the brief-screens artifact.
const SCREEN_WIDTHS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 390, height: 844 },
];

test.describe('Brief page screens', () => {
  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('brief-screens');
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Brief Screens');
  });

  for (const vp of SCREEN_WIDTHS) {
    test(`drafted columns, editors and an error fit ${vp.width}px`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(e.message));
      const fits = async (what: string) => {
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${what} scrolls sideways at ${vp.width}px`).toBeLessThanOrEqual(0);
      };

      const id = await createBrief(page);
      await draftBrief(page, id);
      await expect(rulesPart(page).getByText('Rules out when "Industry" is one of: drone vendor', { exact: true })).toBeVisible();
      await fits('the drafted Brief');
      await page.screenshot({ path: `test-results/brief-${vp.name}-draft.png`, fullPage: true });

      const columns = columnsPart(page);
      await columns.getByRole('button', { name: 'Change', exact: true }).click();
      await columns.getByRole('button', { name: 'Add a column', exact: true }).click();
      await expect(columns.getByRole('group', { name: 'Column 4', exact: true })).toBeVisible();
      await fits('the open Columns editor');
      await page.screenshot({ path: `test-results/brief-${vp.name}-columns-editor.png`, fullPage: true });
      await columns.getByRole('button', { name: 'Cancel', exact: true }).click();

      const rules = rulesPart(page);
      await rules.getByRole('button', { name: 'Change', exact: true }).click();
      await craftInvalidRule(rules);
      await rules.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(rules.getByText(INVALID_RULE_MESSAGE)).toBeVisible();
      await fits('the rule error');
      await page.screenshot({ path: `test-results/brief-${vp.name}-rule-error.png`, fullPage: true });

      expect(errors, 'browser errors').toEqual([]);
    });
  }
});
