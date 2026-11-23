'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { checkResult, evaluate, formulaProblem, parse, type Scalar } from '@signal/formula';
import {
  addColumn,
  estimateRun,
  loadAddColumnContext,
  loadColumnPreview,
  publishColumn,
  requestColumnApproval,
  type AddColumnContext,
  type ColumnPreview,
  type RunEstimate,
  type RunTarget,
} from '@/app/actions/columns';
import { loadColumnTemplates } from '@/app/actions/column-templates';
import { columnKeyProblem, keyFromLabel, PENDING_CHANGES, TYPE_LABELS, type ColumnSpec, type ValueType } from '@/lib/briefs';
import { formatValue } from './cell';
import { estimateText, RunOutcome, usd, useRun } from './run-confirm';
import type { Column, TableRow } from './types';

const field = 'w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[14px] text-ink';
const POLL_MS = 1500;
const POLL_GIVE_UP_MS = 5 * 60_000;
const PREVIEW_ROWS = 5;
const VALUE_TYPES = Object.keys(TYPE_LABELS) as ValueType[];
const HAS_VALUE = new Set(['value', 'derived', 'stale']);

type Kind = 'extract' | 'formula' | 'research' | 'provider';
const KINDS: { kind: Kind; label: string; hint: string }[] = [
  { kind: 'extract', label: 'Found in sources', hint: 'The AI reads the records collected for each account.' },
  { kind: 'formula', label: 'Formula', hint: 'Computed from other columns. Free, and instant.' },
  { kind: 'research', label: 'AI research', hint: 'A grounded answer with pages and a receipt. Paid per run.' },
  { kind: 'provider', label: 'Provider waterfall', hint: 'Work email on people, or a PDL company field. Paid per lookup.' },
];
const PROVIDERS = [
  { key: 'email_waterfall', label: 'Work email (waterfall)' },
  { key: 'pdl_company', label: 'PDL company field' },
  { key: 'leadmagic', label: 'LeadMagic' },
  { key: 'findymail', label: 'Findymail' },
  { key: 'prospeo', label: 'Prospeo' },
];

type Step =
  | { at: 'form' }
  | { at: 'saving' }
  | { at: 'preview'; key: string; previewId: string }
  | { at: 'draft' }
  | { at: 'published'; predicateId: string | null; fillMethod: string | null; queued: number };

const today = () => new Date().toISOString().slice(0, 10);
const typeOf = (v: Scalar): ValueType | null =>
  typeof v === 'boolean' ? 'boolean' : typeof v === 'number' ? 'number' : typeof v === 'string' ? 'text' : null;
const duration = (m: number | null) => (!m ? 'under a minute' : m < 60 ? `about ${m} min` : `about ${Math.round(m / 60)} h`);

/** The formula over the first rows in view, worked out in the browser with @signal/formula. */
function previewRows(formula: string, rows: TableRow[], columns: Column[]) {
  let node: ReturnType<typeof parse>;
  try {
    node = parse(formula);
  } catch {
    return null;
  }
  const day = today();
  return rows.slice(0, PREVIEW_ROWS).map((r) => {
    const values: Record<string, unknown> = {};
    for (const c of columns) {
      const cell = r.cells[c.id];
      if (cell && HAS_VALUE.has(cell.state)) values[c.key] = cell.value;
    }
    try {
      return { name: r.name, value: evaluate(node, values, { name: r.name, domain: r.domain }, day), error: null as string | null };
    } catch (e) {
      return { name: r.name, value: null, error: e instanceof Error ? e.message : 'The formula could not be worked out.' };
    }
  });
}

/**
 * Add column (05 §3.3): Found in sources, Formula, AI research or a paid provider waterfall. Saving
 * appends the column to the lens Brief's draft, runs P2's preview (cost and tier), publishes through
 * the existing path, then offers a 5-account paid preview, Run on 10, Run on all in view, or Save
 * without running, each with its estimate. A formula shows a live 5-row preview over the rows in
 * view. Editor+ only (the page renders nothing for a Viewer).
 */
