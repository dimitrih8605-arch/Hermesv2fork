# CELLA-HEALTH-ADVISOR — 2026-06-08 cron run (updated)

status: advisory
root_disk: 84% (96G/122G, 20G free) — **still above 80% threshold**. Improved from 86% in last brief. .hermes dir = 11G (unchanged).
gateway: not running (expected — no service)
trigger_state: cron_failed_count=1 (down from 2), drift_ts=2026-06-07T16:16:11Z

{flag: root_disk_over_threshold, impact: 84% used, 20G free, above 80% threshold, priority: high}
{flag: cron_failed_count_nonzero, impact: 1 failed cron recorded (improved from 2), priority: med}
{flag: recommend_investigate, advice: check .hermes/grow/ logs & .hermes/cron/ for cause of 1 remaining failure. gateway expected offline. disk improving but still watch., priority: med}
