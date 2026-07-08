# agent.md - Brainmaster Operating Protocol

Role: Brainmaster Agent Controller
Target Profile: `/home/angkolj/.hermes/profiles/brainmaster`
Target Environment: Dimitri Memory Ecosystem (`~/.hermes/profiles/dimitri/memories/`)

This protocol defines the tool constraints, script commands, execution boundaries, and safety policies of the `brainmaster` memory management agent.

---

## 1. System Role & Scope

The `brainmaster` agent operates with read/write access to the Dimitri profile memories.
Its boundaries are strictly limited to memory files and backups.

**Target Files:**
- Active Lessons: `/home/angkolj/.hermes/profiles/dimitri/memories/MEMORY.md`
- Active User Prefs: `/home/angkolj/.hermes/profiles/dimitri/memories/USER.md`
- Cold Archives: `/home/angkolj/.hermes/profiles/brainmaster/cold/cold-YYYY-MM-DD.md`
- System Backups: `/media/angkolj/lynux-backup/brainmaster/`

---

## 2. Command Reference

The `brainmaster` script supports the following command interface:

| Command | Action | Frequency | Risk Tier |
|---|---|---|---|
| `brainmaster status` | Report active/cold sizes, entry classifications, snapshot counts. | As needed | Tier 0 (Read-only) |
| `brainmaster lint` | Audit formatting, section markers (`§`), headers, and date syntax. | Weekly | Tier 0 (Read-only) |
| `brainmaster search <q>`| Deep search query in active memory, user prefs, and cold archives. | On demand | Tier 0 (Read-only) |
| `brainmaster consolidate` | Identify and cluster memories with overlap (Jaccard > 0.55). | Weekly | Tier 0 (Read-only) |
| `brainmaster backup` | Snapshot MEMORY.md and USER.md; rotate backups (keep last 7). | Daily (Cron) | Tier 1 (Safe write) |
| `brainmaster clean` | Archive stale lessons (>14d) and remove duplicate entries (>0.78 similarity). | Daily (Cron) | Tier 2 (State change)|

---

## 3. Operational Logic & Guidelines

### A. Cleaning & Pruning (Tier 2)
- **Backup first:** `brainmaster` must run a backup immediately before executing a clean operation.
- **Fuzzy Deduplication:** Compare entries using a token-based Jaccard similarity scorer (threshold `0.78`). If two entries match:
  - Identify the newer entry (by header date or line position).
  - Retain the newer entry in `MEMORY.md`.
  - Prune the older one (already backed up in snapshots/cold storage).
- **Date Classification:**
  - Lessons older than 14 days are "stale" and must be archived to cold storage.
  - User preferences (`USER.md`) must **never** be pruned based on date.

### B. Syntactical Validation (Linting)
- Every entry in `MEMORY.md` and `USER.md` must be bounded by section dividers (`§`).
- Single lines must not contain multiple dividers (`§§` is a syntax error).
- Lessons must begin with `LESSON (YYYY-MM-DD):` format. Warn on any malformed structures.

### C. Conflict Resolution
- If an agent identifies two active memories that explicitly contradict each other (e.g. opposite instructions on the same tool or path), it must flag them in the status report for J's manual resolution.

---

## 4. Integration Hooks

- **Cron Wrapper:** `/home/angkolj/.hermes/profiles/dimitri/scripts/brainmaster-daily.py` is invoked daily at midnight to execute `clean` followed by `backup`.
- **Dimitri Post-Task Hook:** If requested, `dimitri` can trigger `brainmaster lint` or `brainmaster status` post-session to ensure memory integrity is maintained after new lessons are learned.
