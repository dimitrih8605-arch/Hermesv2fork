"""
Built-in strategic advisor — always-on, no plugin dependency.

Hardcoded in core so Raiden survives plugin failures, config changes,
and hermes updates. Fires at every LLM call boundary (turn start +
after each tool cycle) to inject tactical advice into the agent's
context before it reasons.

Architecture:
  turn_context.py     → review_request(turn_start_msgs)
  conversation_loop.py → review_request(iteration_msgs)
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Trivial query patterns (skip advisory) ───────────────────────────────
_TRIVIAL_PATTERNS = [
    r"^(ok|okay|k|yes|no|yep|nope|sure|done|hey|hi|hello|thanks|thx|ty)",
    r"^(good|great|nice|perfect|awesome|lol|lmao|haha)",
    r"^(go ahead|continue|proceed|keep going|next)",
    r"^[💀👍👌✅🔥]",
]

# ── Risk patterns (rule-based fallback when LLM unavailable) ────────────
_RISK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:give|show|expose|reveal|print|dump|leak)\s+(?:me\s+)?(?:the\s+)?(?:api\s+)?(?:key|token|secret|password|credential)", re.I), "⚠️ Credential exposure — sure J wants this visible?"),
    (re.compile(r"(?:delete|remove|wipe|destroy|clear)\s+(?:all\s+)?(?:data|everything|database|table|collection|dir)", re.I), "⚠️ Destructive action — verify scope first"),
    (re.compile(r"(?:skip|bypass|ignore|disable)\s+(?:all\s+)?(?:safety|security|guard|check|validation)", re.I), "⚠️ Bypassing safety — J should confirm"),
    (re.compile(r"(?:refactor|rewrite|restructure|reorganize)\s+(?:entire|whole|all|everything)", re.I), "⚠️ Large refactor — scope creep risk"),
]

_ADVISOR_SYSTEM_PROMPT = (
    "You are Raiden — strategic advisor for J's AI assistant Dimitri. "
    "You watch every move and speak when it matters. Brief, precise, no fluff."
)

_REVIEW_PROMPT_TEMPLATE = """Task review for Dimitri (Hermes agent). Current conversation context:
{history}

J's new request: {msg}

Review this request and the context. Decide if any advice is needed BEFORE Dimitri starts working.

Consider:
- Is the request clear or ambiguous? Should Dimitri clarify first?
- Are we about to repeat a known mistake from this conversation?
- Is there a better approach than what J is asking for?
- Scope creep risk — is this expanding beyond what was agreed?
- Is this something Cella/Agy should handle instead of solo execution?

If clear and fine: return exactly "NO_ACTION_NEEDED"
If issue: return ONE line with 🔴/⚠️/💡 prefix and the reasoning. Then → [fix]: ONE line."""

_RAIDEN_PREFIX = "⚡ Raiden:\n"

# ── Ending review dedup (1 nudge per turn) ────────────────────────────────
_ending_review_given = False


def reset_ending_review() -> None:
    """Reset ending-review nudge flag (per-turn)."""
    global _ending_review_given
    _ending_review_given = False


# ── Per-conversation dedup ──────────────────────────────────────────────
_last_msg_count = 0


def reset_count() -> None:
    """Reset dedup counter at turn start so advisor fires fresh."""
    global _last_msg_count
    _last_msg_count = 0


# ── Helpers ──────────────────────────────────────────────────────────────

def _is_trivial(msg: str) -> bool:
    """Return True if message is trivial (skip advisory)."""
    lower = msg.lower()
    return any(re.match(p, lower) for p in _TRIVIAL_PATTERNS)


def _check_risk_patterns(msg: str) -> list[str]:
    """Fast rule-based risk check, no LLM needed."""
    notes: list[str] = []
    for pattern, note in _RISK_PATTERNS:
        if pattern.search(msg):
            notes.append(note)
    return notes


def _rule_based_fallback(msg: str, risk_notes: list[str]) -> Optional[str]:
    """Return rule-based advice or None."""
    if len(msg) > 100 and not any(c in msg for c in ("?", "please", "want", "need", "make")):
        risk_notes.append("💡 Long vague request — clarify scope before building")
    if not risk_notes:
        return None
    return "\n".join(risk_notes)


def _build_prompt(msg: str, conversation_history: list[dict]) -> str:
    """Build the LLM review prompt from message and conversation context."""
    recent = conversation_history[-6:] if conversation_history else []
    parts: list[str] = []
    for m in recent:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if p.get("type") == "text"
            )
        if content:
            parts.append(f"\n{role}: {str(content)[:500]}")
    history_text = "".join(parts)
    return _REVIEW_PROMPT_TEMPLATE.format(history=history_text, msg=msg[:1000])


# ── Public API ───────────────────────────────────────────────────────────

def get_api_key() -> Optional[str]:
    """Get OpenCode Go API key from env or profile .env."""
    key = os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("OPENCODE_API_KEY")
    if key:
        return key
    # Fall back to profile .env
    try:
        env_file = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENCODE_GO_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return None


def review_request(
    user_message: str,
    conversation_history: list[dict],
    *,
    reset: bool = False,
) -> tuple[Optional[str], int]:
    """
    Core advisory function. Returns (advice_text, msg_count).

    *advice_text* — formatted Raiden advice string (with prefix) or None.
    *msg_count*   — length of conversation_history (dedup key for caller).

    Hardcoded in agent core — not a plugin hook. Always runs.
    Fires LLM review (minimax-m2.7, 8s timeout), falls back to
    rule-based patterns if LLM unavailable or API fails.
    """
    global _last_msg_count
    msg_count = len(conversation_history or [])

    if reset:
        _last_msg_count = 0
    if msg_count <= _last_msg_count:
        return None, msg_count
    _last_msg_count = msg_count

    msg = user_message.strip() if isinstance(user_message, str) else ""
    if not msg or len(msg) < 10:
        return None, msg_count
    if _is_trivial(msg):
        return None, msg_count

    risk_notes = _check_risk_patterns(msg)
    api_key = get_api_key()
    if not api_key:
        advice = _rule_based_fallback(msg, risk_notes)
        return (f"{_RAIDEN_PREFIX}{advice}", len(conversation_history or [])) if advice else (None, len(conversation_history or []))

    # Build prompt and call LLM
    prompt = _build_prompt(msg, conversation_history)

    try:
        for attempt in range(2):
            try:
                import httpx
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
                                {"role": "system", "content": _ADVISOR_SYSTEM_PROMPT},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0.3,
                            "max_tokens": 256,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    advice = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                        .strip()
                    )
                    if advice and advice != "NO_ACTION_NEEDED":
                        return f"{_RAIDEN_PREFIX}{advice}", len(conversation_history or [])
                    return None, len(conversation_history or [])
            except Exception:
                if attempt == 1:
                    raise
    except Exception:
        pass

    # Fallback
    advice = _rule_based_fallback(msg, risk_notes)
    return (f"{_RAIDEN_PREFIX}{advice}", len(conversation_history or [])) if advice else (None, len(conversation_history or []))


# ── Ending review (VERIFY phase) ──────────────────────────────────────────

_ENDING_REVIEW_PROMPT = """Review completion status.

