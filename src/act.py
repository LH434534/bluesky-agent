"""Autonomous decision engine.

One run = one human-like session: read the timeline, pick a few things to
engage with, maybe post. Every action goes through policy.allow + spam.scan.
Nothing executes without both gates green.
"""

import json
import random
from datetime import datetime, timezone

from src import bluesky
from src import cfg as cfgmod
from src import growth
from src import models
from src import policy
from src import queue as q
from src import social
from src import spam
from src import timing

SYS_REPLY = (
    "You are replying as a real person on Bluesky.\n"
    "Voice:\n{voice}\n"
    "Rules: max {max_chars} chars. Add something concrete or shut up. "
    "No agreements like 'great post'. No emoji. No hashtags. "
    "Reply with the text ONLY."
)

SYS_PICK = (
    "You score how well this post fits our interests.\n"
    "Interests: {topics}\n"
    "Reply ONE line: score 0.0-1.0 then a 5-word reason. Nothing else."
)

SYS_FOLLOW = (
    "Decide if this account is worth following.\n"
    "Interests: {topics}\n"
    "Reply one word: YES or NO."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(entry: dict) -> None:
    q.push("activity", {**entry, "at": _now()})
    q.cap("activity", 2000)


def _text(item: dict) -> str:
    post = item.get("post", item)
    rec = post.get("record", post)
    return (rec.get("text") or "").strip()


def _author(item: dict) -> dict:
    post = item.get("post", item)
    return post.get("author", {}) or {}


def _own(post: dict, my_did: str) -> bool:
    return _author(post).get("did") == my_did


def gather(cfg: dict, jwt: str, my_did: str) -> list:
    """Pull candidate posts from timeline + search. Filter out own posts."""
    pool = []
    size = int(cfg["discovery"]["sample_size"])

    try:
        pool += social.timeline(jwt, limit=size)
    except bluesky.BskyError as e:
        _log({"kind": "read", "ok": False, "error": str(e)[:200]})

    want = max(0, 24 - len(pool))
    if want:
        terms = cfg["discovery"]["search_terms"]
        for term in random.sample(terms, min(3, len(terms))):
            try:
                pool += [{"post": p} for p in social.search_posts(jwt, term, limit=15)]
            except bluesky.BskyError:
                continue
            if len(pool) >= 24:
                break

    return [p for p in pool if not _own(p, my_did) and _text(p)]


def score(cfg: dict, text: str) -> float:
    """Cheap lexical score. Model pass only when worth the token."""
    base = spam.relevance(cfg, text)
    clean, _ = spam.scan(text, cfg)
    if not clean:
        return 0.0
    return min(1.0, base * 2.2)


def do_reply(cfg: dict, jwt: str, item: dict) -> bool:
    post = item.get("post", item)
    author = _author(item)
    did = author.get("did", "")
    text = _text(item)

    ok, why = policy.allow("reply", cfg, did)
    if not ok:
        _log({"kind": "reply", "ok": False, "reason": why})
        return False

    clean, why = spam.scan(text, cfg, did)
    if not clean:
        _log({"kind": "reply", "ok": False, "reason": why})
        return False

    if score(cfg, text) < float(cfg["reply"]["require_relevance"]):
        _log({"kind": "reply", "ok": False, "reason": "low relevance"})
        return False

    from src import llm
    if llm.provider() == "none":
        _log({"kind": "reply", "ok": False, "reason": "no model provider"})
        return False
    try:
        draft = models.complete(
            "reply",
            SYS_REPLY.format(voice=cfgmod.voice(cfg), max_chars=cfg["reply"]["max_chars"]),
            f"Post by @{author.get('handle','someone')}:\n{text}\n\nYour reply:",
            cfg,
            temperature=0.85,
        ).strip()[: int(cfg["reply"]["max_chars"])]
    except models.BudgetExceeded as e:
        _log({"kind": "reply", "ok": False, "reason": str(e)[:200]})
        return False

    clean_draft, why = spam.scan(draft, cfg)
    if not clean_draft or not draft:
        _log({"kind": "reply", "ok": False, "reason": f"own draft rejected: {why}"})
        return False

    from src import dedupe
    own = [r.get("text", "") for r in q.read("posted") if r.get("text")]
    own += [a.get("text", "") for a in q.read("activity")
            if a.get("kind") == "reply" and a.get("text")]
    if dedupe.too_similar(draft, own[-40:], threshold=0.55):
        _log({"kind": "reply", "ok": False, "reason": "reply repeats something we already said"})
        return False

    if cfg.get("dry_run"):
        _log({"kind": "reply", "ok": True, "dry": True, "text": draft})
        return True

    try:
        uri, cid = post.get("uri"), post.get("cid")
        social.reply(draft, uri, cid, uri, cid, cfg["langs"])
        policy.record("reply", did, True)
        _log({"kind": "reply", "ok": True, "text": draft, "to": author.get("handle")})
        return True
    except bluesky.BskyError as e:
        policy.record("reply", did, False, str(e))
        _log({"kind": "reply", "ok": False, "error": str(e)[:200]})
        return False


def do_like(cfg: dict, item: dict) -> bool:
    author = _author(item)
    did = author.get("did", "")
    text = _text(item)

    ok, why = policy.allow("like", cfg, did)
    if not ok:
        return False

    clean, why = spam.scan(text, cfg, did)
    if not clean:
        _log({"kind": "like", "ok": False, "reason": why})
        return False

    if score(cfg, text) < float(cfg.get("like", {}).get("min_relevance", 0.12)):
        return False

    if cfg.get("dry_run"):
        _log({"kind": "like", "ok": True, "dry": True})
        return True

    try:
        uri, cid = item.get("post", item).get("uri"), item.get("post", item).get("cid")
        social.like(uri, cid)
        policy.record("like", did, True)
        _log({"kind": "like", "ok": True, "to": author.get("handle")})
        return True
    except bluesky.BskyError as e:
        policy.record("like", did, False, str(e))
        return False


def do_repost(cfg: dict, item: dict) -> bool:
    author = _author(item)
    did = author.get("did", "")
    text = _text(item)

    ok, why = policy.allow("repost", cfg, did)
    if not ok:
        return False
    if score(cfg, text) < 0.5:
        return False

    if cfg.get("dry_run"):
        _log({"kind": "repost", "ok": True, "dry": True})
        return True
    try:
        post = item.get("post", item)
        social.repost(post.get("uri"), post.get("cid"))
        policy.record("repost", did, True)
        _log({"kind": "repost", "ok": True, "to": author.get("handle")})
        return True
    except bluesky.BskyError as e:
        policy.record("repost", did, False, str(e))
        return False


def do_follow(cfg: dict, item: dict) -> bool:
    author = _author(item)
    did = author.get("did", "")
    if not did:
        return False

    ok, why = policy.allow("follow", cfg)
    if not ok:
        return False
    led = policy.read_ledger()
    if did in led["followed"]:
        return False

    clean, why = spam.scan(
        f'{author.get("description","")} {_text(item)}', cfg, did
    )
    if not clean:
        _log({"kind": "follow", "ok": False, "reason": f"author rejected: {why}",
              "to": author.get("handle")})
        return False

    from src import llm
    if llm.provider() == "none":
        _log({"kind": "follow", "ok": False, "reason": "no model provider"})
        return False
    try:
        verdict = models.complete(
            "follow",
            SYS_FOLLOW.format(topics=", ".join(cfg["topics"])),
            f"@{author.get('handle')}: {author.get('description','')}\nRecent: {_text(item)[:200]}",
            cfg,
            temperature=0.2,
        ).strip().upper()
    except models.BudgetExceeded as e:
        _log({"kind": "follow", "ok": False, "reason": str(e)[:200]})
        return False
    if not verdict.startswith("YES"):
        _log({"kind": "follow", "ok": False, "reason": f"model said {verdict[:20]}"})
        return False

    if cfg.get("dry_run"):
        _log({"kind": "follow", "ok": True, "dry": True, "to": author.get("handle")})
        return True
    try:
        social.follow(did)
        policy.record("follow", did, True)
        policy.mark_followed(did, author.get("handle", ""))
        _log({"kind": "follow", "ok": True, "to": author.get("handle")})
        return True
    except bluesky.BskyError as e:
        policy.record("follow", did, False, str(e))
        return False


def timing_ok(cfg: dict) -> tuple[bool, str]:
    from src import fame
    return fame.should_post_now(cfg)


def do_post(cfg: dict) -> bool:
    from src import compose

    ok, why = timing_ok(cfg)
    if not ok:
        _log({"kind": "post", "ok": False, "reason": why})
        return False
    ok, why = policy.allow("post", cfg)
    if not ok:
        _log({"kind": "post", "ok": False, "reason": why})
        return False
    pending = q.read("queue")
    if not pending:
        from src import fame
        out = fame.run()          # real news works with or without a model
        if not out.get("queued"):
            out = compose.run(attempts=2)
        if not out.get("queued"):
            _log({"kind": "post", "ok": False, "reason": out.get("reason", "no draft")})
            return False
        pending = q.read("queue")

    item = pending[0]
    text = item.get("text", "")
    link = item.get("url") or ""
    if cfg.get("dry_run"):
        _log({"kind": "post", "ok": True, "dry": True, "text": text, "link": link})
        return True
    try:
        res = bluesky.post(text, cfg["langs"], link or None)
        q.take("queue", 1)
        q.push("posted", {**item, "uri": res.get("uri"), "postedAt": _now()})
        q.cap("posted", 2000)
        policy.record("post", "", True)
        _log({"kind": "post", "ok": True, "text": text})
        return True
    except bluesky.BskyError as e:
        policy.record("post", "", False, str(e))
        _log({"kind": "post", "ok": False, "error": str(e)[:200]})
        return False


def run() -> dict:
    cfg = cfgmod.load()
    summary = {"ran": [], "skipped": [], "errors": 0}

    if policy.kill_switch_on(cfg):
        summary["skipped"].append("kill switch")
        return summary
    if policy.circuit_open(cfg):
        summary["skipped"].append("circuit open")
        return summary
    if int(cfg["autonomy"]) == 0:
        summary["skipped"].append("autonomy 0")
        return summary

    try:
        my_did = social.my_did()
        try:
            _, jwt0 = social._auth()
            timing.observe(social.author_feed(jwt0, my_did, limit=20), cfg)
        except bluesky.BskyError:
            pass
    except bluesky.BskyError as e:
        policy.record("auth", "", False, str(e))
        summary["errors"] += 1
        return summary

    try:
        _, jwt = social._auth()
    except bluesky.BskyError:
        return summary

    pool = gather(cfg, jwt, my_did)
    summary["pool"] = len(pool)

    if cfg.get("growth", {}).get("enabled", True):
        try:
            pool += growth.watch_targets(cfg, jwt, limit=12)
        except bluesky.BskyError:
            pass
        if random.random() < 0.25:
            try:
                n = growth.refresh_targets(
                    cfg, jwt, int(cfg["growth"].get("max_new_targets_per_run", 8))
                )
                if n:
                    _log({"kind": "discover", "ok": True, "reason": f"{n} new targets"})
            except bluesky.BskyError:
                pass

    if not pool:
        summary["skipped"].append("empty timeline")
        summary["top_scores"] = []
        return summary

    summary["top_scores"] = [round(score(cfg, _text(i)), 2) for i in pool[:8]]

    random.shuffle(pool)
    pool.sort(key=lambda i: score(cfg, _text(i)), reverse=True)
    budget = int(cfg["human"]["max_actions_per_run"])

    acted = 0
    for item in pool[: budget * 3]:
        if acted >= budget:
            break
        roll = random.random()
        s = score(cfg, _text(item))

        if s > 0.62 and roll < 0.34 and do_reply(cfg, jwt, item):
            acted += 1
        elif s > 0.45 and roll < 0.5 and do_follow(cfg, item):
            acted += 1
        elif s > 0.5 and roll < 0.62 and do_repost(cfg, item):
            acted += 1
        elif s > 0.10 and do_like(cfg, item):
            acted += 1

    if do_post(cfg):
        acted += 1

    from src import llm
    summary["llm"] = llm.provider()
    summary["acted"] = acted
    summary["ran"] = [a["kind"] for a in q.read("activity")[-acted:]] if acted else []
    if acted == 0:
        summary["why_idle"] = [a.get("reason") for a in q.read("activity")[-6:]
                               if a.get("reason")]
    return summary
