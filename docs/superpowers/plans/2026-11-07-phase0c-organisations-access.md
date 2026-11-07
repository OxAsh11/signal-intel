# Phase 0c — Organisations and Access (M14) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user signs in, creates an organisation or accepts an invitation, and Admins manage members, roles, allowed domains and teams. Every change goes through an audited SQL function that enforces the PRD permission matrix.

**Architecture:** Migration 0011 adds `public.*` SECURITY DEFINER write functions. They check the caller's role with the existing `app.has_org_role` and `app.has_workspace_role` helpers and write `audit_log` in the same transaction. The web app calls them through server actions (`supabase.rpc`) and refreshes with `refresh()`. All session reads sit inside `<Suspense>` (Cache Components).

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` (PRD M14)

## Global Constraints

- Permission matrix (M14): only Owner/Admin manage members, roles, teams, invitations and allowed domains. Only an Owner can grant or remove the Owner role. Editor/Operator/Viewer cannot reach any of these paths.
- At least one active Owner always exists per organisation.
- Owner/Admin are organisation roles; Editor/Operator/Viewer are per workspace (existing CHECK).
- An invitation is accepted only by a signed-in user whose verified email equals the invited email (case-insensitive), before expiry (7 days), once. When `allowed_email_domains` is non-empty, the email's domain must be in it.
- The invitation token is returned once. Only its SHA-256 hash is stored.
- Removing a member sets `status = 'removed'` and deletes their role assignments. Access ends immediately because every RLS helper checks active membership. API token revocation joins this function when `api_token` lands (M13).
- Every function writes `audit_log` (M18).
- Copy follows the design system README: sentence case, plain, no exclamation marks.

## Review Focus

1. An Admin tries to make someone an Owner. Expect: refused. Test: `admin cannot grant Owner`.
2. The last Owner is demoted or removed. Expect: refused. Test: `last owner cannot be removed`.
3. Someone else signs in and opens a forwarded invitation link. Expect: refused. Test: `invitation accepted only by its email`.
4. A removed member loses access immediately. Test: `removed member sees nothing`.
5. An invitee's domain is outside the allowed list. Expect: refused at accept. Test: `domain outside allowed list refused`.

---

### Task 1: Migration 0011
`invite_member`, `revoke_invitation`, `accept_invitation(token)`, `my_invitations()`, `accept_my_invitation(id)`, `set_member_role`, `remove_role`, `remove_member`, `set_allowed_domains`, `create_team`, `set_team_member`; helper `app.assert_has_owner(org)`.

### Task 2: Tests
Review Focus cases plus happy paths in `scripts/check-migrations.mjs` and the pgTAP file `05_access.test.sql`.

### Task 3: Web context and onboarding
`lib/org.ts` (`getOrgContext()`: memberships, the current org/workspace from cookie `si_ws` or the first, and the caller's roles). A `MembershipGate` in the shell redirects users with no membership to `/onboarding`. `/onboarding` (create organisation, pending invitations). `/invite/[token]` (accept). A workspace switcher in the top bar.

### Task 4: Settings → Members and access
Members table with org and workspace roles; invite form (shows the link once); pending invitations with revoke; change role; remove; allowed domains. Read-only for non-Admins.

### Task 5: Verify
All checks pass; check in the browser with the Supabase env present (signed-out redirect, login page renders providers).
