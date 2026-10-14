# Handoff

Both Codex and Claude Code read this file at the start of a session and rewrite it at the end. It describes the current state. It is not a log. If a merge conflicts here, keep the newer version.

Last updated: 2026-10-14 by Claude Code, on branch `claude/ignore-credentials`.

## State

`.gitignore` now excludes `.env` files and service-account or key JSON patterns, so the GCP key that `cloud/launch_gcp.py --key` takes cannot be committed by a broad `git add`. Tracked `config/*.json` files are unaffected.

## In progress

Nothing recorded.

## Next

Nothing recorded.

## Watch out for

This repository is public. Do not write private details, credentials or internal plans in this file or anywhere else in it. Keep service-account key files outside the checkout, or name them to match the ignore patterns.