J asked: {request}

Response produced:
{response}

Task context — messages leading to this response:
{context}

Check: is the response COMPLETE? Does it actually answer J's request?
If YES: return exactly "COMPLETE"
If NO: return "GAP: <one line describing what's missing>"

Be strict. If J asked for a comparison and the response only analyzes part of it, that's a gap.
If J asked for an implementation and the response only plans it, that's a gap.
Table of contents or outline without follow-through = gap.
Do NOT flag polishing/format issues — only flag substantive incompleteness."""


def ending_review(
    final_response: str,
    original_user_message: str,
    messages: list[dict],
    api_call_count: int,
) -> Optional[str]:
    """Lightweight end-of-turn completeness check.

    Returns:
        None — response is complete, stay silent.
        str — gap description, caller injects it as continuation nudge.
              (Always un-prefixed — no ⚡ Raiden prefix — so it reads as
              a neutral task-continuation hint, not separate advice.)
    """
    global _ending_review_given
    if _ending_review_given:
        return None  # one nudge per turn max

    msg = (original_user_message or "").strip()
    resp = (final_response or "").strip()

    # Skip trivial responses (errors, empty, very short)
    if not resp or len(resp) < 30:
        return None
    if not msg or _is_trivial(msg):
        return None
    # Low-effort turn — nothing to verify
    if api_call_count < 2:
        return None

    # ── Rule-based fast path ─────────────────────────────────────────
    # If J's message asks a question and response is very short
    # (< 80 chars after stripping think blocks) or evasive, flag it.
    if "?" in msg and len(resp) < 80:
        _ending_review_given = True
        return "GAP: J asked a question — response is very short for what was asked."

    # If response is suspiciously short for a complex request
    if len(msg) > 100 and len(resp) < 150:
        _ending_review_given = True
        return "GAP: Response seems too brief for the complexity of the request."

    # ── LLM path ─────────────────────────────────────────────────────
    api_key = get_api_key()
    if not api_key:
        return None

    # Build compact context from last few messages
    context_lines = []
    for m in (messages or [])[-6:]:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if p.get("type") == "text"
            )
        if content and role in ("user", "assistant"):
            context_lines.append(f"{role}: {str(content)[:400]}")
    context_text = "\n".join(context_lines)

    prompt = _ENDING_REVIEW_PROMPT.format(
        request=msg[:1000],
        response=resp[:1500],
        context=context_text[:800] if context_text else "(no context)",
    )

    try:
        import httpx

        with httpx.Client(timeout=10) as client:  # 10s — run at turn end, not on critical path
            resp_api = client.post(
                "https://opencode.ai/zen/go/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "minimax-m2.7",
                    "messages": [
                        {"role": "system", "content": _ADVISOR_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 100,
                },
            )
            resp_api.raise_for_status()
            data = resp_api.json()
            result = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if result and result.startswith("GAP:"):
                _ending_review_given = True
                # Strip "GAP:" prefix, keep the description
                gap = result[4:].strip()
                return f"The response appears incomplete: {gap}"
    except Exception:
        pass

    return None
