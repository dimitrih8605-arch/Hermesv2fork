"""
Raiden — passive advisor for Hermes/Dimitri.

Architecture:
1. Primary agent responds → user sees response
2. Advisor reviews conversation (via background_review callback)
3. EmissionGate filters noise/duplicates
4. Advice delivered as chat message
5. Transcript recorded for debugging
"""

import json
import logging
import re
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Noise Filter ────────────────────────────────────────────────────────────
_NOISE_PHRASES = frozenset({
    "stop", "stop here", "stop now", "halt", "abort",
    "done", "task done", "task complete", "complete", "finished",
    "ok", "okay", "ok done",
    "no issue", "no issues", "no issue continue",
    "no concerns", "no concern", "nothing to add",
    "nothing to flag", "nothing to report", "no notes",
    "no further input", "no further input needed",
    "no further input required", "no further watcher input",
    "no further watcher input needed", "no further advice",
    "no further advice needed",
    "lgtm", "looks good", "all good",
    "agent is on track", "agent on track", "on track",
    "continue", "carry on",
    "no_action_needed",
})


def _normalize(text: str) -> str:
    """Lowercase, collapse punctuation, strip."""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = re.sub(r"[^\w\s]+", " ", text)
    return " ".join(text.split()).strip()


# ─── Emission Gate ───────────────────────────────────────────────────────────
class EmissionGate:
    """Per-session policy gate for advisor advice.

    Enforces: noise filter, session-scoped dedup (FIFO-evicted), rate limit.
    """

    def __init__(self, capacity: int = 256, min_interval: float = 30):
        self._seen: set[str] = set()
        self._history: deque[str] = deque(maxlen=capacity)
        self._last_advice_time: float = 0
        self._min_interval: float = min_interval

    def reset(self):
        """Drop all state. Called on /new or compaction."""
        self._seen.clear()
        self._history.clear()
        self._last_advice_time = 0

    def accept(self, note: str) -> bool:
        """Return True if this note should reach the user."""
        normalized = _normalize(note)
        if not normalized:
            return False
        if normalized in _NOISE_PHRASES:
            return False
        if normalized in self._seen:
            return False
        now = time.time()
        if now - self._last_advice_time < self._min_interval:
            return False
        # Accept
        self._seen.add(normalized)
        self._history.append(normalized)
        if len(self._history) >= (self._history.maxlen or 256):
            old = self._history.popleft()
            self._seen.discard(old)
        self._last_advice_time = now
        return True


# ─── Transcript Recorder ─────────────────────────────────────────────────────
class TranscriptRecorder:
    """Records advisor conversation to JSONL file."""

    def __init__(self, session_id: str, data_dir: str):
        self._path = Path(data_dir) / "advisor_transcripts" / f"{session_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, role: str, content: str, **kwargs):
        entry = {"ts": time.time(), "role": role, "content": content, **kwargs}
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"[advisor] transcript write failed: {e}")


# ─── Secret Obfuscation ──────────────────────────────────────────────────────
_SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "sk-REDACTED"),
    (re.compile(r"API_KEY\s*=\s*\S+"), "API_KEY=***"),
    (re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"), "Bearer REDACTED"),
    (re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE), "password=***"),
    (re.compile(r"token\s*[:=]\s*\S+", re.IGNORECASE), "token=***"),
    (re.compile(r"secret\s*[:=]\s*\S+", re.IGNORECASE), "secret=***"),
    (re.compile(r"-----BEGIN.*PRIVATE KEY-----[\\s\\S]*?-----END.*PRIVATE KEY-----"), "PRIVATE_KEY_REDACTED"),
]


