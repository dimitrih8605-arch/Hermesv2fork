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
_prev_msg_count = 0

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

# Pre-LLM skip patterns — trivial queries don't need advisory
_TRIVIAL_PATTERNS = [
    r"^(ok|okay|k|yes|no|yep|nope|sure|done|hey|hi|hello|thanks|thx|ty)",
    r"^(good|great|nice|perfect|awesome|lol|lmao|haha)",
    r"^(go ahead|continue|proceed|keep going|next)",
    r"^[💀👍👌✅🔥]",
]

# Pre-LLM risk patterns — flag these before responding
_RISK_PATTERNS = [
    (re.compile(r"(?:give|show|expose|reveal|print|dump|leak)\s+(?:me\s+)?(?:the\s+)?(?:api\s+)?(?:key|token|secret|password|credential)", re.I), "⚠️ Credential exposure — sure J wants this visible?"),
    (re.compile(r"(?:delete|remove|wipe|destroy|clear)\s+(?:all\s+)?(?:data|everything|database|table|collection|dir)", re.I), "⚠️ Destructive action — verify scope first"),
    (re.compile(r"(?:skip|bypass|ignore|disable)\s+(?:all\s+)?(?:safety|security|guard|check|validation)", re.I), "⚠️ Bypassing safety — J should confirm"),
    (re.compile(r"(?:refactor|rewrite|restructure|reorganize)\s+(?:entire|whole|all|everything)", re.I), "⚠️ Large refactor — scope creep risk"),
]


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


# ─── Pre-LLM call (LLM-powered advisory before each turn) ──────────────

def _run_advisory_sync(
    user_message: str,
    conversation_history: list[dict],
    session_id: str,
) -> str | None:
    """Call Raiden LLM for tactical advice before the turn starts.

    Fast model (minimax-m2.7), short timeout. Returns advice or None.
    """
    msg = user_message.strip() if isinstance(user_message, str) else ""
    if not msg or len(msg) < 10:
        return None
    msg_lower = msg.lower()
    if any(re.match(p, msg_lower) for p in _TRIVIAL_PATTERNS):
        return None

    # Check risk patterns first (fast path, no LLM needed)
    risk_notes = []
    for pattern, note in _RISK_PATTERNS:
        if pattern.search(msg):
            risk_notes.append(note)

    try:
        import httpx
    except ImportError:
        return _rule_based_advice(msg, risk_notes)
    import os
    from pathlib import Path

    api_key = os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("OPENCODE_API_KEY")
    if not api_key:
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENCODE_GO_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        # Fallback to rule-based
        return _rule_based_advice(msg, risk_notes)

    # Build quick review prompt
    recent_history = conversation_history[-6:] if conversation_history else []
    history_text = ""
    for m in recent_history:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        if content:
            history_text += f"\n{role}: {str(content)[:500]}"

    prompt = f"""Task review for Dimitri (Hermes agent). Current conversation context:
{history_text}

J's new request: {msg[:1000]}

Review this request and the context. Decide if any advice is needed BEFORE Dimitri starts working.

Consider:
- Is the request clear or ambiguous? Should Dimitri clarify first?
- Are we about to repeat a known mistake from this conversation?
- Is there a better approach than what J is asking for?
- Scope creep risk — is this expanding beyond what was agreed?
- Is this something Cella/Agy should handle instead of solo execution?

If clear and fine: return exactly "NO_ACTION_NEEDED"
If issue: return ONE line with 🔴/⚠️/💡 prefix and the reasoning. Then → [fix]: ONE line."""

    try:
        for attempt in range(2):
            try:
                with httpx.Client(timeout=8) as client:
                    resp = client.post(
                        "https://opencode.ai/zen/go/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "minimax-m2.7",
                            "messages": [
                                {"role": "system", "content": "You are Raiden — strategic advisor for J's AI assistant Dimitri. You watch every move and speak when it matters. Brief, precise, no fluff."},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0.3,
                            "max_tokens": 256,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    advice = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if advice and advice != "NO_ACTION_NEEDED":
                        return advice
                    break
            except Exception:
                if attempt == 1:
                    raise
    except Exception:
        pass

    # Fallback to rule-based if LLM failed
    return _rule_based_advice(msg, risk_notes)


def _rule_based_advice(msg: str, risk_notes: list[str]) -> str | None:
    """Fast rule-based fallback when LLM unavailable."""
    if len(msg) > 100 and not any(c in msg for c in ("?", "please", "want", "need", "make")):
        risk_notes.append("💡 Long vague request — clarify scope before building")
    if not risk_notes:
        return None
    return "\n".join(risk_notes)


def on_pre_llm_call(**kwargs):
    """LLM-powered advisory before Dimitri responds.

    Calls Raiden (minimax-m2.7, 8s timeout) to review J's request
    and inject tactical advice into context. Falls back to rule-based
    if LLM unavailable.

    Fires on every LLM call iteration, dedup by message count so
    Raiden only re-consults when conversation has new information.
    """
    global _prev_msg_count

    user_message = kwargs.get("user_message", "")
    session_id = kwargs.get("session_id", "")
    conversation_history = kwargs.get("conversation_history", [])

    msg = user_message.strip() if isinstance(user_message, str) else ""
    if not msg:
        return None

    # Dedup by message count — only fire when conversation grew
    msg_count = len(conversation_history) if conversation_history else 0
    if msg_count <= _prev_msg_count:
        return None
    _prev_msg_count = msg_count

    # Skip trivial queries (fast path, no LLM)
    msg_lower = msg.lower()
    if any(re.match(p, msg_lower) for p in _TRIVIAL_PATTERNS):
        return None

    # Run LLM advisory (with rule-based fallback)
    advice = _run_advisory_sync(msg, conversation_history, session_id)

    if not advice:
        return None

    return {"context": f"⚡ Raiden:\n{advice}"}


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
