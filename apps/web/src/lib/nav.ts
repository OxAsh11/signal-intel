/**
 * The navigation model: the product read as its journey, Find → Work → Engage → Measure.
 * One tree drives the sidebar, the placeholder pages and the every-screen e2e test.
 */
export type NavGroup = 'home' | 'find' | 'work' | 'engage' | 'measure' | 'settings';

export type NavItem = {
  key: string;
  label: string;
  href: string;
  group: NavGroup;
  /** One line on what the page does (or will do, for a placeholder). */
  job: string;
  children?: NavItem[];
  /** Set on pages that are placeholders: which release brings them. */
  placeholder?: 'email' | 'crm' | 'p6';
  /** Today's working screen for this job, so a placeholder never hides a current capability. */
  todayHref?: (ws: string) => string;
  todayLabel?: string;
};

/** The serialisable part of a nav item, for the client sidebar (no functions). */
export type NavLink = { key: string; label: string; href: string; group: NavGroup; children?: NavLink[] };

export const GROUP_LABELS = { find: 'Find', work: 'Work', engage: 'Engage', measure: 'Measure' } as const;

export const NAV: NavItem[] = [
  { key: 'home', label: 'Home', href: '/', group: 'home', job: 'Where your Briefs stand and what needs you today.' },
  { key: 'briefs', label: 'Briefs', href: '/briefs', group: 'find', job: 'Describe who you sell to; pilot, calibrate and edit each Brief.' },
  { key: 'sources', label: 'Sources', href: '/sources', group: 'find', job: 'The radars each Brief runs: volume, filtering and yield.' },
  { key: 'accounts', label: 'Accounts', href: '/accounts', group: 'work', job: 'Every account found, with its columns, verdict and evidence.' },
  {
    key: 'people', label: 'People', href: '/people', group: 'work',
    job: 'The people at each account: roles, contact points and who to reach first.',
  },
  { key: 'review', label: 'Review', href: '/review', group: 'work', job: 'One queue for every human decision, ordered by how much the answer matters.' },
  {
    key: 'email', label: 'Email engine', href: '/email', group: 'engage', placeholder: 'email',
    job: 'Sequences that send from your own Gmail or Outlook, with approvals and replies in one place.',
    children: [
      {
        key: 'email-sequences', label: 'Sequences', href: '/email/sequences', group: 'engage', placeholder: 'email',
        job: 'Multi-step sequences per Brief, written from the evidence on each account.',
        todayHref: (ws) => `/workspace/${ws}/campaigns`, todayLabel: "Open today's campaigns",
      },
      {
        key: 'email-approvals', label: 'Approvals', href: '/email/approvals', group: 'engage', placeholder: 'email',
        job: 'Drafted emails waiting for a person to approve, edit or skip before they send.',
      },
      {
        key: 'email-inbox', label: 'Inbox', href: '/email/inbox', group: 'engage', placeholder: 'email',
        job: 'Every reply with its class and one-click next actions.',
        todayHref: (ws) => `/workspace/${ws}/inbox`, todayLabel: "Open today's inbox",
      },
      {
        key: 'email-mailboxes', label: 'Mailboxes', href: '/email/mailboxes', group: 'engage', placeholder: 'email',
        job: 'The Gmail and Outlook mailboxes that send, with their limits and health.',
      },
    ],
  },
  {
    key: 'crm', label: 'CRM', href: '/crm', group: 'engage', placeholder: 'crm',
    job: 'Deals by stage, meetings and tasks, inside the product.',
    children: [
      {
        key: 'crm-pipeline', label: 'Pipeline', href: '/crm/pipeline', group: 'engage', placeholder: 'crm',
        job: 'Accounts by stage on one board, from first reply to won.',
      },
      {
        key: 'crm-meetings', label: 'Meetings', href: '/crm/meetings', group: 'engage', placeholder: 'crm',
        job: 'Meetings booked and held, with their proof status and next steps.',
        todayHref: (ws) => `/workspace/${ws}/meetings`, todayLabel: "Open today's meetings",
      },
      {
        key: 'crm-tasks', label: 'Tasks', href: '/crm/tasks', group: 'engage', placeholder: 'crm',
        job: 'Tasks and follow-ups due, by owner and date.',
        todayHref: (ws) => `/workspace/${ws}/meetings`, todayLabel: "Open today's tasks",
      },
    ],
  },
  { key: 'reports', label: 'Reports', href: '/reports', group: 'measure', job: 'Funnel, cohorts and cuts by motion and segment, from one definition of every metric.' },
  { key: 'settings', label: 'Settings', href: '/settings', group: 'settings', job: 'Integrations, members and roles, budgets, tokens and health.' },
];

const flat = (items: NavItem[]): NavItem[] => items.flatMap((i) => [i, ...flat(i.children ?? [])]);

export function navItem(href: string): NavItem {
  const item = flat(NAV).find((i) => i.href === href);
  if (!item) throw new Error(`unknown nav item ${href}`);
  return item;
}

/** Every route in the tree, children included. */
export function allRoutes(): string[] {
  return flat(NAV).map((i) => i.href);
}

/** The tree without functions, safe to pass to a client component. */
export function navLinks(items: NavItem[] = NAV): NavLink[] {
  return items.map(({ key, label, href, group, children }) =>
    children ? { key, label, href, group, children: navLinks(children) } : { key, label, href, group },
  );
}

/** Today's workspace-scoped screens, read as the nav item they now live under. */
const WORKSPACE_ALIASES: Record<string, string> = {
  accounts: '/accounts',
  verdicts: '/accounts',
  reports: '/reports',
  review: '/review',
  campaigns: '/email/sequences',
  inbox: '/email/inbox',
  meetings: '/crm/meetings',
};

/** The pathname as a nav route: `/workspace/<ws>/inbox` reads as `/email/inbox`. */
export function navPath(pathname: string): string {
  const m = /^\/workspace\/[^/]+\/([^/]+)/.exec(pathname);
  return m ? (WORKSPACE_ALIASES[m[1]] ?? pathname) : pathname;
}

/** The menu an item belongs to: itself when it has sub-pages, else its parent (if any). */
export function navSection(href: string): NavItem | null {
  const item = navItem(href);
  if (item.children) return item;
  return NAV.find((p) => p.children?.some((c) => c.href === href)) ?? null;
}
