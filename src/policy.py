"""Anti-ban / anti-spam governor.

Every outbound action passes through here. Nothing is unbounded.
Rules: quotas per day, minimum gaps, burst control, human sleep rhythm,
jitter, random skip, kill switch, circuit breaker on repeated errors.
"""

import json
import os
import pathlib
import random
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state"

# Write-through memo. Single-process runner: after a write we already know the
# truth, re-reading is redundant and can return stale bytes on network FS.
_CACHE: dict | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------- ledger ----------

def read_ledger() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    p = STATE / "ledger.json"
    if not p.exists():
        return {"actions": [], "errors": [], "followed": {}, "last": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return {"actions": [], "errors": [], "followed": {}, "last": {}}
    for k, default in (("actions", []), ("errors", []), ("followed", {}), ("last", {})):
        if not isinstance(data.get(k), type(default)):
            data[k] = default
    return data


def write_ledger(led: dict) -> None:
    """Single atomic write. Truncate BEFORE persisting, never after."""
    global _CACHE
    _CACHE = led
    p = STATE / "ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    led["actions"] = led["actions"][-5000:]
    led["errors"] = led["errors"][-500:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def record(kind: str, target: str = "", ok: bool = True, error: str = "") -> None:
    led = read_ledger()
    now = _now()
    led["actions"].append(
        {"kind": kind, "target": target, "ok": ok, "at": _iso(now)}
    )
    if ok:
        led["last"][kind] = _iso(now)
    else:
        led["errors"].append({"kind": kind, "error": error[:300], "at": _iso(now)})
    write_ledger(led)


def mark_followed(did: str, handle: str) -> None:
    led = read_ledger()
    led["followed"][did] = {"handle": handle, "at": _iso(_now())}
    write_ledger(led)


def unfollowed(did: str) -> None:
    led = read_ledger()
    led["followed"].pop(did, None)
    write_ledger(led)


# ---------- counting ----------

def _count_since(actions: list, kind: str, since: datetime) -> int:
    n = 0
    for a in actions:
        if a.get("kind") != kind:
            continue
        at = _parse(a.get("at", ""))
        if at and at >= since:
            n += 1
    return n


def count_today(kind: str) -> int:
    led = read_ledger()
    return _count_since(led["actions"], kind, _now() - timedelta(days=1))


def count_for_author(kind: str, did: str) -> int:
    led = read_ledger()
    since = _now() - timedelta(days=1)
    return sum(
        1
        for a in led["actions"]
        if a.get("kind") == kind
        and a.get("target", "").endswith(did)
        and (_parse(a.get("at", "")) or since) >= since
    )


def seconds_since_last(kind: str) -> float:
    led = read_ledger()
    last = _parse(led["last"].get(kind, ""))
    if not last:
        return 1e9
    return (_now() - last).total_seconds()


def recent_errors(limit: int = 5) -> list:
    led = read_ledger()
    return led["errors"][-limit:]


# ---------- human rhythm ----------

def local_hour(cfg: dict) -> int:
    off = int(cfg["human"]["timezone_offset"])
    return (_now() + timedelta(hours=off)).hour


def activity_scale(cfg: dict) -> float:
    h = local_hour(cfg)
    start, end = cfg["human"]["active_hours"]
    if start <= h < end:
        return 1.0
    return float(cfg["human"]["night_activity_scale"])


def _jitter(base: float, pct: float) -> float:
    return base * (1.0 + random.uniform(-pct / 100.0, pct / 100.0))


# ---------- gates ----------

def kill_switch_on(cfg: dict) -> bool:
    return (STATE / cfg["safety"]["kill_switch_file"]).exists()


def circuit_open(cfg: dict) -> bool:
    errs = recent_errors(int(cfg["safety"]["consecutive_error_limit"]))
    limit = int(cfg["safety"]["consecutive_error_limit"])
    if len(errs) < limit:
        return False
    tail = errs[-limit:]
    last = _parse(tail[-1].get("at", ""))
    if not last:
        return False
    cooldown = timedelta(minutes=int(cfg["safety"]["cooldown_after_error_minutes"]))
    return (_now() - last) < cooldown


def daily_budget(kind: str, cfg: dict) -> int:
    per_day = int(cfg[kind]["per_day"])
    scale = activity_scale(cfg)
    return max(1, int(per_day * scale))


def allow(kind: str, cfg: dict, target_did: str = "") -> tuple[bool, str]:
    """Single gate for every action. Returns (allowed, reason)."""
    if kill_switch_on(cfg):
        return False, "kill switch present"
    if circuit_open(cfg):
        return False, "circuit breaker open"
    if not cfg.get(kind, {}).get("enabled", True):
        return False, f"{kind} disabled"

    if random.random() < float(cfg["human"]["skip_probability"]):
        return False, "random human skip"

    used = count_today(kind)
    budget = daily_budget(kind, cfg)
    if used >= budget:
        return False, f"daily quota {used}/{budget}"

    sec_cfg = cfg[kind]
    if "min_gap_minutes" in sec_cfg:
        gap = _jitter(int(sec_cfg["min_gap_minutes"]) * 60, float(cfg["human"]["jitter_pct"]))
        if seconds_since_last(kind) < gap:
            return False, f"min gap not met ({kind})"
    elif "min_gap_seconds" in sec_cfg:
        gap = _jitter(int(sec_cfg["min_gap_seconds"]), float(cfg["human"]["jitter_pct"]))
        if seconds_since_last(kind) < gap:
            return False, f"min gap not met ({kind})"

    if kind == "like":
        burst = int(sec_cfg["burst_max"])
        recent = [a for a in read_ledger()["actions"] if a.get("kind") == "like"][-burst:]
        if len(recent) == burst:
            first = _parse(recent[0].get("at", ""))
            if first and (_now() - first) < timedelta(
                minutes=int(sec_cfg["burst_cooldown_minutes"])
            ):
                return False, "burst limit reached"

    if target_did:
        cap_key = f"max_{kind}s_per_author_per_day"
        cap = int(cfg["safety"].get(cap_key, 99))
        if count_for_author(kind, target_did) >= cap:
            return False, f"author cap reached ({kind})"

    return True, "ok"
