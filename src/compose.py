"""Draft -> self-critique -> queue. Two model passes, one gate."""

import random
from datetime import datetime, timezone

from src import brain
from src import cfg as cfgmod
from src import dedupe
from src import queue as q

SYS_DRAFT = (
    "You are a post generator for a specific account.\n"
    "Voice and rules:\n{voice}\n"
    "Hard limits:\n"
    "- at most {max_chars} characters total\n"
    "- one self-contained post, no thread markers\n"
    "- no hashtags unless the topic demands one\n"
    "- reply with the post text ONLY, no quotes, no preamble\n"
)

SYS_CRITIC = (
    "You are a ruthless editor for a social account.\n"
    "Voice rules:\n{voice}\n"
    "Reply with exactly one line: PASS <reason> or FAIL <reason>.\n"
    "FAIL if: over {max_chars} chars, cliche opener, emoji wall, "
    "vague without a concrete detail, sounds like marketing, "
    "or reads like it was written by a committee.\n"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _recent_texts(limit: int) -> list[str]:
    posted = [r.get("text", "") for r in q.read("posted") if r.get("text")]
    pending = [r.get("text", "") for r in q.read("queue") if r.get("text")]
    return (posted + pending)[-limit:]


def _clean(text: str) -> str:
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    for bad in ("Post:", "Tweet:", "Here's the post"):
        if t.startswith(bad):
            t = t[len(bad) :].strip()
    return t


def draft_once(cfg: dict) -> dict:
    voice = cfgmod.voice(cfg)
    model = cfg["model"]
    max_chars = int(cfg["max_chars"])
    topic = random.choice(cfg["topics"])

    raw = brain.complete(
        SYS_DRAFT.format(voice=voice, max_chars=max_chars),
        f"Topic: {topic}\nWrite one post.",
        model,
        temperature=0.95,
    )
    text = _clean(raw)[:max_chars]

    if not text:
        return {"ok": False, "reason": "empty draft", "topic": topic}

    verdict = brain.complete(
        SYS_CRITIC.format(voice=voice, max_chars=max_chars),
        f"Post:\n{text}",
        model,
        temperature=0.2,
    )
    passed = verdict.upper().startswith("PASS")

    if not passed:
        return {"ok": False, "reason": verdict[:200], "text": text, "topic": topic}

    history = _recent_texts(int(cfg["dedupe_window"]))
    if dedupe.too_similar(text, history):
        return {"ok": False, "reason": "too similar to recent post", "text": text, "topic": topic}

    return {
        "ok": True,
        "text": text,
        "hash": dedupe.fingerprint(text),
        "topic": topic,
        "verdict": verdict[:200],
        "model": model,
        "createdAt": _now(),
    }


def run(attempts: int = 3) -> dict:
    cfg = cfgmod.load()
    for _ in range(attempts):
        result = draft_once(cfg)
        if result.get("ok"):
            q.push("queue", result)
            q.cap("queue")
            return {"queued": True, **result}
        q.push("rejected", {**result, "at": _now()})
        q.cap("rejected", 200)
    return {"queued": False, "reason": f"no draft passed after {attempts} attempts"}
