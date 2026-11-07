import { switchWorkspace } from '@/app/actions/access';
import { getOrgContext } from '@/lib/org';
import { WorkspaceSelect } from './workspace-select';

/** Organisation · workspace context for every screen. */
export async function WorkspaceSwitcher() {
  const ctx = await getOrgContext();
  if (!ctx?.workspace) return <span className="si-label">No workspace</span>;
  const options = ctx.workspaces.map((w) => ({
    id: w.id,
    label: `${ctx.orgs.find((o) => o.id === w.orgId)?.name ?? 'Organisation'} · ${w.name}`,
  }));
  return (
    <form action={switchWorkspace} className="flex min-w-0 items-center gap-2">
      <span className="si-label hidden sm:inline">Workspace</span>
      <WorkspaceSelect options={options} value={ctx.workspace.id} />
      {ctx.workspaceRole ? <span className="si-chip si-band">{ctx.workspaceRole}</span> : null}
    </form>
  );
}
