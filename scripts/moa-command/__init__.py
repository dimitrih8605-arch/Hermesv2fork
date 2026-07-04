"""
Moa CLI — gateway-level slash command for multi-brain presets.
Registered via plugin system so it persists across updates.
Usage: /cli-moa <prompt>   or   /cli-moa 1 <prompt>   or   /cli-moa 2 <prompt>
"""

import logging
import subprocess
import shlex

logger = logging.getLogger("moa-command")

MOA_SCRIPT = "/home/angkolj/.local/bin/moa"

def register(ctx):
    """Register /cli-moa slash command."""
    ctx.register_command(
        name="cli-moa",
        handler=handle_cli_moa,
        description="Run multi-brain MoA: agy+omp presets (1=agy+omp->cella, 2=agy+cella->dimitri)",
        args_hint="<prompt>",
    )
    logger.info("[moa-command] /cli-moa registered")

def handle_cli_moa(raw_args: str) -> str:
    """Run moa script with args, return output."""
    if not raw_args.strip():
        return "Usage: /cli-moa [1|2] <prompt>\n  default: agy+omp->deepseek\n  1: agy+omp->cella\n  2: agy+cella->dimitri"

    try:
        # Build command: moa script takes prompt or preset+prompt
        cmd = [MOA_SCRIPT] + shlex.split(raw_args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            env=None,  # inherit env
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode != 0:
            return f"Error (exit {result.returncode}):\n{err}"
        if not out and err:
            return err
        return out
    except subprocess.TimeoutExpired:
        return "MoA timed out after 180s. One of the agents hung."
    except FileNotFoundError:
        return f"MoA script not found at {MOA_SCRIPT}"
    except Exception as e:
        logger.error(f"cli-moa error: {e}")
        return f"MoA error: {e}"
