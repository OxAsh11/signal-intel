'use client';

import { inputClass } from './forms';

export function WorkspaceSelect({ options, value }: { options: { id: string; label: string }[]; value: string }) {
  if (options.length === 1) return <span className="truncate font-semibold">{options[0]!.label}</span>;
  return (
    <select
      name="workspace"
      aria-label="Workspace"
      defaultValue={value}
      className={`${inputClass} max-w-[60vw] truncate`}
      onChange={(e) => e.currentTarget.form?.requestSubmit()}
    >
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