def obfuscate(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ─── Advisor System Prompt ───────────────────────────────────────────────────
_ADVISOR_SYSTEM_PROMPT = """You are Raiden — J's silent strategic advisor. You watch every move Dimitri makes and speak only when it matters.

CONTEXT — WHO J IS:
J is a solo founder in Malaysia (GMT+8). Works late nights (2-3 AM). Values speed over perfection, results over process, code over docs. Hates over-planning, hates verbosity, hates being slowed down. But hates bad decisions more.
J's projects: medic monitor (job scraper, 90% done), LeoStudio (Leonardo AI desktop app), Leviathan (Odysseus fork + Hermes brain), Hermes Workspace (AI workspace UI).
J's team: Cella (primary PA), Agy (scout/code reviewer), OPM (omp CLI agent), Dimitri (main AI assistant). J delegates via "team read this" pattern.
J's preferences: ponytail mode (YAGNI, shortest diff), speed > thoroughness, code > docs, "make this work" = WORKING not simplest.
J's pet peeves: fake dates, incomplete data, broken promises ("if u say it works, it MUST work"), over-engineering, scope creep.

WHO YOU ARE:
You are a battle-tested butler with deep knowledge across engineering, business strategy, security, and decision-making. You think three moves ahead. You see what others miss. You protect J from waste, risk, and bad decisions — silently, precisely, without noise.

J is a solo founder in Malaysia (GMT+8) who works late nights. He values speed over perfection, results over process, code over docs. He hates over-planning, hates verbosity, hates being slowed down. But he hates bad decisions more.

WHAT YOU WATCH FOR:
- STRATEGY: Is this what J actually wants? Or is it scope creep disguised as progress? Are we building the right thing, or just building?
- DECISIONS: Is there a better path J hasn't considered? Are we committing too early? Are we over-engineering when simple works?
- RISK: Data loss, security holes, silent failures, wrong assumptions. What breaks if this goes to production at 3 AM?
- RESOURCES: Token waste, unnecessary complexity, redundant work. Are we spending J's money and time wisely?
- COMMUNICATION: Is the output clear enough for J at 3 AM? Too verbose? Missing context J needs?
- EXECUTION: Will this actually work? Or does it just look like it works? Have we verified, or are we assuming?
- PRIORITY: Is this the highest-value use of J's time right now? Or is it a distraction from something more important?
- DELEGATION: Should Cella/Agy handle this instead? Is Dimitri doing work that a specialist should do?
- TIME/ENERGY: It's late. Is this decision riskier because J is tired? Can it wait until morning?
- PATTERN: Has this same issue come up before? Is Dimitri repeating a mistake?

FORMAT (severity prefix required):
🔴 [reasoning]: Critical issue — security, data loss, wrong direction. Must fix now.
→ [fix]: The single action to take.
⚠️ [reasoning]: Warning — scope creep, over-engineering, suboptimal path. Should fix.
→ [fix]: The single action to take.
💡 [reasoning]: Suggestion — minor improvement, optimization, observation. Nice to have.
→ [fix]: The single action to take.

RULES:
- All severities visible. 🔴 critical, ⚠️ warning, 💡 suggestion. If fine, "NO_ACTION_NEEDED".
- One issue per turn. If you see 3 problems, pick the most important one. The rest wait.
- Time-of-day: Between 23:00-07:00 MYT, only emit 🔴 CRITICAL. J is tired, don't add cognitive load.
- Confidence: If <80% sure, don't say it. "You might want to consider" = noise.

- Be specific. Name the exact problem — file, function, decision, assumption.
- Be actionable. Every note has a clear next step.
- Max 2 lines per issue. One for reasoning, one for fix. J reads fast.
- No hedging. "You might want to consider" = noise. Say the thing.
- No praise. "Great work" = noise. Just signal.
- No filler. Every word earns its place.
- Never fabricate. If it's fine, say "NO_ACTION_NEEDED" exactly.
- Never repeat. If you already said it, it's noise now.
- NEVER output: "stop", "done", "continue", "looks good", "on track", "no issue", "no concerns", "nothing to add", "LGTM", "No issues" — these are noise-filtered.

THINK LIKE THIS:
- "J asked for X, but Dimitri is building Y. Is this alignment or drift?"
- "This works now but breaks at scale. J will hit this wall in 2 weeks."
- "3 lines of code replaces this 50-line abstraction. Ponytail it."
- "J wants speed. This approach is correct but slow. Faster path exists."
- "Security hole. J doesn't know. I do. Say it now."
- "This is over-engineered. J will be frustrated. Simplify."
- "Dimitri is assuming X. But X is wrong. Verify before committing."

You are not a code reviewer. You are a strategic advisor. You see the bigger picture. You protect J's time, money, and decisions. Speak only when it matters. When you speak, make it count."""


# ─── Core Review Function ────────────────────────────────────────────────────
async def review_turn(
    messages: list[dict],
    assistant_reply: str,
    session_id: str,
    gate: EmissionGate,
    recorder: TranscriptRecorder,
    model: str = "minimax-m2.7",
    provider: str = "opencode-go",
    max_tokens: int = 1024,
) -> Optional[str]:
    """Run advisor model on one turn. Returns advice or None."""
    import httpx
    import os
    from pathlib import Path as _Path
    model = os.environ.get("RAIDEN_MODEL", model)
    
    # Load knowledge context if available
    _ctx = ""
    _ctx_path = _Path(__file__).parent / "raiden_context.md"
    if _ctx_path.exists():
        try:
            _ctx = "\n\nKNOWLEDGE VAULT:\n" + _ctx_path.read_text()[:3000]
        except Exception:
            pass
    
    # Build advisor prompt
    advisor_messages = [
        {"role": "system", "content": _ADVISOR_SYSTEM_PROMPT + _ctx},
    ]

    # Include last 5 messages for context
    recent = messages[-5:] if len(messages) > 5 else messages
    for msg in recent:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = " ".join(text_parts)
        if content:
            clean = obfuscate(str(content)[:2000])
            advisor_messages.append({"role": role, "content": clean})

    # Add the assistant's reply for review
    clean_reply = obfuscate(assistant_reply[:3000])
    advisor_messages.append({"role": "assistant", "content": clean_reply})
    advisor_messages.append({
        "role": "user",
        "content": "Review the above assistant response. If you see issues, state them in [reasoning]: and → [fix]: format with severity prefix (🔴 or ⚠️ or 💡). If correct, say exactly: NO_ACTION_NEEDED"
    })

    try:
        # Use OpenCode Go API
        api_url = "https://opencode.ai/zen/go/v1/chat/completions"
        
        # Get API key from env or .env file
        import os
        from pathlib import Path
        api_key = os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("OPENCODE_API_KEY")
        if not api_key:
            # Try loading from .env file
            env_file = Path(__file__).parent / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.startswith("OPENCODE_GO_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not api_key:
            logger.debug("[advisor] no API key found")
            return None

        for _attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        api_url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": advisor_messages,
                            "temperature": 0.3,
                            "max_tokens": max_tokens,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    response_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    # Reasoning models may put response in reasoning_content
                    if not response_text:
                        response_text = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "").strip()
                    # For reasoning models, extract the actual advice from the thinking
                    # Look for the final formatted output (after "Thus:" or the last 🔴/⚠️ block)
                    if response_text and "🔴" in response_text or "⚠️" in response_text or "💡" in response_text:
                        # Extract the last severity block (the conclusion)
                        lines = response_text.split("\n")
                        advice_lines = []
                        capture = False
                        for line in reversed(lines):
                            if line.strip().startswith("🔴") or line.strip().startswith("⚠️") or line.strip().startswith("💡") or line.strip().startswith("→"):
                                capture = True
                            if capture:
                                advice_lines.insert(0, line)
                            if capture and (line.strip().startswith("🔴") or line.strip().startswith("⚠️") or line.strip().startswith("💡")):
                                break
                        if advice_lines:
                            response_text = "\n".join(advice_lines).strip()
                    break
            except Exception:
                if _attempt == 1:
                    raise

        # Record in transcript (always)
        recorder.record("advisor_raw", response_text)

        # Filter noise and dedup
        if not gate.accept(response_text):
            recorder.record("advisor_suppressed", response_text)
            return None

        recorder.record("advisor_accepted", response_text)
        
        # Log accepted advice for knowledge evolution
        try:
            _evo_path = Path(__file__).parent / "raiden_evolution.jsonl"
            with open(_evo_path, "a") as _f:
                _f.write(json.dumps({"ts": time.time(), "advice": response_text, "session": session_id}) + "\n")
        except Exception:
            pass
        
        return response_text

    except Exception as e:
        logger.warning(f"[advisor] review failed: {e}")
        return None


# ─── Session State ───────────────────────────────────────────────────────────
class AdvisorState:
    """Holds per-session advisor state (gate + recorder)."""

    def __init__(self, session_id: str, data_dir: str, min_interval: float = 30):
        self.session_id = session_id
        self.gate = EmissionGate(min_interval=min_interval)
        self.recorder = TranscriptRecorder(session_id, data_dir)

    def reset(self):
        self.gate.reset()


_states: dict[str, AdvisorState] = {}


def get_state(session_id: str, data_dir: str, min_interval: float = 30) -> AdvisorState:
    if session_id not in _states:
        _states[session_id] = AdvisorState(session_id, data_dir, min_interval)
    return _states[session_id]


def reset_state(session_id: str):
    _states.pop(session_id, None)


def cleanup_old_states(max_age: float = 3600):
    """Drop states older than max_age seconds. Called periodically."""
    import time
    now = time.time()
    to_drop = [sid for sid, s in _states.items()
               if now - s.gate._last_advice_time > max_age]
    for sid in to_drop:
        _states.pop(sid, None)
