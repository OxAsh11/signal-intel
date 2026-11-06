import type { Screen } from '@/lib/screens';

/** Honest empty state for a screen whose module has not shipped yet. */
export function ScreenPlaceholder({ screen }: { screen: Screen }) {
  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em] text-balance">{screen.label}</h1>
        <p className="m-0 max-w-[65ch] text-ink-muted">{screen.job}</p>
      </div>
      <section className="flex max-w-[65ch] flex-col gap-2 rounded-md border border-dashed border-line-strong bg-surface p-6">
        <span className="si-label">Not built yet</span>
        <p className="m-0">
          This screen arrives in <strong>Phase {screen.phase}</strong> with {screen.modules}. Nothing here is
          placeholder data: it stays empty until the modules behind it write real rows.
        </p>
      </section>
    </>
  );
}
