'use client';

import { tagAccounts } from '@/app/actions/tags';

type Tag = { id: string; name: string; colour: string };

/** Tag chips beside a name, with a per-tag remove button. */
export function AccountTags({ ws, entityId, tags }: { ws: string; entityId: string; tags: Tag[] }) {
  if (!tags.length) return null;
  const rows = tags.map((t) => (
    <li key={t.id} className="flex items-center gap-1">
      <span
        className="inline-block h-2.5 w-2.5 rounded-full"
        style={{ background: t.colour }}
        aria-hidden="true"
      />
      {t.name}
      <form action={tagAccounts} className="inline">
        <input type="hidden" name="workspace" value={ws} />
        <input type="hidden" name="tag" value={t.id} />
        <input type="hidden" name="account" value={entityId} />
        <input type="hidden" name="on" value="false" />
        <button type="submit" className="si-btn si-btn-sm si-btn-quiet" title="Remove tag">✕</button>
      </form>
    </li>
  ));
  return (
    <ul className="m-0 flex flex-wrap gap-1 pl-0 text-[12px] text-ink-muted">
      {rows}
    </ul>
  );
}