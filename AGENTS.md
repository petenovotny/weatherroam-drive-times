# WeatherRoam drive-time table agent instructions

These instructions apply to every AI agent working here, including Codex and Claude Code.

## What this is

The scripts, router settings and inputs that produce WeatherRoam's precomputed drive-time table from OpenStreetMap data with Valhalla. Python 3.12, managed with uv.

## Commands and checks

- `uv sync` to install
- `REGION=mn ./run.sh` is the laptop smoke run (Minnesota, about 2 minutes)
- Read `METHOD.md` before changing pipeline settings

## Working with the other agent

Codex and Claude Code both work in this repository, at different times.

- Read `HANDOFF.md` before you start. Rewrite it before your last commit of the session.
- Work on a task branch named `codex/<task>` or `claude/<task>`, and merge through a pull request.
- Commit and push at the end of every session, including unfinished work. Leave no uncommitted files.
- `CLAUDE.md` imports this file with `@AGENTS.md`. Put project rules here, not there.
