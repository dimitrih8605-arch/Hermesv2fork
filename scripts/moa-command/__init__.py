"""
Moa CLI — gateway-level slash commands for multi-brain presets.
/climoa  — agy + brain (local knowledge) raw → Dimitri synthesizes
/climoa1 — agy + omp raw → Dimitri synthesizes
/cli-moa — alias for /climoa
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

def register(ctx):
    # /climoa — agy + brain raw → Dimitri synthesizes
    def climoa_handler(raw_args):
        a = raw_args.strip()
        if not a:
            return "Usage: /climoa <prompt>"
        result = _run([MOA_SCRIPT, "2", "--raw", a])
        if result.startswith("Error"):
            return result
        if ctx.inject_message(f"[MoA] agy + brain results for: {a}\n\n{result}"):
            return "MoA done. Dimitri will respond..."
        return result

    # /climoa1 — agy + omp raw → Dimitri synthesizes (no cella, direct synth)
    def climoa1_handler(raw_args):
        a = raw_args.strip()
        if not a:
            return "Usage: /climoa1 <prompt>"
        result = _run([MOA_SCRIPT, "1", "--raw", a])
        if result.startswith("Error"):
            return result
        if ctx.inject_message(f"[MoA preset 1] agy + omp results for: {a}\n\n{result}"):
            return "MoA1 done. Dimitri will respond..."
        return result

    ctx.register_command("climoa", climoa_handler,
        description="agy + brain (local knowledge) → raw → Dimitri synthesizes",
        args_hint="<prompt>")
    ctx.register_command("climoa1", climoa1_handler,
        description="agy + omp → raw → Dimitri synthesizes",
        args_hint="<prompt>")
    ctx.register_command("cli-moa", climoa_handler,
        description="Alias for /climoa",
        args_hint="<prompt>")

    logger.info("[moa-command] /climoa /climoa1 /cli-moa registered")
