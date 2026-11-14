'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export function BriefIntakeLoading({ briefId }: { briefId: string }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), 3000);
    return () => clearInterval(id);
  }, [router]);
  return (
    <section className="si-fcard si-fam-mint" aria-live="polite">
      <span className="si-ftag si-fam-mint">Drafting</span>
      <h2 className="si-h2 text-[22px]">The AI is drafting your Brief</h2>
      <p className="m-0">It writes the signal to look for, who fits, examples, sources and who to contact. About 15 seconds; this page updates by itself.</p>
      <div className="flex flex-col gap-2 animate-pulse" aria-hidden="true">
        <div className="h-3 w-2/3 rounded-xs bg-surface/70" />
        <div className="h-3 w-1/2 rounded-xs bg-surface/70" />
        <div className="h-3 w-3/5 rounded-xs bg-surface/70" />
      </div>
    </section>
  );
}
