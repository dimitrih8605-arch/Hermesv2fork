'use strict'

/**
 * before-pack.cjs — electron-builder beforePack hook.
 *
 * Removes any stale unpacked app directory (`appOutDir`) before
 * electron-builder stages the Electron binaries into it.
 *
 * Also patches electron/main.cjs with --single-process + --no-sandbox flags
 * so rebuilds don't lose the fix (Chromium child processes crash on this host).
 *
 * WHY THIS EXISTS
 * ---------------
 * electron-builder's final packaging step copies the stock `electron`
 * binary into `release/<platform>-unpacked/` and then renames it to the
 * product name (`Hermes`). If a PREVIOUS `npm run pack` was interrupted
 * (Ctrl-C, OOM kill, crash, full disk) the unpacked directory is left in a
 * corrupted partial state: it keeps the already-renamed `LICENSE.electron.txt`
 * and the Chromium payload (.pak/.so/icudtl.dat/chrome-sandbox) but is MISSING
 * the `electron` binary itself.
 *
 * On the next run, electron-builder sees the destination directory already
 * populated, skips re-copying the binary it thinks is present, then tries to
 * rename a `electron` file that no longer exists. The build dies with:
 *
 *   ENOENT: no such file or directory, rename
 *   '.../release/linux-unpacked/electron' -> '.../release/linux-unpacked/Hermes'
 *
 * This is a hard failure with no obvious cause for the user — `hermes desktop`
 * just prints "Desktop GUI build failed" and the only fix is to manually
 * `rm -rf` the release directory, which a normal user has no way to know.
 *
 * The packaging step is not idempotent across an interrupted run, so we make
 * it idempotent ourselves: wipe the target unpacked directory up front so
 * electron-builder always stages into a clean tree. This is safe — the
 * directory is a pure build artifact that electron-builder fully recreates
 * on every pack; nothing else depends on its prior contents.
 *
 * Cross-platform: the same partial-state trap exists on macOS
 * (the mac-unpacked Hermes.app bundle) and Windows (win-unpacked), so we
 * clean whatever `appOutDir` electron-builder hands us regardless of platform.
 *
 * Best-effort: a cleanup failure must never mask the real build. We log and
 * resolve rather than throw — worst case electron-builder hits the original
 * ENOENT, which is no worse than not having this hook at all.
 *
 * electron-builder passes a context with:
 *   - appOutDir:            the unpacked app directory about to be staged
 *   - electronPlatformName: 'win32' | 'darwin' | 'linux'
 */

const fs = require('node:fs')
const path = require('node:path')

function cleanStaleAppOutDir(appOutDir) {
  if (!appOutDir || typeof appOutDir !== 'string') {
    return false
  }
  if (!fs.existsSync(appOutDir)) {
    return false
  }
  // Recursive + force so a half-written tree (read-only bits, partial files)
  // can't block the wipe. retry/maxRetries rides out transient EBUSY on
  // Windows where an AV/indexer may briefly hold a handle.
  fs.rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  return true
}

exports.cleanStaleAppOutDir = cleanStaleAppOutDir

/**
 * Patch electron/main.cjs with --single-process + --no-sandbox flags.
 * This host's Chromium child processes (zygote/GPU/network) all crash with
 * error_code=1002 due to systemd transient scope collisions and shared memory
 * kernel errors (errno 3 ESRCH). Single-process mode avoids spawning children.
 */
function patchMainCjs() {
  const mainCjs = path.join(__dirname, '..', 'electron', 'main.cjs')
  if (!fs.existsSync(mainCjs)) return false

  let content = fs.readFileSync(mainCjs, 'utf8')

  // Already patched — skip
  if (content.includes('ponytail: --single-process')) return false

  const oldBlock = (
    'if (REMOTE_DISPLAY_REASON) {\n' +
    '  app.disableHardwareAcceleration()\n' +
    "  app.commandLine.appendSwitch('disable-gpu-compositing')\n" +
    '  console.log(\n' +
    '    `[hermes] remote display detected (${REMOTE_DISPLAY_REASON}); disabling GPU hardware acceleration to prevent flicker`\n' +
    '  )\n' +
    '}'
  )

  const newBlock = (
    'if (REMOTE_DISPLAY_REASON) {\n' +
    '  app.disableHardwareAcceleration()\n' +
    "  app.commandLine.appendSwitch('disable-gpu-compositing')\n" +
    '  // ponytail: --single-process + --no-sandbox works around systemd transient\n' +
    '  // scope + shared memory kernel errors (errno 3 ESRCH) on this host.\n' +
    '  // Chromium child processes (zygote/GPU/network) crash with error_code=1002.\n' +
    "  app.commandLine.appendSwitch('single-process')\n" +
    "  app.commandLine.appendSwitch('no-sandbox')\n" +
    '  console.log(\n' +
    '    `[hermes] remote display detected (${REMOTE_DISPLAY_REASON}); GPU disabled, single-process mode`\n' +
    '  )\n' +
    '}'
  )

  if (!content.includes(oldBlock)) {
    console.warn('[before-pack] cannot patch main.cjs — pattern not found; hermes updated with different code?')
    return false
  }

  content = content.replace(oldBlock, newBlock)
  fs.writeFileSync(mainCjs, content, 'utf8')
  console.log('[before-pack] patched electron/main.cjs: --single-process + --no-sandbox')
  return true
}

exports.default = async function beforePack(context) {
  // Apply persistent patches before staging so they end up in the asar
  patchMainCjs()

  const appOutDir = context && context.appOutDir
  try {
    if (cleanStaleAppOutDir(appOutDir)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`)
    }
  } catch (err) {
    // Never fail the build over cleanup; surface why so a genuinely stuck
    // directory (permissions, mount) is still diagnosable.
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`)
  }
}
