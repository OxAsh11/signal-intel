'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

type Step = { step: string; status: 'pending' | 'running' | 'done' | 'failed'; detail?: string };

const LABELS: Record<string, string> = {
  reading_site: 'Reading the website',
  drafting_icp: 'Drafting who fits',
  drafting_columns: 'Drafting the columns',
  drafting_rubric: 'Drafting the rubric',
  choosing_sources: 'Choosing where to look',
  done: 'Done',
};

export function BriefIntakeLoading({ briefId, steps }: { briefId: string; steps: Step[] }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), 3000);
    return () => clearInterval(id);
  }, [router]);
  const list: Step[] = steps.length ? steps : Object.keys(LABELS).map((s) => ({ step: s, status: 'pending' as const, detail: '' }));
  return (
    <section className="si-fcard" aria-live="polite">
      <span className="si-ftag">Drafting</span>
      <h2 className="si-h2">The AI is drafting your Brief</h2>
      <p className="m-0">It writes the signal to look for, who fits, examples, sources and who to contact. About 15 seconds; this page updates by itself.</p>
      <ol className="m-0 flex flex-col gap-2 p-0">
        {list.map((s) => (
          <li key={s.step} className="flex items-center gap-2 text-[13px]">
            <span aria-hidden="true">
              {s.status === 'done' ? '✓' : s.status === 'failed' ? '✗' : s.status === 'running' ? '…' : '○'}
            </span>
            <span className={s.status === 'done' ? 'text-ink' : 'text-ink-muted'}>
              {LABELS[s.step] ?? s.step}
            </span>
            {s.detail ? <span className="text-[12px] text-ink-muted">· {s.detail}</span> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}