# Git handoff inventory — 2026-10-14

Snapshot after fetching origin. Initial main counts are ahead / behind GitHub main.
Original branches, worktrees and stashes are retained. All recovery branches are untested.
No application tests, deployments, store uploads, DNS writes or VPS changes were performed.

## Checkouts and main

- Checkout `.`: initial local main 0 / 0; fetched GitHub main `c3795ac0e9f2f19ba0c2aa093f30a5026196d209`. No local-main rescue was needed.
  - `.`: initial branch `main`, HEAD `c3795ac0e9f2f19ba0c2aa093f30a5026196d209`.

## Original local branches

| Checkout | Branch | Initial SHA | GitHub branch | Commits unique vs GitHub main | Recovery branch |
|---|---|---|---|---|---|
| `.` | `main` | `c3795ac0e9f2f19ba0c2aa093f30a5026196d209` | same tip | 0 | `codex/handoff-2026-10-14` |

## Unmerged original Codex branches

None at the inventory boundary (recovery branches below are intentionally unmerged).

## Recovery and updated task branches

| Branch | Source | Contents / limits | Test status |
|---|---|---|---|
| `codex/handoff-2026-10-14` | `main` at `c3795ac0e9f2f19ba0c2aa093f30a5026196d209` | working-tree handoff | UNTESTED; metadata checks only |

## Stashes

- `.`: no stashes.

## Things intentionally left local

- `./.venv/`: generated dependency, cache or build output.
- `./out/`: generated dependency, cache or build output.
- `./pipeline/__pycache__/`: generated dependency, cache or build output.
- `./work/`: ignored local data/configuration; not force-added.

These exclusions are not required to inspect or continue source work. Recreate dependencies and generated assets from the checked-in commands; provision credentials separately with approval. Do not copy excluded personal records or credentials into GitHub.
