/**
 * Placeholder for anything that needs an OAuth app (or other credentials) the operator has not set up
 * yet: sign-in providers now, connectors later. It says plainly that the feature is off, never fakes
 * a working control, and shows the setup steps only outside production.
 */
export function NotConfigured({
  name,
  description,
  setup,
}: {
  name: string;
  description?: string;
  /** Steps for whoever runs this deployment. Hidden in production. */
  setup?: string[];
}) {
  const showSetup = process.env.NODE_ENV !== 'production' && setup && setup.length > 0;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-dashed border-line-strong bg-surface-sunken p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">{name}</span>
        <span className="si-chip">Not set up yet</span>
      </div>
      {description ? <p className="m-0 text-[13px] text-ink-muted">{description}</p> : null}
      {showSetup ? (
        <ol className="m-0 flex flex-col gap-0.5 pl-5 text-[12px] text-ink-muted">
          {setup.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
