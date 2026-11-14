'use client';
import { goLive, updateBriefState } from '@/app/actions/briefs';
import { ActionForm } from './action-form';

type Brief = { id: string; state: string };

export function StateControls({ brief }: { brief: Brief }) {
  const to = (state: string) => ({ brief: brief.id, state });
  if (brief.state === 'Pilot') {
    return (
      <div className="flex gap-2 items-start">
        <ActionForm action={goLive} fields={{ brief: brief.id }} label="Go live" />
        <ActionForm
          action={updateBriefState}
          fields={to('Archived')}
          label="Archive"
          variant="danger"
        />
      </div>
    );
  }
  if (brief.state === 'Active') {
    return (
      <div className="flex gap-2 items-start">
        <ActionForm action={updateBriefState} fields={to('Paused')} label="Pause" variant="quiet" />
        <ActionForm
          action={updateBriefState}
          fields={to('Archived')}
          label="Archive"
          variant="danger"
        />
      </div>
    );
  }
  if (brief.state === 'Paused') {
    return (
      <div className="flex gap-2 items-start">
        <ActionForm action={updateBriefState} fields={to('Active')} label="Resume" />
        <ActionForm
          action={updateBriefState}
          fields={to('Archived')}
          label="Archive"
          variant="danger"
        />
      </div>
    );
  }
  return null;
}
