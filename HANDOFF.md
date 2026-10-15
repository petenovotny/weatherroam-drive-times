# Handoff

Both Codex and Claude Code read this file at the start of a session and rewrite it at the end. It describes the current state. It is not a log. If a merge conflicts here, reconcile the newer verified state.

Last updated: 2026-10-15 by Claude Code, on branch `claude/project-thread-a6p8o9`.

## State

Claude should begin on `codex/handoff-2026-10-14`, based on fetched GitHub main `c3795ac0e9f2`, and read `HANDOFF-INVENTORY-2026-10-14.md`. That inventory covers all local clones/worktrees, branch mappings, stashes and excluded paths.
Eligible unfinished work and local branch tips have recovery commits on GitHub. Recovery branches are untested; source branches may overlap squash-merged main. No branches, worktrees, stashes or files were deleted.

Current project facts carried forward from the latest checked-in handoff (external live state was not rechecked):

`.gitignore` now covers `.env*` (except `.env.example`) and Google service-account key files. No env or key file was ever tracked in this repo's history (checked 2026-10-15).

## In progress

Nothing recorded.

- Remaining preserved branch work is listed by source branch and SHA in the inventory. All newly recovered work is UNTESTED; historical branch tests were not revalidated.

## Next

1. Read the inventory and review the appropriate recovery branch against current main; do not merge old snapshots wholesale.
2. Run the repository checks before continuing or proposing a merge. Create new implementation branches as `claude/<task>`; use `codex/<task>` for Codex work.
3. Obtain approval immediately before PR creation, merging, deployment, store/build upload, DNS, VPS, account or sharing changes. This cleanup only authorized pushes to existing GitHub remotes.

Current project follow-up carried forward:

Nothing recorded.

## Watch out for

This repository is public. Do not write private details, credentials or internal plans in this file or anywhere else in it.

- Secrets, environment files, local settings, dependencies, generated outputs, large datasets and excluded personal corpus changes remain local as listed in the inventory. Provision credentials separately; laptop keychains and local launcher paths are not part of a clone.
- Git and document integrity were checked. Application tests were not run during this archival handoff, and no release/deployment state was verified.
