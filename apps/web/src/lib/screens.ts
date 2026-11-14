/** The screens from PRD §6.1. The account page is a detail route under Accounts. */
export type Screen = {
  href: string;
  label: string;
  job: string;
  modules: string;
  phase: 0 | 1 | 2 | 3;
  /** Sidebar group: 1 find and judge, 2 work the accounts, 3 reports and settings. */
  group: 1 | 2 | 3;
};

export const SCREENS: Screen[] = [
  { href: '/', label: 'Today', job: 'Daily briefing: key metrics, review queues waiting, accounts that moved, health alerts.', modules: 'M16 · M17 · M12', phase: 3, group: 1 },
  { href: '/briefs', label: 'Brief Studio', job: 'Create, pilot, calibrate and edit Briefs, with a cost preview before every edit.', modules: 'M1', phase: 1, group: 1 },
  { href: '/discovery', label: 'Discovery', job: 'Radars per Brief: their queries, volume, yield and processing status.', modules: 'M2 · M3', phase: 1, group: 1 },
  { href: '/accounts', label: 'Accounts', job: 'Accounts with predicate columns, saved views and segments; a board by stage; a map of sites.', modules: 'M8 · M11', phase: 2, group: 2 },
  { href: '/inbox', label: 'Inbox', job: 'Every reply with its class and one-click actions.', modules: 'M10', phase: 3, group: 2 },
  { href: '/campaigns', label: 'Campaigns', job: 'Linked campaigns by motion, push mode, health and bounce guard.', modules: 'M9', phase: 3, group: 2 },
  { href: '/meetings', label: 'Meetings and Tasks', job: 'Meetings with proof status, tasks and next steps.', modules: 'M12', phase: 3, group: 2 },
  { href: '/review', label: 'Review', job: 'One queue for every human decision, ordered by how much the answer matters.', modules: 'M4 · M5 · M10 · M12 · S1', phase: 1, group: 1 },
  { href: '/reports', label: 'Reports', job: 'Funnel, cohorts, and cuts by motion and segment, from one definition of every metric.', modules: 'M16', phase: 3, group: 3 },
  { href: '/settings', label: 'Settings', job: 'Integrations and access modes, members and roles, budgets, tokens and health.', modules: 'M13 · M14 · S6 · M17', phase: 0, group: 3 },
];

export function screen(href: string): Screen {
  const s = SCREENS.find((x) => x.href === href);
  if (!s) throw new Error(`unknown screen ${href}`);
  return s;
}
