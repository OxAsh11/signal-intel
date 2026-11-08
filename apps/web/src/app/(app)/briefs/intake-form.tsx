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
      <Button type="submit" variant="primary">Draft with AI</Button>
    </ActionForm>
  );
}
