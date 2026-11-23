import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import { newUser, onboard, signIn, sql } from './support';

type Workspace = { ws: string; org: string; member: string };
type Person = { name: string; title: string; masked: string; contact: string; entity: string; flagged: boolean };
type Seeded = { account: string; company: string; people: Person[]; unlinked: string[] };

async function workspaceOf(userId: string): Promise<Workspace> {
  const [row] = await sql<Workspace>(
    `select w.id as ws, w.org_id as org, m.id as member
       from public.workspace w join public.member m on m.org_id = w.org_id
      where m.user_id = $1 order by w.created_at limit 1`,
    [userId],
  );
  return row;
}

/**
 * People are inserted the way the worker writes them: a person entity, a works_at relationship to
 * an account, a Fernet-shaped contact point with a masked hint, and (for Mike) a location flag.
 * Every id is local; no vendor, key or provider call is involved.
 */
async function seedPeople(
  w: Workspace,
  opts: { linked?: number; unlinked?: number } = {},
): Promise<Seeded> {
  const linked = opts.linked ?? 2;
  const unlinked = opts.unlinked ?? 1;
  const tag = randomUUID().replace(/-/g, '').slice(0, 10);
  const names = ['Jane Roe', 'Mike Mumbai', 'Ada CTO', 'Sam Sales', 'Pat Product'];
  const titles = ['VP Operations', 'Head of Security', 'CTO', 'Sales Lead', 'Product Lead'];
  const masks = ['j•••@acme.example', 'm•••@acme.example', 'a•••@acme.example', 's•••@acme.example', 'p•••@acme.example'];
  const linkRows = Array.from({ length: linked }, (_, i) =>
    `('p${i}', '${names[i]}', '${titles[i]}', '${masks[i]}', ${i === 1})`,
  ).join(', ');
  const unlinkRows = Array.from({ length: unlinked }, (_, i) => `('u${i}', 'Wait ${i + 1}')`).join(', ');
  const links = linkRows || "('none', '', '', '', false)";
  const unlinks = unlinkRows || "('none', '')";

  const rows = await sql<{ account: string; company: string; people: Person[]; unlinked: string[] }>(`
    begin;
    create temp table ids (k text primary key, v uuid) on commit drop;
    insert into ids values
      ('co', app.create_entity('${w.org}', 'company', 'Acme Grid ${tag}', 'tenant')),
      ('s', gen_random_uuid());

    select set_config('app.writer', 'derivation', true);
    insert into public.account (org_id, workspace_id, entity_id)
    select '${w.org}', '${w.ws}', v from ids where k = 'co';
    select set_config('app.writer', '', true);
    insert into ids
    select 'acc', id from public.account where workspace_id = '${w.ws}'
      and entity_id = (select v from ids where k = 'co');

    insert into public.source_record (id, org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status)
    values ((select v from ids where k = 's'), '${w.org}', '${w.ws}', 'people:e2e', 'directory',
            'https://people.example/${tag}', now(), 'people-${tag}', 'extracted');

    insert into ids (k, v)
    select x.k, app.create_entity('${w.org}', 'person', x.name, 'tenant')
      from (values ${links}) x(k, name, title, mask, flagged) where x.k <> 'none';
    insert into public.relationship (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence, attributes)
    select '${w.org}', i.v, 'works_at', (select v from ids where k = 'co'), (select v from ids where k = 's'), 100,
           jsonb_build_object('title', x.title)
      from (values ${links}) x(k, name, title, mask, flagged)
      join ids i on i.k = x.k where x.k <> 'none';
    insert into public.contact_point (org_id, workspace_id, entity_id, account_id, type, value_encrypted, status, provider, masked_hint, value_hash)
    select '${w.org}', '${w.ws}', i.v, (select v from ids where k = 'acc'), 'email',
           'enc:v1:e2e-${tag}-' || x.k, 'valid', 'pdl', x.mask, encode(sha256(('${tag}-' || x.k)::bytea), 'hex')
      from (values ${links}) x(k, name, title, mask, flagged)
      join ids i on i.k = x.k where x.k <> 'none';
    insert into public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
    select '${w.org}', i.v, 'profile_url', 'https://linkedin.com/in/' || lower(replace(x.name, ' ', '-')) || '-${tag}', 90,
           (select v from ids where k = 's')
      from (values ${links}) x(k, name, title, mask, flagged)
      join ids i on i.k = x.k where x.k <> 'none';
    insert into public.entity_flag (entity_id, workspace_id, kind, reason)
    select i.v, '${w.ws}', 'location_mismatch', 'Possibly a different office (Mumbai vs Delhi)'
      from (values ${links}) x(k, name, title, mask, flagged)
      join ids i on i.k = x.k where x.k <> 'none' and x.flagged;

    insert into ids (k, v)
    select x.k, app.create_entity('${w.org}', 'person', x.name, 'tenant')
      from (values ${unlinks}) x(k, name) where x.k <> 'none';

    select
      (select v from ids where k = 'acc') as account,
      (select v from ids where k = 'co') as company,
      (select coalesce(jsonb_agg(jsonb_build_object(
          'name', x.name, 'title', x.title, 'masked', x.mask, 'flagged', x.flagged,
          'contact', (select cp.id from public.contact_point cp where cp.entity_id = i.v and cp.type = 'email'),
          'entity', i.v) order by x.k), '[]'::jsonb)
         from (values ${links}) x(k, name, title, mask, flagged)
         join ids i on i.k = x.k where x.k <> 'none') as people,
      (select coalesce(jsonb_agg(i.v order by i.k), '[]'::jsonb) from ids i where i.k like 'u%') as unlinked;
    commit;
  `);
  const row = rows[0];
  if (!row) throw new Error('people seeding returned no ids');
  for (const person of (row.unlinked ?? []) as string[]) {
    await sql(`select app.queue_unlinked_person($1, $2)`, [person, w.ws]);
  }
  return {
    account: row.account,
    company: row.company,
    people: (row.people ?? []) as Person[],
    unlinked: (row.unlinked ?? []) as string[],
  };
}

