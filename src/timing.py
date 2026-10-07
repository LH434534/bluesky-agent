"""Engagement learning. When does this account actually get seen?

Bluesky has no free analytics API, so we measure what we can observe:
likes/replies on our own posts, via getAuthorFeed on ourselves. Cheap, one
call, and enough to learn a rhythm.
"""

import json
import pathlib
import statistics
from datetime import datetime, timedelta, timezone

from src import policy

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read() -> dict:
    p = STATE / "timing.json"
    if not p.exists():
        return {"hours": {}, "updated": ""}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"hours": {}, "updated": ""}


def write(data: dict) -> None:
    p = STATE / "timing.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import os
    os.replace(tmp, p)


def observe(posts: list, cfg: dict) -> None:
    """Record engagement per local hour from our own recent posts."""
    off = int(cfg["human"]["timezone_offset"])
    data = read()
    hours = data.setdefault("hours", {})
    for p in posts:
        rec = p.get("post", {}).get("record", p.get("post", p))
        created = rec.get("createdAt") or p.get("post", {}).get("indexedAt", "")
        if not created:
            continue
        try:
            then = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        hour = (then + __import__("datetime").timedelta(hours=off)).hour
        likes = int(p.get("post", {}).get("likeCount") or 0)
        replies = int(p.get("post", {}).get("replyCount") or 0)
        reposts = int(p.get("post", {}).get("repostCount") or 0)
        score = likes + 2 * replies + 3 * reposts
        row = hours.setdefault(str(hour), {"n": 0, "total": 0})
        row["n"] += 1
        row["total"] += score
    data["updated"] = _now().isoformat().replace("+00:00", "Z")
    write(data)


def best_hours(cfg: dict, top: int = 4) -> list[int]:
    """Hours with the best average engagement. Falls back to configured peak."""
    data = read()
    hours = data.get("hours", {})
    scored = []
    for h, row in hours.items():
        n = int(row.get("n", 0))
        if n < int(cfg.get("timing", {}).get("min_samples", 3)):
            continue
        scored.append((int(h), row["total"] / n))
    if not scored:
        return list(cfg.get("timing", {}).get("fallback_hours", [9, 12, 18, 21]))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [h for h, _ in scored[:top]]


def in_peak(cfg: dict, slack: int = 1) -> bool:
    """Is now close to a historically good hour?"""
    off = int(cfg["human"]["timezone_offset"])
    hour = (_now() + timedelta(hours=off)).hour
    peaks = best_hours(cfg)
    return any(abs((hour - p) % 24) <= slack for p in peaks)


def boost(cfg: dict) -> float:
    """Multiplier for posting probability. 1.0 = neutral."""
    scale = float(cfg.get("timing", {}).get("peak_boost", 1.6))
    return scale if in_peak(cfg) else 1.0


def summary() -> str:
    data = read()
    hours = data.get("hours", {})
    rows = sorted(hours.items(), key=lambda kv: kv[0])
    if not rows:
        return "sem dados"
    return " ".join(
        f"{h}h:{row['total'] / max(1, row['n']):.1f}(n={row['n']})" for h, row in rows
    )
