"use client";

import { useState } from "react";
import { BandChip, VerdictBadge, StageBadge } from "@signal/ui";
import type { Band, Verdict, Stage } from "@signal/contracts";

type AccountRow = {
  id: string;
  stage: string;
  entity: { id: string; display_name: string } | null;
  score: {
    band: string;
    fit: number | null;
    evidence: number | null;
    scale: number | null;
    timing: number | null;
    priority: number | null;
    computed_at: string | null;
  } | null;
  verdict: { value: string; computed_at: string | null } | null;
  contact_point: { id: string }[];
};

const BAND_OPTIONS: Band[] = ["High", "Medium", "Low", "Excluded"];
const VERDICT_OPTIONS: Verdict[] = ["Confirmed", "Likely", "Unverified", "Contradicted"];
const STAGE_OPTIONS: Stage[] = [
  "Untouched",
  "Queued",
  "Contacted",
  "Engaged",
  "Qualified",
  "Meeting",
  "Handed off",
  "Dormant",
  "Disqualified",
];

function isBand(value: string): value is Band {
  return (BAND_OPTIONS as string[]).includes(value);
}

function isVerdict(value: string): value is Verdict {
  return (VERDICT_OPTIONS as string[]).includes(value);
}

function isStage(value: string): value is Stage {
  return (STAGE_OPTIONS as string[]).includes(value);
}

export function AccountsFilter({ accounts }: { accounts: AccountRow[] }) {
  const [selectedBands, setSelectedBands] = useState<Band[]>([]);
  const [selectedVerdicts, setSelectedVerdicts] = useState<Verdict[]>([]);
  const [selectedStages, setSelectedStages] = useState<Stage[]>([]);

  function handleMultiSelect<T extends string>(
    e: React.ChangeEvent<HTMLSelectElement>,
    guard: (v: string) => v is T,
    setter: (vals: T[]) => void,
  ) {
    const values = Array.from(e.target.selectedOptions)
      .map((o) => o.value)
      .filter(guard);
    setter(values);
  }

  const filtered = accounts.filter((row) => {
    if (selectedBands.length > 0) {
      const band = row.score?.band;
      if (!band || !selectedBands.includes(band as Band)) return false;
    }
    if (selectedVerdicts.length > 0) {
      const verdict = row.verdict?.value;
      if (!verdict || !selectedVerdicts.includes(verdict as Verdict)) return false;
    }
    if (selectedStages.length > 0) {
      if (!selectedStages.includes(row.stage as Stage)) return false;
    }
    return true;
  });

  return (
    <div>
      <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
        <label>
          Band
          <select
            multiple
            value={selectedBands}
            onChange={(e) => handleMultiSelect(e, isBand, setSelectedBands)}
          >
            {BAND_OPTIONS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>

        <label>
          Verdict
          <select
            multiple
            value={selectedVerdicts}
            onChange={(e) => handleMultiSelect(e, isVerdict, setSelectedVerdicts)}
          >
            {VERDICT_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>

        <label>
          Stage
          <select
            multiple
            value={selectedStages}
            onChange={(e) => handleMultiSelect(e, isStage, setSelectedStages)}
          >
            {STAGE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {filtered.length === 0 ? (
        <p>No accounts match the active filters.</p>
      ) : (
        <table className="si-table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Band</th>
              <th>Verdict</th>
              <th>Fit</th>
              <th>Evidence</th>
              <th>Stage</th>
              <th>Contacts</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
              <tr key={row.id}>
                <td>{row.entity?.display_name ?? row.id}</td>
                <td>
                  {row.score && isBand(row.score.band) ? (
                    <BandChip band={row.score.band} />
                  ) : null}
                </td>
                <td>
                  {row.verdict?.value && isVerdict(row.verdict.value) ? (
                    <VerdictBadge verdict={row.verdict.value} />
                  ) : null}
                </td>
                <td>{row.score?.fit ?? "—"}</td>
                <td>{row.score?.evidence ?? "—"}</td>
                <td>
                  {isStage(row.stage) ? <StageBadge stage={row.stage} /> : row.stage}
                </td>
                <td>{row.contact_point?.length ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
