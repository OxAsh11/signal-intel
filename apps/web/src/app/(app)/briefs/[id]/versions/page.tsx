import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import { notFound } from 'next/navigation';
import { VersionDiff } from './version-diff';

export default async function VersionsPage({ params, searchParams }: PageProps<'/briefs/[id]/versions'>) {
  const { id } = await params;
  const sp = await searchParams;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: versions, error } = await supabase
    .from('brief_version').select('id, version, config, rubric, weights, edit_tier, change_note, published_at')
    .eq('brief_id', id).order('version');

  if (error || !versions) notFound();

  const aV = Number(Array.isArray(sp.a) ? sp.a[0] : sp.a) || null;
  const bV = Number(Array.isArray(sp.b) ? sp.b[0] : sp.b) || null;
  const vA = versions.find(v => v.version === aV) ?? versions.at(-2) ?? versions.at(0) ?? null;
  const vB = versions.find(v => v.version === bV) ?? versions.at(-1) ?? null;

  return (
    <>
      <h2 className="m-0 text-xl font-semibold">Version history</h2>
      {versions.length < 2
        ? <p className="m-0 text-ink-muted">Publish at least two versions to compare them.</p>
        : <VersionDiff versions={versions} vA={vA} vB={vB} briefId={id} />}
    </>
  );
}
