import type { Metadata } from 'next';
import {
  BandChip,
  Button,
  Card,
  DerivedValue,
  EvidenceQuote,
  FamilyTag,
  HealthIndicator,
  MetricTile,
  ReplyClassTag,
  ScoreBreakdown,
  StageBadge,
  VerdictBadge,
} from '@signal/ui';
import { Band, ReplyClass, Stage, Verdict } from '@signal/contracts';
import type { Family } from '@signal/ui';
import tokens from '../../../../../design/signal-intel-ds/project/tokens.json';

export const metadata: Metadata = { title: 'Design reference' };

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="m-0 text-[15px] leading-[22px] font-semibold">{title}</h2>
      <div className="flex flex-wrap items-start gap-3">{children}</div>
    </section>
  );
}

const FAMILIES: [Family, string][] = [
  ['pool', 'Discovery'],
  ['mint', 'Judgement'],
  ['grass', 'Engagement'],
  ['mustard', 'Needs you'],
  ['lava', 'Cost and alerts'],
];

/** Living reference: every token and component, rendered from the same sources the app uses. */
export default function DesignPage() {
  return (
    <main className="si-page">
      <div className="flex flex-col gap-1">
        <span className="si-label">Design reference</span>
        <h1 className="si-h1">Signal Intel</h1>
        <p className="si-lede">
          Tokens come from design/signal-intel-ds/project/tokens.json. Components come from @signal/ui. Light only, on the
          Mixpanel guideline system; colour families identify kinds of components.
        </p>
      </div>

      <Section title="Colour">
        <div className="grid w-full grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {tokens.color.tokens.map((t) => (
            <div key={t.name} className="flex flex-col overflow-hidden rounded-md border border-line bg-surface">
              <div className="h-12 border-b border-line" style={{ background: `var(--${t.name})` }} />
              <div className="flex flex-col gap-0.5 p-2">
                <span className="font-mono text-[12px]">{t.name}</span>
                <span className="text-[12px] leading-4 text-ink-muted">{t.usage}</span>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type">
        <div className="flex w-full flex-col gap-2 rounded-md border border-line bg-surface p-4">
          {tokens.type.groups.flatMap((g) =>
            g.styles.map((s) => (
              <div key={s.name} className="flex flex-wrap items-baseline gap-3">
                <span className="si-label w-20">{s.name}</span>
                <span
                  style={{
                    fontFamily: `var(--font-${g.family})`,
                    fontSize: s.fontSize,
                    lineHeight: s.lineHeight,
                    fontWeight: s.fontWeight,
                    letterSpacing: 'letterSpacing' in s ? s.letterSpacing : undefined,
                  }}
                >
                  {s.sample}
                </span>
              </div>
            )),
          )}
        </div>
      </Section>

      <Section title="Actions">
        <Button variant="primary">Publish version</Button>
        <Button cost="≈ $42">Expand to Germany</Button>
        <Button variant="quiet">Compare versions</Button>
        <Button variant="danger">Push live</Button>
        <Button disabled>Enrich</Button>
      </Section>

      <Section title="Verdicts, bands, stages, replies">
        {Verdict.options.map((v) => (
          <VerdictBadge key={v} verdict={v} />
        ))}
        {Band.options.map((b) => (
          <BandChip key={b} band={b} reason={b === 'Excluded' ? 'Competitor' : undefined} />
        ))}
        {Stage.options.map((s) => (
          <StageBadge key={s} stage={s} />
        ))}
        {ReplyClass.options.map((r) => (
          <ReplyClassTag key={r} replyClass={r} />
        ))}
      </Section>

      <Section title="Evidence">
        <DerivedValue
          label="Verdict"
          versions="brief v14 · rubric v3 · extractor v3.2"
          explanation={<span>Rests on 2 claims from job posts, 3 days old.</span>}
        >
          <VerdictBadge verdict="Confirmed" />
        </DerivedValue>
        <EvidenceQuote claim="uses_tool = Revit" sourceType="Job post" sourceName="werkenbij.example.nl" observed="3 days ago" translated="Dutch">
          We are looking for two BIM technicians <mark>fluent in Revit</mark> to join our Rotterdam team.
        </EvidenceQuote>
        <ScoreBreakdown
          priority={82}
          band="High"
          readiness={40}
          version="brief v14 · weights v14"
          dimensions={[
            { name: 'Fit', value: 88 },
            { name: 'Evidence', value: 91 },
            { name: 'Scale', value: 64 },
            { name: 'Timing', value: 77 },
          ]}
          gates={[
            { name: 'Not excluded', hit: false },
            { name: 'Not contradicted', hit: false },
          ]}
        />
      </Section>

      <Section title="Reporting and health">
        <MetricTile label="Companies met" value="38" href="#" delta="+6 vs Oct" version="def v2" />
        <MetricTile label="Genuine replies" value="7.4 / 1k" href="#" version="def v1" incomplete />
        <div className="flex flex-col gap-2">
          <HealthIndicator name="Job-postings radar" status="ok" detail="412 records today" />
          <HealthIndicator name="Sequencer webhooks" status="silent" detail="0 events in expected 2 h window" />
          <HealthIndicator name="Derivation lag" status="degraded" detail="14 min behind" />
        </div>
      </Section>
      <section className="flex flex-col gap-3">
        <h2 className="si-h2">Colour families</h2>
        <p className="m-0 text-ink-muted">Pool = discovery · mint = judgement · grass = engagement · mustard = needs you · lava = cost and alerts.</p>
        <div className="si-tiles">
          {FAMILIES.map(([family, label]) => (
            <MetricTile key={family} family={family} label={label} value="128" href="#" delta="example" version="def v1" />
          ))}
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-4">
          {FAMILIES.map(([family, label]) => (
            <Card key={family} family={family}>
              <FamilyTag family={family}>{label}</FamilyTag>
              <h3 className="si-h2">Content card</h3>
              <p className="m-0 text-[14px]">Grey card with the family&apos;s small fade at the bottom.</p>
            </Card>
          ))}
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,360px),1fr))] gap-4">
          {(['mint', 'grass', 'pool'] as Family[]).map((family) => (
            <Card key={family} family={family} kind="feature" className="min-h-[240px]">
              <FamilyTag family={family}>Feature card</FamilyTag>
              <h3 className="si-h1 text-[24px]">The product-card gradient</h3>
              <p className="m-0">Copy sits in the light top; the colour carries the identity.</p>
              <div><Button variant="primary">Primary action</Button></div>
            </Card>
          ))}
        </div>
      </section>

      <Section title="Buttons">
        <Button variant="primary">Start sequence</Button>
        <Button>Create deal</Button>
        <Button variant="quiet">Cancel</Button>
        <Button variant="primary" size="sm">Small primary</Button>
        <Button size="sm">Small</Button>
      </Section>

      <p className="m-0 text-[12px] text-ink-muted">All figures on this page are design examples, not data.</p>
    </main>
  );
}
