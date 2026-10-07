"""Organic growth engine.

Growth on Bluesky is mostly: be genuinely useful in replies to accounts your
audience already reads, and post things worth reposting. This module finds
those accounts and those moments — and spends nothing on ads.

No follow-churn, no follow-back farming, no mass DMs. Those get accounts
flagged and they don't build an audience anyway.
"""

import json
import random
from datetime import datetime, timezone

from src import models
from src import policy
from src import social
from src import spam

SYS_TARGET = (
    "Decide if this account is a good place for us to be visible.\n"
    "Our interests: {topics}\n"
    "Good: active in our area, posts get real replies, not a spam account, "
    "not a celeb with 2M followers who will never see us.\n"
    "Reply ONE word: YES or NO."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_targets() -> dict:
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "state" / "targets.json"
    if not p.exists():
        return {"accounts": [], "updated": ""}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"accounts": [], "updated": ""}


def write_targets(data: dict) -> None:
    import os
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "state" / "targets.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def discover(cfg: dict, jwt: str) -> list[dict]:
    """Find accounts worth being visible around. Cheap: search by term."""
    found: dict[str, dict] = {}
    terms = cfg.get("growth", {}).get("discovery_terms") or cfg["discovery"]["search_terms"]
    for term in random.sample(terms, min(3, len(terms))):
        try:
            posts = social.search_posts(jwt, term, limit=25)
        except social.BskyError:
            continue
        for p in posts:
            a = p.get("author", {}) or {}
            did = a.get("did", "")
            if not did:
                continue
            followers = int(a.get("followersCount") or 0)
            lo = int(cfg.get("growth", {}).get("min_followers", 300))
            hi = int(cfg.get("growth", {}).get("max_followers", 120000))
            if not (lo <= followers <= hi):
                continue
            clean, _ = spam.scan(
                f'{a.get("displayName","")} {a.get("description","")} {p.get("record",{}).get("text","")}',
                cfg, did,
            )
            if not clean:
                continue
            if did not in found:
                found[did] = {
                    "did": did,
                    "handle": a.get("handle", ""),
                    "followers": followers,
                    "description": (a.get("description") or "")[:200],
                    "score": 0,
                    "addedAt": _now(),
                }
    return list(found.values())


def vet(candidates: list[dict], cfg: dict) -> list[dict]:
    from src import llm
    if llm.provider() == "none":
        return candidates[: int(cfg.get("growth", {}).get("max_new_targets_per_run", 8))]
    kept = []
    for c in candidates:
        try:
            v = models.complete(
                "filter",
                SYS_TARGET.format(topics=", ".join(cfg["topics"])),
                f'@{c["handle"]} ({c["followers"]} followers): {c["description"]}',
                cfg,
                temperature=0.1,
            )
        except (models.BudgetExceeded, llm.NoProvider):
            break
        if v.strip().upper().startswith("YES"):
            c["score"] = 1
            kept.append(c)
    return kept


def refresh_targets(cfg: dict, jwt: str, max_new: int = 12) -> int:
    data = read_targets()
    existing = {a["did"] for a in data["accounts"]}
    cand = [c for c in discover(cfg, jwt) if c["did"] not in existing]
    if not cand:
        return 0
    random.shuffle(cand)
    kept = vet(cand[: max_new * 2], cfg)[:max_new]
    data["accounts"] = (data["accounts"] + kept)[-200:]
    data["updated"] = _now()
    write_targets(data)
    return len(kept)


def watch_targets(cfg: dict, jwt: str, limit: int = 20) -> list:
    """Recent posts from accounts we want to be visible around."""
    data = read_targets()
    if not data["accounts"]:
        return []
    picks = random.sample(data["accounts"], min(4, len(data["accounts"])))
    out = []
    for a in picks:
        try:
            feed = social.author_feed(jwt, a["handle"] or a["did"], limit=5)
        except social.BskyError:
            continue
        for item in feed:
            post = item.get("post", item)
            rec = post.get("record", post)
            text = (rec.get("text") or "").strip()
            if not text:
                continue
            clean, _ = spam.scan(text, cfg)
            if not clean:
                continue
            if int(rec.get("replyCount") or 0) > int(cfg.get("growth", {}).get("skip_if_replies_over", 200)):
                continue
            out.append(item)
    return out[:limit]
