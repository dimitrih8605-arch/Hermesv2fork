"""
Raiden Plugin — Passive + active advisor for Hermes/Dimitri.

Hooks:
- on_session_end   → post-hoc review of completed turns (LLM-powered)
- pre_llm_call     → fast rule-based advisory before each response (no LLM)
- pre_tool_call    → block dangerous tool calls in real-time (pattern-match)
"""

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("raiden")

PROFILE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROFILE_DIR))

# Track per-turn state
_last_reviewed_turn = None

# ─── Tool call guards — fast pattern-match, no LLM ─────────────────────

# Terminal commands that need blocking
_BLOCKED_TERMINAL_PATTERNS = [
    (re.compile(r"\brm\s+-rf\s+/\b|\bdd\s+if=|>?\s*/dev/sda|mkfs\b|format\b|\bmv\s+/\s+"), "Destructive command — confirm with J first"),
    (re.compile(r"chmod\s+777|chown\s+-R\s+/"), "Dangerous permission change"),
    (re.compile(r"cat\s+.*\.env|echo\s+\$\w*(?:KEY|TOKEN|SECRET|PASS|API|AUTH)\w*", re.I), "Credentials exposed in terminal — use read_file"),
]

# Tools that are always safe (skip pre_tool_call entirely)
_SAFE_TOOLS = frozenset({
    "read_file", "search_files", "browser_snapshot", "browser_navigate",
    "skills_list", "skill_view", "todo", "session_search",
})

# Tools where we check args more carefully
_CHECKED_TOOLS = {"terminal", "patch", "write_file", "delegate_task", "cronjob"}


def _get_gate():
    """Lazy-init per-process emission gate."""
    from advisor import EmissionGate
    if not hasattr(_get_gate, "_gate") or _get_gate._gate is None:
        _get_gate._gate = EmissionGate(min_interval=30)
    return _get_gate._gate


def _get_recorder(session_id: str):
    """Lazy-init per-session recorder."""
    if not hasattr(_get_recorder, "_recorders"):
        _get_recorder._recorders = {}
    if session_id not in _get_recorder._recorders:
        from advisor import TranscriptRecorder
        data_dir = str(PROFILE_DIR / "data")
        _get_recorder._recorders[session_id] = TranscriptRecorder(session_id, data_dir)
    return _get_recorder._recorders[session_id]


# ─── Session-end (existing — LLM-powered post-hoc review) ───────────────

def _run_advisor_sync(session_id: str, messages: list, final_response: str) -> str | None:
    """Run advisor synchronously using advisor module."""
    from advisor import review_turn
    return asyncio.run(
        review_turn(
            messages=messages,
            assistant_reply=final_response,
            session_id=session_id,
            gate=_get_gate(),
            recorder=_get_recorder(session_id),
        )
    )


def on_session_end(**kwargs):
    """Post-hoc review of completed turns. LLM-powered, non-blocking."""
    session_id = kwargs.get("session_id", "")
    messages = kwargs.get("messages", [])
    final_response = kwargs.get("final_response", "")
    completed = kwargs.get("completed", True)
    interrupted = kwargs.get("interrupted", False)

    if not completed or interrupted:
        return
    if not final_response or len(final_response) < 20:
        return

    try:
        from advisor import cleanup_old_states
        cleanup_old_states()

        advice = _run_advisor_sync(session_id, messages, final_response)
        if advice:
            return {"advice": advice}
    except Exception as e:
        logger.debug(f"[raiden] on_session_end failed: {e}")


# ─── Pre-tool call (real-time guard on tool execution) ─────────────────

def on_pre_tool_call(**kwargs):
    """Real-time guard on dangerous tool calls.

    Fast pattern matching — no LLM needed. Blocks destructive commands,
    credential exposure, unauthorized cross-profile writes.
    """
    tool_name = kwargs.get("tool_name", "")
    args = kwargs.get("args", {})
    session_id = kwargs.get("session_id", "")
    if not isinstance(args, dict):
        args = {}

    # Skip safe tools
    if tool_name in _SAFE_TOOLS:
        return None

    # Skip tools not in checked list
    if tool_name not in _CHECKED_TOOLS:
        return None

    logger.debug(f"[raiden] pre_tool_call checking: {tool_name}")

    # ── Guard: terminal ──
    if tool_name == "terminal":
        cmd = args.get("command", "")
        if isinstance(cmd, str):
            for pattern, reason in _BLOCKED_TERMINAL_PATTERNS:
                if pattern.search(cmd):
                    logger.info(f"[raiden] blocked terminal: {cmd[:100]}")
                    return {"action": "block", "message": f"🔴 Raiden blocked: {reason}"}

    # ── Guard: .env file writes ──
    if tool_name in ("patch", "write_file"):
        path = args.get("path", args.get("file_path", ""))
        if isinstance(path, str) and (".env" in path or "credential" in path.lower()):
            logger.info(f"[raiden] blocked .env write: {path}")
            return {"action": "block", "message": "🔴 Raiden: .env/credential file protected — use secure credential handling"}

    # ── Guard: large delegations without team consult ──
    if tool_name == "delegate_task":
        goal = args.get("goal", "")
        if isinstance(goal, str) and len(goal) > 200:
            # Check if Cella/Agy already consulted via conversation history marker
            # (Can't access history here — just flag it)
            return {"action": "block", "message": "⚠️ Large task delegation — have you consulted Cella + Agy?"}

    # ── Guard: cronjob destructive action ──
    if tool_name == "cronjob":
        action = args.get("action", "")
        if action in ("remove", "pause") and not args.get("job_id"):
            return {"action": "block", "message": "🔴 Cron action without job_id — specify which job"}

    return None


# ─── Plugin registration ─────────────────────────────────────────────────

def register(ctx):
    """Register all Raiden hooks."""
    # Self-heal turn_finalizer patch for on_session_end data flow
    _patch_file = Path.home() / ".hermes/scripts/raiden_patch.py"
    if _patch_file.exists():
        subprocess.run([sys.executable, str(_patch_file)], capture_output=True)

    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    logger.info("[raiden] hooks registered: on_session_end, pre_llm_call, pre_tool_call")
