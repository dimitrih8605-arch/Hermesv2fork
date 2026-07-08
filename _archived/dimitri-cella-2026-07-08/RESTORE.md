# Cella Profile — Restoration Guide

Archived: 2026-07-08
Archive path: `~/.hermes/profiles/_archived/dimitri-cella-2026-07-08/`

## To restore:

1. `mv ~/.hermes/profiles/_archived/dimitri-cella-2026-07-08 ~/.hermes/profiles/dimitri-cella`
2. Add `"cella-grow-bridge"` to cron (script at `~/.local/bin/_archived-cella-grow-bridge.sh` → restore as `cella-grow-bridge.sh`)
3. Update `~/.hermes/profiles/dimitri/SOUL.md` — restore Cella consult triggers (table rows referencing Cella + Agy, Cella brief at START)
4. Add `dimitri-cella` to GROW enrollment in `~/.hermes/grow/grow.conf`

## Restore only if:
- Production evidence shows `brain query` cannot replace Cella's strategic advisory role
- Or a specific workflow requires Cella's separate profile context

## Reason for archive:
- deepseek-v4-pro API key returned 401 consistently
- Sprint 1 `brain query` replaces strategic consultation role
- Profile overhead (env, cron bridge, skills, memory) not justified