const table = (page: Page) => page.locator('[data-table]');
const panel = (page: Page) => page.getByRole('dialog', { name: 'Person details' });

test.describe('People', () => {
  let userId = '';

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('people');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'People Org');
  });

  test('rows show the account, a masked email and the location flag; phone uses cards', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seedPeople(w, { linked: 2, unlinked: 1 });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/people');
    await page.waitForURL(`**/workspace/${w.ws}/people**`);

    await expect(table(page).getByRole('columnheader')).toContainText([
      'Name', 'Role', 'Account', 'Work email', 'LinkedIn', 'Location flag', 'Last touch',
    ]);
    await expect(table(page).locator('tbody tr')).toHaveCount(2);
    const jane = table(page).locator('tr[data-row="Jane Roe"]');
    await expect(jane.locator('[data-column="account"]')).toContainText('Acme Grid');
    await expect(jane.locator('[data-column="work_email"]')).toContainText('j•••@acme.example');
    const mike = table(page).locator('tr[data-row="Mike Mumbai"]');
    await expect(mike.locator('[data-column="location_flag"]')).toContainText('Possibly a different office');
    await expect(page.getByText(/1 person is waiting in Review/)).toBeVisible();
    await page.screenshot({ path: 'test-results/people-table.png', fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('[data-person-cards] li')).toHaveCount(2);
    await expect(page.locator('[data-person-cards]')).toContainText('Jane Roe');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, 'People phone view scrolls sideways').toBeLessThanOrEqual(1);
    await page.screenshot({ path: 'test-results/people-phone.png', fullPage: true });
    expect(s.people.length, 'seeded people').toBe(2);
  });

  test('an Editor reveals the full email in the panel; a Viewer sees only the mask', async ({ browser, page }) => {
    const w = await workspaceOf(userId);
    const s = await seedPeople(w, { linked: 1, unlinked: 0 });
    const jane = s.people[0];
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${w.ws}/people`);
    await table(page).locator('tr[data-row="Jane Roe"] [data-column="work_email"] button').click();

    await expect(panel(page)).toBeVisible();
    await expect(panel(page)).toContainText(jane.masked);
    await panel(page).getByRole('button', { name: 'Reveal email' }).click();
    const expected = `revealed-${jane.contact.replace(/-/g, '').slice(0, 8)}@people.test`;
    await expect(panel(page).locator('[data-person-email]')).toHaveText(expected);

    const viewer = await newUser('people-viewer');
    const [member] = await sql<{ id: string }>(
      'insert into public.member (org_id, user_id, email) values ($1, $2, $3) returning id',
      [w.org, viewer.id, viewer.email],
    );
    await sql(`insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, $2, $3, 'Viewer')`, [w.org, member.id, w.ws]);
    const ctx = await browser.newContext();
    await signIn(ctx, viewer.email);
    const vp = await ctx.newPage();
    await vp.setViewportSize({ width: 1440, height: 900 });
    await vp.goto(`/workspace/${w.ws}/people`);
    await table(vp).locator('tr[data-row="Jane Roe"] [data-column="work_email"] button').click();
    await expect(panel(vp)).toContainText(jane.masked);
    await expect(panel(vp).getByRole('button', { name: 'Reveal email' })).toHaveCount(0);
    await expect(panel(vp).locator('[data-person-email]')).toHaveCount(0);
    await ctx.close();
  });

  test('the account People tab shows the cost, queues the find and lists the people', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seedPeople(w, { linked: 0, unlinked: 0 });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${w.ws}/accounts/${s.account}?tab=people`);

    const queued = async () =>
      (await sql<{ n: number }>(
        `select count(*)::int as n from public.outbox
          where event = 'people.find_requested' and payload ->> 'account_id' = $1::text`,
        [s.account],
      ))[0].n;
    const before = await queued();
    await page.getByRole('button', { name: 'Find people' }).click();
    const dialog = page.getByRole('dialog', { name: 'Find people' });
    await expect(dialog).toContainText('5 people');
    await expect(dialog).toContainText('$1.40');
    expect(await queued(), 'the estimate queued nothing').toBe(before);
    await dialog.getByRole('button', { name: 'Confirm' }).click();
    await expect(page.getByRole('status')).toContainText(/Queued/);
    expect(await queued()).toBe(before + 1);

    // Play the worker's part: one found person linked to this account.
    await sql(`
      begin;
      with p as (select app.create_entity('${w.org}', 'person', 'Nina Found', 'tenant') as id),
           src as (select id from public.source_record where workspace_id = '${w.ws}' limit 1)
      insert into public.relationship (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence, attributes)
      select '${w.org}', p.id, 'works_at', (select entity_id from public.account where id = '${s.account}'),
             src.id, 100, '{"title": "Head of Data"}'::jsonb from p, src;
      commit;
    `);
    await page.reload();
    await expect(page.getByRole('region', { name: 'People at this account' })).toContainText('Nina Found');
    await page.screenshot({ path: 'test-results/account-people-tab.png', fullPage: true });
  });

  test('an unlinked person can be linked from Review and then appears in People', async ({ page }) => {
    const w = await workspaceOf(userId);
    await seedPeople(w, { linked: 0, unlinked: 1 });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/review');
    const card = page.locator('[data-review-item="unlinked_person"]').filter({ hasText: 'Wait 1' });
    await expect(card).toBeVisible();
    await card.getByLabel('Account').selectOption({ index: 1 });
    await card.getByRole('button', { name: 'Link' }).click();
    await expect(card).toHaveCount(0);

    await page.goto(`/workspace/${w.ws}/people`);
    await expect(table(page).locator('tr[data-row="Wait 1"]')).toBeVisible();
  });

  test('the empty People page says how many people wait in Review', async ({ page }) => {
    const w = await workspaceOf(userId);
    await seedPeople(w, { linked: 0, unlinked: 3 });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/people');
    await page.waitForURL(`**/workspace/${w.ws}/people**`);
    await expect(page.getByText('3 people are waiting in Review')).toBeVisible();
    await expect(table(page)).toHaveCount(0);
  });
});
