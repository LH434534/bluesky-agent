"""News-to-post pipeline. The reason the account has something to say.

gather -> cluster -> corroborate -> pick -> draft -> factcheck -> queue
Everything downstream of here is just publishing.
"""

import json
import random
from datetime import datetime, timezone

from src import cfg as cfgmod
from src import dedupe
from src import ground
from src import models
from src import news
from src import queue as q
from src import timing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(e: dict) -> None:
    q.push("newslog", {**e, "at": _now()})
    q.cap("newslog", 500)


def _history() -> list[str]:
    posted = [r.get("headline", "") for r in q.read("posted") if r.get("headline")]
    used = [r.get("headline", "") for r in q.read("queue") if r.get("headline")]
    return posted + used


def run() -> dict:
    cfg = cfgmod.load()
    out = {"fetched": 0, "clusters": 0, "confirmed": 0, "queued": False, "reason": ""}

    items, nstats = news.gather(cfg)
    out["fetched"] = len(items)
    out["sources"] = nstats
    if not items:
        out["reason"] = "no news fetched"
        _log({"ok": False, **out})
        return out

    clusters = ground.cluster(items)
    out["clusters"] = len(clusters)

    confirmed = ground.corroborated(clusters, cfg)
    out["confirmed"] = len(confirmed)
    if not confirmed:
        out["reason"] = "nothing corroborated by enough sources"
        _log({"ok": False, **out})
        return out

    history = _history()
    fresh = [c for c in confirmed if not ground.already_used(c, history)]
    if not fresh:
        out["reason"] = "all confirmed stories already used"
        _log({"ok": False, **out})
        return out

    # rank by reach then break ties randomly — avoids always posting the same wire
    fresh.sort(key=lambda c: (c["reach"], c["size"]), reverse=True)
    pool = fresh[:6]
    random.shuffle(pool)

    voice = cfgmod.voice(cfg)
    picked = ground.pick(pool, cfg, voice)
    if not picked:
        out["reason"] = "picker returned nothing"
        return out

    for attempt in range(2):
        result = ground.draft(picked, cfg, voice)
        if result.get("ok"):
            if dedupe.too_similar(result["text"], [r.get("text", "") for r in q.read("posted")]):
                out["reason"] = "draft too similar to a past post"
                break
            q.push("queue", result)
            q.cap("queue")
            out.update({"queued": True, "text": result["text"],
                        "headline": result["headline"],
                        "domains": result["domains"],
                        "confidence": result["confidence"]})
            _log({"ok": True, **out})
            return out
        out["reason"] = result.get("reason", "draft rejected")
        q.push("rejected", {**result, "at": _now()})
        q.cap("rejected", 300)
        picked = random.choice(pool)

    _log({"ok": False, **out})
    return out


def should_post_now(cfg: dict) -> tuple[bool, str]:
    """Timing gate: only publish near historically good hours."""
    t = cfg.get("timing", {})
    if not t.get("enabled", True):
        return True, "timing disabled"
    if timing.in_peak(cfg):
        return True, "peak window"
    if random.random() < float(t.get("offpeak_probability", 0.25)):
        return True, "off-peak exploration"
    return False, "waiting for peak"


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
