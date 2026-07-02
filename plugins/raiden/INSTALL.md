# Raiden Advisor — Permanent Installation

## What Is This
Raiden is J's passive strategic advisor — a silent butler that reviews every Dimitri turn and injects advice when it matters. Built following OMP's advisor architecture, adapted for Hermes.

## Files (all persistent)
- `~/.hermes/profiles/dimitri/plugins/raiden/__init__.py` — Plugin hook (on_session_end)
- `~/.hermes/profiles/dimitri/plugins/raiden/plugin.yaml` — Plugin manifest
- `~/.hermes/profiles/dimitri/advisor.py` — Core advisor (persona, EmissionGate, review_turn)
- `~/.hermes/profiles/dimitri/raiden_hook.py` — Standalone hook script

## Config (persistent)
- `plugins.enabled` includes "raiden" in config.yaml
- `auxiliary.background_review` model: deepseek-v4-pro, provider: opencode-go

## turn_finalizer.py Patch (FRAGILE — re-apply after `hermes update`)
The `on_session_end` hook in `agent/turn_finalizer.py` originally only passed session metadata (session_id, task_id, etc.) — NOT messages or final_response. Raiden needs those to review.

### The Patch
In `~/.hermes/hermes-agent/agent/turn_finalizer.py`, find the `on_session_end` hook block (~line 467):

**BEFORE (original):**
```python
_invoke_hook(
    "on_session_end",
    session_id=agent.session_id,
    task_id=effective_task_id,
    turn_id=turn_id,
    completed=completed,
    interrupted=interrupted,
    model=agent.model,
    platform=getattr(agent, "platform", None) or "",
)
```

**AFTER (patched):**
```python
_on_session_end_results = _invoke_hook(
    "on_session_end",
    session_id=agent.session_id,
    task_id=effective_task_id,
    turn_id=turn_id,
    completed=completed,
    interrupted=interrupted,
    model=agent.model,
    platform=getattr(agent, "platform", None) or "",
    messages=messages,
    final_response=final_response,
)
# If a plugin returned advice (dict with "advice" key), append it
# to the final response so the user sees it.
for _hook_result in _on_session_end_results:
    if isinstance(_hook_result, dict) and _hook_result.get("advice"):
        _advice_text = _hook_result["advice"]
        if final_response and isinstance(final_response, str):
            final_response = final_response.rstrip() + "\n\n📋 Raiden: " + _advice_text
            result["final_response"] = final_response
        break
```

### How to Re-apply After Update
```bash
# Check if patch is still applied
grep -c "messages=messages" ~/.hermes/hermes-agent/agent/turn_finalizer.py
# If 0, re-apply: open the file and make the edit above
```

## How It Works
1. Every Dimitri turn → on_session_end hook fires
2. Plugin receives messages + final_response
3. EmissionGate filters noise/duplicates/rate-limits
4. Advisor model (deepseek-v4-pro) reviews conversation
5. If issues found → returns {"advice": "[reasoning]...[fix]..."}
6. turn_finalizer appends "📋 Raiden: [advice]" to response
7. User sees advice after Dimitri's response

##Advisor Persona
Raiden is a strategic advisor, not a code reviewer. Covers:
- STRATEGY: scope creep, alignment with J's goals
- DECISIONS: better paths, over-commitment, over-engineering
- RISK: security, data loss, silent failures
- RESOURCES: token waste, complexity, redundancy
- COMMUNICATION: clarity at 3 AM
- EXECUTION: will this actually work?

## Maintenance
- Plugin files: persistent, survive restarts
- Config: persistent, survive restarts
- turn_finalizer.py patch: fragile, re-apply after `hermes update`
- Advisor model: change in advisor.py line 159 (`model=`)
- Persona: change in advisor.py `_ADVISOR_SYSTEM_PROMPT`