export function AddColumn({
  ws,
  briefId,
  filter,
  rows,
  columns,
}: {
  ws: string;
  briefId: string | null;
  filter: Record<string, unknown> | null;
  rows: TableRow[];
  columns: Column[];
}) {
  const [open, setOpen] = useState(false);
  if (!briefId) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-ink-muted" data-add-column="disabled">
        <button type="button" className="si-btn si-btn-sm" disabled aria-describedby="add-column-needs-brief">
          Add column
        </button>
        <span id="add-column-needs-brief">Choose a Brief in the lens to add a column</span>
      </div>
    );
  }
  return (
    <>
      <button type="button" className="si-btn si-btn-sm" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        Add column
      </button>
      {open ? <AddColumnDrawer ws={ws} briefId={briefId} filter={filter} rows={rows} columns={columns} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function AddColumnDrawer({
  ws,
  briefId,
  filter,
  rows,
  columns,
  onClose,
}: {
  ws: string;
  briefId: string;
  filter: Record<string, unknown> | null;
  rows: TableRow[];
  columns: Column[];
  onClose: () => void;
}) {
  const [ctx, setCtx] = useState<AddColumnContext | null>(null);
  const [ctxError, setCtxError] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>('extract');
  const [label, setLabel] = useState('');
  const [type, setType] = useState<ValueType>('text');
  const [typeTouched, setTypeTouched] = useState(false);
  const [allowed, setAllowed] = useState('');
  const [hint, setHint] = useState('');
  const [formula, setFormula] = useState('');
  const [question, setQuestion] = useState('');
  const [researchInputs, setResearchInputs] = useState<string[]>([]);
  const [web, setWeb] = useState(true);
  const [maxPages, setMaxPages] = useState(2);
  const [provider, setProvider] = useState('email_waterfall');
  const [providerField, setProviderField] = useState('');
  const [step, setStep] = useState<Step>({ at: 'form' });
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ColumnPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [library, setLibrary] = useState<{ id: string; name: string; spec: unknown }[]>([]);
  const panelRef = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus();
    let live = true;
    void loadAddColumnContext(ws, briefId).then((res) => {
      if (!live) return;
      if (res.ok) setCtx(res.data);
      else setCtxError(res.error);
    });
    return () => {
      live = false;
    };
  }, [ws, briefId]);

  useEffect(() => {
    let live = true;
    void loadColumnTemplates(ws).then((res) => {
      if (live) setLibrary(res);
    });
    return () => {
      live = false;
    };
  }, [ws]);

  // The worker works the preview out; ask again until it is ready or failed.
  const previewId = step.at === 'preview' ? step.previewId : null;
  useEffect(() => {
    if (!previewId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const tick = async () => {
      const res = await loadColumnPreview(ws, briefId, previewId);
      if (!live) return;
      if (!res.ok) return setError(res.error);
      setPreview(res.data);
      if (res.data.status !== 'pending') return;
      if (Date.now() - started > POLL_GIVE_UP_MS) return setError('The preview is taking longer than usual. Close this and look at the Brief later.');
      timer = setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [ws, briefId, previewId]);

  const close = () => {
    onClose();
    returnFocus.current?.focus();
  };

  const key = keyFromLabel(label, (ctx?.specs.length ?? 0) + (ctx?.legacyKeys.length ?? 0) + 1);
  const values = allowed.split(',').map((v) => v.trim()).filter(Boolean);
  const live = useMemo(() => (kind === 'formula' && formula.trim() ? previewRows(formula, rows, columns) : null), [kind, formula, rows, columns]);
  const firstType = live?.map((r) => typeOf(r.value)).find((t) => t !== null) ?? null;
  // A formula's type follows its results until the person picks one.
  const valueType: ValueType = kind === 'formula' && !typeTouched && firstType ? firstType : type;

  const problem = (() => {
    if (!ctx) return null;
    if (!label.trim()) return 'Name the column.';
    const spec = { key, label, value_type: valueType, fill_method: kind } as ColumnSpec;
    const keyProblem = columnKeyProblem(spec, ctx.specs as ColumnSpec[], ctx.workspace.find((w) => w.key === key));
    if (keyProblem) return keyProblem;
    if (valueType === 'enum' && (values.length < 2 || values.length > 12)) return 'A list column needs 2 to 12 values, separated by commas.';
    if (kind === 'formula') return formulaProblem(formula, key, [...ctx.specs.map((s) => s.key), key], ctx.legacyKeys);
    if (kind === 'research') {
      if (question.trim().length < 10 || question.trim().length > 500) return 'Ask a question of 10 to 500 characters.';
      if (researchInputs.length > 10) return 'A research column can read at most 10 other columns.';
      if (maxPages < 1 || maxPages > 5) return 'Choose 1 to 5 pages.';
      return null;
    }
    if (kind === 'provider') {
      if (!PROVIDERS.some((p) => p.key === provider)) return 'Choose a provider.';
      if (providerField && (provider !== 'pdl_company' || !/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,4}$/.test(providerField.trim()))) {
        return 'Only PDL company columns take a dotted company field.';
      }
      return null;
    }
    return null;
  })();

  async function save() {
    setError(null);
    setStep({ at: 'saving' });
    const res = await addColumn(ws, briefId, {
      kind,
      label,
      value_type: valueType,
      allowed_values: values,
      hint,
      formula,
      question,
      research_inputs: researchInputs,
      web,
      max_pages: maxPages,
      provider,
      provider_field: providerField,
    });
    if (!res.ok) {
      setError(res.error);
      return setStep({ at: 'form' });
    }
    setPreview(null);
    setStep(res.data.previewId ? { at: 'preview', key: res.data.key, previewId: res.data.previewId } : { at: 'draft' });
  }

  async function publish() {
    if (step.at !== 'preview') return;
    setBusy(true);
    const res = await publishColumn(ws, briefId, step.key, label.trim());
    setBusy(false);
    if (!res.ok) return setError(res.error);
    setError(null);
    setStep({ at: 'published', ...res.data });
  }

  async function askApproval() {
    if (!preview) return;
    setBusy(true);
    const res = await requestColumnApproval(preview.id);
    setBusy(false);
    if (!res.ok) return setError(res.error);
    setPreview({ ...preview, approval: 'pending' });
  }

  const err = error ? (
    <p role="alert" className="m-0 text-[13px] text-danger">
      {error}
    </p>
  ) : null;

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/20" onClick={close}>
      <aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Add column"
        data-add-column-panel=""
        className="flex h-full w-[min(520px,100%)] flex-col gap-5 overflow-y-auto border-l border-line bg-surface p-5 shadow-lg outline-none"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            close();
          }
        }}
      >
        <header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="si-h2 m-0">Add column</h2>
            <p className="m-0 text-[12px] text-ink-muted">Added to the Brief in the lens, for every account.</p>
          </div>
          <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={close}>
            Close
          </button>
        </header>

        {ctxError ? <p role="alert" className="m-0 text-[13px] text-danger">{ctxError}</p> : null}
        {!ctx && !ctxError ? <p className="m-0 text-[13px] text-ink-muted">Loading…</p> : null}

        {ctx?.pending && (step.at === 'form' || step.at === 'saving') ? (
          <div className="flex flex-col gap-2 rounded-md border border-line p-3" role="alert" data-add-column-pending="">
            <p className="m-0 text-[13px] text-ink">{PENDING_CHANGES}</p>
            <Link href={`/briefs/${briefId}`} className="si-btn si-btn-sm self-start no-underline">
              Open the Brief
            </Link>
          </div>
        ) : null}

        {ctx && !ctx.pending && (step.at === 'form' || step.at === 'saving') ? (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!problem) void save();
            }}
          >
            <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
              <legend className="si-label mb-2 p-0">How it is filled</legend>
              <div className="grid grid-cols-2 gap-2 max-sm:grid-cols-1">
                {KINDS.map((k) => (
                  <label
                    key={k.kind}
                    className={`flex cursor-pointer flex-col gap-0.5 rounded-md border p-3 text-[13px] ${
                      kind === k.kind ? 'border-ink' : 'border-line hover:border-line-strong'
                    }`}
                  >
                    <span className="flex items-center gap-2 font-medium">
                      <input
                        type="radio"
                        name="kind"
                        value={k.kind}
                        checked={kind === k.kind}
                        onChange={() => setKind(k.kind)}
                        className="accent-[var(--ink)]"
                      />
                      {k.label}
                    </span>
                    <span className="text-ink-muted">{k.hint}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="flex flex-col gap-1">
              <label htmlFor="add-col-label" className="si-label">Name</label>
              <input id="add-col-label" className={field} value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} autoFocus />
              {label.trim() ? <span className="text-[12px] text-ink-muted">Key: <code className="si-mono">{key}</code></span> : null}
            </div>

            {library.length ? (
              <div className="flex flex-col gap-1">
                <label className="si-label">From library</label>
                <div className="flex flex-wrap gap-2">
                  {library.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className="si-ftag border-0 cursor-pointer text-[13px]"
                      onClick={() => {
                        const spec = (t.spec ?? {}) as Record<string, unknown>;
                        setLabel(typeof spec.label === 'string' ? spec.label : t.name);
                        setAllowed(spec.allowed_values ? String(spec.allowed_values) : '');
                      }}
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="flex flex-col gap-1">
              <label htmlFor="add-col-type" className="si-label">Value</label>
              <select
                id="add-col-type"
                className={field}
                value={valueType}
                onChange={(e) => {
                  setType(e.target.value as ValueType);
                  setTypeTouched(true);
                }}
              >
                {VALUE_TYPES.map((t) => (
                  <option key={t} value={t}>{TYPE_LABELS[t]}</option>
                ))}
              </select>
            </div>

            {valueType === 'enum' ? (
              <div className="flex flex-col gap-1">
                <label htmlFor="add-col-values" className="si-label">Allowed values</label>
                <input id="add-col-values" className={field} value={allowed} placeholder="energy, mining, ports" onChange={(e) => setAllowed(e.target.value)} />
              </div>
            ) : null}

            {kind === 'extract' ? (
              <div className="flex flex-col gap-1">
                <label htmlFor="add-col-hint" className="si-label">What to look for</label>
                <textarea id="add-col-hint" className={field} rows={2} maxLength={300} value={hint} onChange={(e) => setHint(e.target.value)} />
                <span className="text-[12px] text-ink-muted">
                  It fills from the records already collected for this Brief; the count and cost show after you save.
                </span>
              </div>
            ) : null}

            {kind === 'formula' ? (
              <div className="flex flex-col gap-2">
                <label htmlFor="add-col-formula" className="si-label">Formula</label>
                <textarea
                  id="add-col-formula"
                  className={`${field} si-mono`}
                  rows={2}
                  maxLength={500}
                  value={formula}
                  placeholder={ctx.specs[0] ? `${ctx.specs[0].key} and …` : ''}
                  onChange={(e) => setFormula(e.target.value)}
                />
                {ctx.specs.length ? (
                  <span className="text-[12px] text-ink-muted">
                    Columns: {ctx.specs.map((s) => s.key).join(' · ')}
                  </span>
                ) : null}
                <LivePreview rows={live} valueType={valueType} allowed={values} />
              </div>
            ) : null}

            {kind === 'research' ? (
              <div className="flex flex-col gap-3" data-add-column-research="">
                <div className="flex flex-col gap-1">
                  <label htmlFor="add-col-question" className="si-label">Question</label>
                  <textarea
                    id="add-col-question"
                    className={field}
                    rows={3}
                    maxLength={500}
                    value={question}
                    placeholder="What did this company announce about…?"
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                  <span className="text-[12px] text-ink-muted">{question.trim().length}/500. One grounded answer, with its pages cited.</span>
                </div>
                <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
                  <legend className="si-label mb-1 p-0">Inputs</legend>
                  {ctx.specs.length === 0 ? (
                    <span className="text-[12px] text-ink-muted">This Brief has no other columns to read yet.</span>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {ctx.specs.map((s) => (
                        <label
                          key={s.key}
                          className={`cursor-pointer rounded-full border px-2.5 py-1 text-[12px] ${
                            researchInputs.includes(s.key) ? 'border-ink bg-signal-soft text-ink' : 'border-line text-ink-muted'
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={researchInputs.includes(s.key)}
                            onChange={(e) =>
                              setResearchInputs((prev) => (e.target.checked ? [...prev, s.key] : prev.filter((k) => k !== s.key)))
                            }
                          />
                          {s.label}
                        </label>
                      ))}
                    </div>
                  )}
                </fieldset>
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex items-center gap-2 text-[13px] text-ink">
                    <input id="add-col-web" type="checkbox" className="accent-[var(--ink)]" checked={web} onChange={(e) => setWeb(e.target.checked)} />
                    Search the web
                  </label>
                  <div className="flex flex-col gap-1">
                    <label htmlFor="add-col-max-pages" className="si-label">Max pages</label>
                    <select id="add-col-max-pages" className={field} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))}>
                      {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </div>
                </div>
                <span className="text-[12px] text-ink-muted">Paid per account. You confirm a 5-account preview before anything runs.</span>
              </div>
            ) : null}

            {kind === 'provider' ? (
              <div className="flex flex-col gap-2" data-add-column-provider="">
                <div className="flex flex-col gap-1">
                  <label htmlFor="add-col-provider" className="si-label">Provider</label>
                  <select id="add-col-provider" className={field} value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {PROVIDERS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                  </select>
                </div>
                {provider === 'pdl_company' ? (
                  <div className="flex flex-col gap-1">
                    <label htmlFor="add-col-provider-field" className="si-label">Company field</label>
                    <input
                      id="add-col-provider-field"
                      className={`${field} si-mono`}
                      value={providerField}
                      placeholder="employee_count"
                      onChange={(e) => setProviderField(e.target.value)}
                    />
                    <span className="text-[12px] text-ink-muted">A dotted path such as employee_count or location.country.</span>
                  </div>
                ) : null}
                <span className="text-[12px] text-ink-muted">Every lookup, including a not-found, is listed with its cost in the cell.</span>
              </div>
            ) : null}

            {label.trim() && problem ? <p className="m-0 text-[13px] text-caution" data-add-column-problem="">{problem}</p> : null}
            {err}
            <div className="flex gap-2">
              <button type="submit" className="si-btn si-btn-primary si-btn-sm" disabled={!!problem || step.at === 'saving'}>
                {step.at === 'saving' ? 'Saving…' : 'Save and preview'}
              </button>
              <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={close}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        {step.at === 'draft' ? (
          <p role="status" className="m-0 text-[14px] text-ink">
            Added to the Brief&apos;s draft. It is collected once the Brief&apos;s pilot starts.
          </p>
        ) : null}

        {step.at === 'preview' ? (
          <section className="flex flex-col gap-3" aria-label="Preview" data-add-column-preview="">
            <p className="m-0 text-[14px] font-medium text-ink">{label.trim()}</p>
            {!preview || preview.status === 'pending' ? (
              <p className="m-0 text-[13px] text-ink-muted">Working out what this column changes and costs…</p>
            ) : preview.status === 'failed' ? (
              <p role="alert" className="m-0 text-[13px] text-danger">The preview could not be worked out. Open the Brief to try again.</p>
            ) : (
              <>
                {kind === 'extract' ? (
                  <p className="m-0 text-[14px] text-ink" data-add-column-records="">
                    Fills from {preview.records} {preview.records === 1 ? 'record' : 'records'} already collected
                  </p>
                ) : null}
                <p className="m-0 text-[14px] text-ink" data-add-column-cost="">
                  {preview.tier ? <span className="si-chip mr-2 border-line">{preview.tier}</span> : null}
                  Estimated cost {usd(preview.estimate_usd)} · {duration(preview.duration_minutes)}
                </p>
                {preview.over_budget && preview.approval !== 'approved' ? (
                  preview.approval === 'pending' ? (
                    <p className="m-0 text-[13px] text-ink-muted">Over budget: waiting for an Owner or Admin to approve.</p>
                  ) : (
                    <div className="flex flex-col gap-2">
                      <p className="m-0 text-[13px] text-caution">This edit is over budget and needs an approval.</p>
                      <div>
                        <button type="button" className="si-btn si-btn-sm" disabled={busy} onClick={() => void askApproval()}>
                          Request approval
                        </button>
                      </div>
                    </div>
                  )
                ) : (
                  <div>
                    <button type="button" className="si-btn si-btn-primary si-btn-sm" disabled={busy} onClick={() => void publish()}>
                      {busy ? 'Publishing…' : 'Publish column'}
                    </button>
                  </div>
                )}
              </>
            )}
            {err}
          </section>
        ) : null}

        {step.at === 'published' ? (
          step.predicateId ? (
            <RunChoices
              ws={ws}
              label={label.trim()}
              predicate={step.predicateId}
              briefId={briefId}
              filter={filter}
              free={step.fillMethod === 'formula'}
              paid={kind === 'research' || kind === 'provider'}
              backfilled={step.queued}
              onDone={close}
            />
          ) : (
            <p role="status" className="m-0 text-[14px] text-ink">Published. The column appears in the table.</p>
          )
        ) : null}
      </aside>
    </div>
  );
}

/** The formula's result for the first rows in view, as the cells would show it. */
function LivePreview({ rows, valueType, allowed }: { rows: ReturnType<typeof previewRows>; valueType: ValueType; allowed: string[] }) {
  if (rows === null) return null;
  return (
    <div className="rounded-md border border-line" data-formula-preview="">
      <p className="si-label m-0 border-b border-line px-3 py-1.5">Preview on the first {Math.min(PREVIEW_ROWS, rows.length)} rows</p>
      {rows.length === 0 ? <p className="m-0 px-3 py-2 text-[13px] text-ink-muted">No rows in view.</p> : null}
      <ul className="m-0 list-none p-0">
        {rows.map((r) => {
          const mismatch = r.error ? null : checkResult(r.value, valueType, allowed);
          return (
            <li key={r.name} className="flex items-center justify-between gap-3 border-b border-line px-3 py-1.5 text-[13px] last:border-b-0" data-preview-row={r.name}>
              <span className="min-w-0 truncate text-ink-muted">{r.name}</span>
              <span className={`min-w-0 truncate text-right ${r.error || mismatch ? 'text-danger' : 'text-ink'}`} data-preview-value="">
                {r.error ?? mismatch ?? (r.value === null ? 'No data' : formatValue(r.value, valueType))}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** After publishing: Run on 10, Run on all in view, Save without running, each with its estimate (mode empty). */
function RunChoices({
  ws,
  label,
  predicate,
  briefId,
  filter,
  free,
  paid,
  backfilled,
  onDone,
}: {
  ws: string;
  label: string;
  predicate: string;
  briefId: string;
  filter: Record<string, unknown> | null;
  free: boolean;
  /** Research and waterfall runs spend money: offer the explicit five-account confirm first. */
  paid: boolean;
  /** Runs publishing already queued (a formula column fills every empty cell by itself). */
  backfilled: number;
  onDone: () => void;
}) {
  const filterKey = JSON.stringify(filter);
  const target = useMemo<RunTarget>(
    () => ({ predicate, mode: 'empty', brief: briefId, filter: JSON.parse(filterKey) as Record<string, unknown> | null }),
    [predicate, briefId, filterKey],
  );
  const [estimate, setEstimate] = useState<RunEstimate | null>(null);
  const [estError, setEstError] = useState<string | null>(null);
  const [limit, setLimit] = useState<number | undefined>(undefined);
  const { outcome, busy, run } = useRun(ws);

  useEffect(() => {
    let live = true;
    void estimateRun(ws, target).then((res) => {
      if (!live) return;
      if (res.ok) setEstimate(res.data);
      else setEstError(res.error);
    });
    return () => {
      live = false;
    };
  }, [ws, target]);

  const ten = estimate ? Math.min(10, estimate.count) : 0;
  const cost = (n: number) => (free || !estimate ? 0 : n * estimate.per_entity_usd);
  const go = (n?: number) => {
    setLimit(n);
    void run(target, n);
  };

  return (
    <section className="flex flex-col gap-3" aria-label="Run the new column" data-run-choices="">
      <p role="status" className="m-0 text-[14px] text-ink">
        Published &ldquo;{label}&rdquo;. Fill it now?
      </p>
      {backfilled > 0 ? (
        <p className="m-0 text-[13px] text-ink-muted" data-run-backfilled={backfilled}>
          Publishing queued {backfilled} {backfilled === 1 ? 'run' : 'runs'}: a formula fills every empty cell by itself, for free.
        </p>
      ) : null}
      {estError ? <p role="alert" className="m-0 text-[13px] text-danger">{estError}</p> : null}
      {!estimate && !estError ? <p className="m-0 text-[13px] text-ink-muted">Estimating…</p> : null}
      {estimate && !outcome ? (
        <div className="flex flex-col gap-2">
          {paid ? (
            <button
              type="button"
              className="si-btn si-btn-primary si-btn-sm justify-between"
              disabled={busy || estimate.count === 0}
              onClick={() => go(Math.min(5, estimate.count))}
              data-run-choice="preview5"
            >
              <span>{`Preview on ${Math.min(5, estimate.count)} accounts (est. ${usd(cost(Math.min(5, estimate.count)))})`}</span>
            </button>
          ) : null}
          <button type="button" className="si-btn si-btn-sm justify-between" disabled={busy || ten === 0} onClick={() => go(10)} data-run-choice="10">
            <span>Run on 10</span>
            <span className="text-ink-muted">{estimateText(ten, cost(ten))}</span>
          </button>
          <button type="button" className="si-btn si-btn-sm justify-between" disabled={busy || estimate.count === 0} onClick={() => go()} data-run-choice="all">
            <span>Run on all in view</span>
            <span className="text-ink-muted">{estimateText(estimate.count, cost(estimate.count))}</span>
          </button>
          <button type="button" className="si-btn si-btn-quiet si-btn-sm self-start" onClick={onDone} data-run-choice="none">
            Save without running
          </button>
          {estimate.count === 0 ? (
            <p className="m-0 text-[12px] text-ink-muted">No empty cell in view without a run already queued.</p>
          ) : null}
        </div>
      ) : null}
      <RunOutcome outcome={outcome} busy={busy} onFirstN={(n) => void run(target, Math.min(n, limit ?? n))} />
      {outcome ? (
        <div>
          <button type="button" className="si-btn si-btn-sm" onClick={onDone}>
            Done
          </button>
        </div>
      ) : null}
    </section>
  );
}
