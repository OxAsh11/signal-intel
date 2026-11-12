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
    <div className="flex flex-col gap-3 rounded-md border border-line bg-surface p-6 animate-pulse">
      <p className="m-0 text-ink-muted">The AI is drafting your Brief. This takes about 10–15 seconds…</p>
      <div className="h-4 w-48 rounded bg-line" />
      <div className="h-4 w-72 rounded bg-line" />
      <div className="h-4 w-56 rounded bg-line" />
    </div>
  );
}
