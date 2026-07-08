# Cella v2 Deployment Record
# 2026-05-29

Cella v2 operational. Changes from v1:
- Removed pre-decision hook (impossible without event system)
- Removed post-task hook (impossible without event system)
- Removed trigger planes (paper architecture only)
- Removed compliance gate integration (already non-functional)
- Removed old memory vault files (observations, patterns, thresholds, lessons)
- Created new SOUL.md with v2 protocol
- Created dimitri-cella-consult wrapper (one-shot advisory, no conversation)
- Cron heartbeat: flash model only, [SILENT] when healthy
- Cost guard: 2/session, 4/hr — now a feature, not bug

Key insight: Cella is a stateless advisory function, not an agent.
No hooks. No triggers. No execution. No conversation.
Just fast judgment when asked, and silent presence when not.
