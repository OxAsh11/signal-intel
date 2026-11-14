'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/** Re-renders the page every few seconds while work runs in the background. */
export function AutoRefresh({ everyMs = 3000 }: { everyMs?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), everyMs);
    return () => clearInterval(id);
  }, [router, everyMs]);
  return null;
}
