# Decision: Electron Desktop Deprecated — Web Dashboard Primary

**2026-07-06**

## Problem

Electron 40.10.x crashes on this host (kernel 6.17.0-35, NVIDIA RTX 3060 Ti,
driver 580.159.03). Three root causes:

1. **systemd transient scope collision** — Chromium child processes conflict
   with systemd's per-process resource tracking.
2. **`/dev/shm` shared memory ESRCH (errno 3)** — GPU process cascade failure
   from kernel-level memory signal handling.
3. **SIGTRAP on BrowserWindow creation** — Electron 40.x itself cannot create
   windows on this kernel, regardless of GPU flags.

## Fixes Applied (in Hermesv2fork git history)

| Commit | Fix |
|--------|-----|
| `007629e74` | `--single-process` + `--no-sandbox` + `disableHardwareAcceleration()` |
| `ae8a1a3c4` | FD cleanup patch — close inherited FDs before Python backend starts |
| `8be51d1eb` | Quit sentinel wrapper — distinguish normal close from crash for auto-restart |
| `89cf65ab6` | TUI ANSI strip for desktop chat compatibility |

None of these fully fix Electron 40.x on this kernel. SIGTRAP on
BrowserWindow creation is an Electron-internal issue, not workaroundable
from flags or patches.

## Resolution

**Web dashboard** (port 9119) replaces Electron desktop entirely for
chat + agent + profile UI. Feature-equivalent on this host. Electron
desktop files left in repo for future use when kernel/hardware supports it.
All patches preserved in fork history.

## Status

Electron desktop: **not running**. No node_modules, no asar build.
Web dashboard: **active** (systemd service, port 9119).
