#!/bin/bash
# Hermes Desktop launcher — single instance, no restart loop.
# Kills stale processes + systemd scopes, shows window immediately on Linux/Wayland.

SENTINEL="/tmp/hermes-desktop-quit.sentinel"
PIDFILE="/tmp/hermes-desktop.pid"
DESKTOP_DIR="$HOME/.hermes/hermes-agent/apps/desktop"
ELECTRON="$HOME/.hermes/hermes-agent/node_modules/.bin/electron"

export HERMES_DESKTOP_DISABLE_GPU=1
export ELECTRON_OZONE_PLATFORM_HINT=auto

# Stale-dist guard: rebuild if main process source is newer than dist bundle.
# ponytail: one sentinel file (electron/main.ts) vs dist. npm run build
# rebuilds both main + renderer together, so a single check catches all.
cd "$DESKTOP_DIR" || exit 1
if [ "electron/main.ts" -nt "dist/electron-main.mjs" ] 2>/dev/null; then
  echo "[hermes-launcher] Source newer than dist — rebuilding..."
  npm run build 2>&1 | tail -5
  echo "[hermes-launcher] Rebuild complete."
fi

# Cleanup before launch — kill any previous hermes electron/processes
kill -9 $(ps aux | grep 'hermes-agent.*electron' | grep -v grep | awk '{print $2}') 2>/dev/null
kill -9 $(ps aux | grep 'electron.*desktop' | grep -v grep | awk '{print $2}') 2>/dev/null
systemctl --user reset-failed 2>/dev/null
# Kill stale systemd scopes via their PIDs
for pid in $(systemctl --user show 'app-org.chromium.Chromium-*.scope' -p MainPID --value 2>/dev/null | grep -v '^0$'); do
  kill $pid 2>/dev/null
done
sleep 1
systemctl --user kill app-org.chromium.Chromium-*.scope 2>/dev/null
systemctl --user reset-failed 2>/dev/null
rm -f /tmp/hermes-desktop*.pid /tmp/hermes-desktop-quit.sentinel

# PID lock
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (PID $(cat "$PIDFILE")). Exiting."
  exit 0
fi
echo $$ > "$PIDFILE"

cleanup() {
  rm -f "$SENTINEL" "$PIDFILE"
  exit 0
}
trap cleanup EXIT INT TERM

cd "$DESKTOP_DIR" && "$ELECTRON" . --no-sandbox --in-process-gpu --disable-features=UseSystemdServiceManager --ozone-platform-hint=auto --enable-features=UseOzonePlatform
