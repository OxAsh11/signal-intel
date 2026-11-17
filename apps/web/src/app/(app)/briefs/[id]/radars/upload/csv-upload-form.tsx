'use client';
import { useActionState } from 'react';
import { Button } from '@signal/ui';
import { uploadCsvRadar } from '@/app/actions/radars';

const field = 'w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink';

/** Upload a list (one row per company or article) as a radar; the worker reads it once. */
export function CsvUploadForm({ briefId, workspaceId }: { briefId: string; workspaceId: string }) {
  const [state, action, pending] = useActionState(uploadCsvRadar, {});
  return (
    <form action={action} className="flex flex-col gap-6">
      <input type="hidden" name="brief" value={briefId} />
      <input type="hidden" name="workspace" value={workspaceId} />
      <div className="flex flex-col gap-2">
        <label htmlFor="name" className="si-label">Radar name</label>
        <input id="name" name="name" type="text" required maxLength={120} placeholder="e.g. Trade show exhibitors" className={field} />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="file" className="si-label">CSV file</label>
        <input id="file" name="file" type="file" accept=".csv,text/csv" required className="text-[14px] text-ink" />
        <span className="text-[13px] text-ink-muted">
          First row is the header. Useful columns: url, company, headline, summary. Up to 10 MB and 500 rows.
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" variant="primary" disabled={pending}>{pending ? 'Uploading…' : 'Upload and create radar'}</Button>
      </div>
      {state?.error ? (
        <p role="alert" className="m-0 text-[13px] text-danger">{state.error}</p>
      ) : null}
    </form>
  );
}
