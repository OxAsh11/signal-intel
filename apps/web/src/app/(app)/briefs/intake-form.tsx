'use client';
import { ActionForm, inputClass } from '@/components/forms';
import { createBrief } from '@/app/actions/briefs';
import { Button } from '@signal/ui';

export function IntakeForm({ workspaceId }: { workspaceId: string }) {
  return (
    <ActionForm action={createBrief}>
      <input type="hidden" name="workspace" value={workspaceId} />
      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">Brief name</span>
        <input name="name" required placeholder="e.g. CAD firms hiring 2026" className={inputClass} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">Describe what you're looking for</span>
        <textarea name="description" rows={5}
          placeholder="e.g. Engineering firms that design in SolidWorks or CATIA and are actively hiring mechanical engineers…"
          className={inputClass} />
        <p className="text-xs text-ink-muted">Optional — guides the AI draft. You can edit every field afterwards.</p>
      </label>
      <Button type="submit" variant="primary">Draft with AI</Button>
    </ActionForm>
  );
}
