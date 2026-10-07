"""Multi-model router over GitHub Models.

Free tier is tight and tiered:
  high complexity  (gpt-4o, gpt-4.1, llama-3.3-70b)  -> 10 RPM, 50 RPD
  low complexity   (gpt-4o-mini, gpt-4.1-mini, phi-4-mini) -> 15 RPM, 150 RPD
  reasoning        (deepseek-r1, o3-mini, o4-mini)   -> 1-2 RPM, 8-15 RPD

So: cheap model for filtering and scoring, strong model only for the final
draft, reasoning model almost never. Budget is tracked per day in the ledger
and enforced before every call.
"""

import os
from datetime import datetime, timedelta, timezone

from src import brain
from src import policy

# id -> tier. daily caps are conservative, well under published limits.
CATALOG = {
    "openai/gpt-4.1-mini": ("low", 120),
    "openai/gpt-4o-mini": ("low", 120),
    "openai/gpt-4.1": ("high", 40),
    "openai/gpt-4o": ("high", 40),
    "meta/Llama-3.3-70B-Instruct": ("high", 40),
    "microsoft/Phi-4": ("low", 120),
    "deepseek/DeepSeek-R1": ("reasoning", 6),
    "openai/o3-mini": ("reasoning", 6),
}

ROLES = {
    "filter": "openai/gpt-4.1-mini",
    "score": "openai/gpt-4o-mini",
    "critic": "openai/gpt-4o-mini",
    "draft": "openai/gpt-4.1",
    "follow": "openai/gpt-4o-mini",
    "reply": "openai/gpt-4.1-mini",
    "angle": "openai/gpt-4.1",
    "reason": "deepseek/DeepSeek-R1",
}


class BudgetExceeded(RuntimeError):
    pass


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def usage() -> dict:
    led = policy.read_ledger()
    return led.get("models", {}).get(_today(), {})


def spent(model: str) -> int:
    return int(usage().get(model, 0))


def cap(model: str) -> int:
    return CATALOG.get(model, ("low", 50))[1]


def _bump(model: str, n: int = 1) -> None:
    led = policy.read_ledger()
    day = _today()
    models = led.setdefault("models", {})
    # keep only today and yesterday, prune older
    for d in list(models):
        if d < (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"):
            models.pop(d, None)
    row = models.setdefault(day, {})
    row[model] = int(row.get(model, 0)) + n
    policy.write_ledger(led)


def resolve(role: str, cfg: dict) -> str:
    override = cfg.get("models", {}).get("roles", {}) or {}
    return override.get(role) or ROLES.get(role, "openai/gpt-4.1-mini")


def chain(role: str, cfg: dict) -> list[str]:
    """Preferred model first, then same-tier fallbacks, then the floor model."""
    primary = resolve(role, cfg)
    tier = CATALOG.get(primary, ("low", 50))[0]
    out = [primary]
    for m, (t, _) in CATALOG.items():
        if m != primary and t == tier:
            out.append(m)
    floor = cfg.get("models", {}).get("floor", "openai/gpt-4o-mini")
    if floor not in out:
        out.append(floor)
    return out


def complete(role: str, system: str, user: str, cfg: dict,
             temperature: float = 0.9) -> str:
    """Call with budget enforcement and automatic downgrade on 429/exhaustion."""
    last_err = None
    for model in chain(role, cfg):
        if spent(model) >= cap(model):
            last_err = f"{model} daily cap reached"
            continue
        try:
            out = brain.complete(system, user, model, temperature=temperature)
            _bump(model)
            return out
        except brain.NoProvider:
            raise
        except brain.BrainError as e:
            last_err = str(e)
            msg = last_err.lower()
            if "429" in msg or "rate" in msg or "quota" in msg:
                _bump(model, cap(model))  # burn the cap, stop retrying today
                continue
            continue
    if isinstance(last_err, str) and not last_err:
        raise BudgetExceeded(f"no model available for role {role}")
    raise BudgetExceeded(f"no model available for role {role}: {last_err}")
