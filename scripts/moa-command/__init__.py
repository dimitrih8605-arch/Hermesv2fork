"""
Moa CLI — gateway-level slash commands for multi-brain presets.
/climoa, /climoa1, /climoa2 (and /cli-moa for backwards compat).
"""

import logging
import subprocess
import os

logger = logging.getLogger("moa-command")
MOA_SCRIPT = "/home/angkolj/.local/bin/moa"

def _env():
    e = os.environ.copy()
    e["PATH"] = f"/home/angkolj/.local/bin:{e.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    return e

def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=_env())
        out, err = r.stdout.strip(), r.stderr.strip()
        if r.returncode != 0: return f"Error (exit {r.returncode}):\n{err}"
        return out or err or "No output"
    except subprocess.TimeoutExpired: return "MoA timed out after 180s."
    except FileNotFoundError: return f"Script not found: {MOA_SCRIPT}"
    except Exception as e: logger.error(f"moa error: {e}"); return f"Error: {e}"

def _mk_handler(preset):
    def h(raw_args):
        a = raw_args.strip()
        if not a:
            name = "climoa" + (" " + preset if preset else "")
            return f"Usage: /{name} <prompt>"
        return _run([MOA_SCRIPT] + ([preset] if preset else []) + [a])
    return h

def register(ctx):
    for name, desc, preset in [
        ("climoa", "Run multi-brain MoA: agy+omp presets", ""),
        ("climoa1", "MoA preset 1: agy+omp → Cella", "1"),
        ("climoa2", "MoA preset 2: agy+Cella → Dimitri synth", "2"),
        ("cli-moa", "Alias for /climoa", ""),  # backwards compat
    ]:
        ctx.register_command(name=name, handler=_mk_handler(preset), description=desc, args_hint="<prompt>")
    logger.info("[moa-command] /climoa /climoa1 /climoa2 registered")
