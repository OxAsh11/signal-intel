# Release flow: branches, worktrees and merges

How every change travels from an idea to the running app with nothing lost on the way. Agreed on 2026-11-17 after four leaks:
- a stacked PR (#5) was closed when its base branch merged;
- a commit was pushed 5 minutes after its PR merged;
- worktree folders were left behind;
- the web service did not follow `main`.

## The shape

- **`main` is the only source of truth.**
  - Railway (web and worker) deploys from it.
  - "Migrate dev" applies its migrations to the dev database.
  - Nothing reaches `main` without a pull request, green CI and the owner's merge.
- **One branch and one PR per piece of work**, named `feat/…`, `fix/…`, `chore/…` or `docs/…`.
- **One worktree per branch**, at `.claude/worktrees/<short-name>`. A worktree is a separate folder checked out on that branch. It keeps the owner's main project folder untouched, and lets two pieces of work run side by side without mixing.

## Rules

1. **Start from the latest `main`:** `git fetch` and then `git worktree add -b <branch> .claude/worktrees/<name> origin/main`.
2. **Stacking only when unavoidable.** A branch built on another unmerged branch is stated in the PR. Before the base PR is merged, retarget it to `main` (`gh pr edit <n> --base main`). Otherwise GitHub closes it when the base branch is deleted.
3. **Handover is the last thing that happens to a branch.** Claude says a PR is ready only after:
   - the final push;
   - green CI;
   - the PR description updated.

   The handover gives the exact head commit and a merge command that pins it:

   ```powershell
   & "C:\Program Files\GitHub CLI\gh.exe" pr merge <n> --repo OxAsh11/signal-intel --merge --delete-branch --match-head-commit <sha>
   ```

   `--match-head-commit` makes GitHub refuse the merge if anything was pushed after the handover.
4. **No pushes after handover.** If more work is needed once a PR has been handed over, Claude says so before the merge and hands over again with the new commit, or puts the work in a new branch and PR after the merge.
5. **After every merge, run `pnpm ship:check`.** It must end with "All clear":
   - every commit on GitHub is on `main` or in an open PR;
   - open PRs target `main`;
   - the newest migration was applied by a successful "Migrate dev" run;
   - web and worker on Railway run `main`'s newest commit (SKIPPED means the service had nothing to rebuild);
   - no finished worktrees, uncommitted edits in them, or left-over worktree folders.
6. **Clean up straight after the check:**
   - remove the worktree with `git worktree remove --force <path>`, then delete the folder if Windows left cached files behind;
   - delete the local branch (`git branch -d <branch>`).
7. **Never discard someone's uncommitted edits.** A worktree with uncommitted changes is only removed after the owner says keep (commit them in a PR) or discard.

## Who does what

| Step | Who |
| --- | --- |
| Branch, worktree, code, tests, PR, CI green, handover with pinned merge command | Claude |
| Merge | Owner |
| `pnpm ship:check`, cleanup, fixing anything it flags | Claude, right after the owner reports the merge |
| Railway service settings, keys, OAuth apps | Owner (Claude gives exact steps) |
