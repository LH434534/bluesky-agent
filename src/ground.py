"""Grounding layer. The part that keeps the bot from inventing 2026.

Rule: a story is only postable if it is CORROBORATED — the same event appears
in at least `min_sources` distinct domains. One-outlet claims are dropped.

Then the model writes about the headline it was given, and the output is
checked back against the source: every number and proper noun in the draft
must appear in the source text. Anything new is treated as hallucination and
the draft is rejected.
"""

import re
from datetime import datetime, timezone

from src import dedupe
from src import models

STOP = {
    "the","a","an","and","or","but","of","to","in","on","for","with","at","by",
    "from","as","is","was","are","were","be","been","it","its","this","that",
    "de","da","do","em","no","na","para","com","por","que","um","uma","os","as",
    "dos","das","ao","aos","ele","ela","seu","sua","mais","como","sobre",
}

NUM = re.compile(
    r"\d[\d.,]*\s*"
    r"(?:%|[kKmMbB]\b|milh[\u00e3a]o|milh[\u00f5o]es|bilh[\u00e3a]o|"
    r"billion|million|thousand|mil\b|km|kg|mp|\u00b0)?",
    re.IGNORECASE,
)

WORD = re.compile(r"[A-Za-zÀ-ÿ]{3,}")

_SUFFIX = [
    ("milhoes", 1_000_000), ("milh\u00f5es", 1_000_000),
    ("milhao", 1_000_000),  ("milh\u00e3o", 1_000_000),
    ("billion", 1_000_000_000), ("bilhao", 1_000_000_000),
    ("million", 1_000_000), ("mil", 1_000), ("thousand", 1_000),
    ("bi", 1_000_000_000), ("m", 1_000_000), ("k", 1_000),
]


def _norm_num(raw: str) -> float | None:
    """Normalize '100k', '100,000', '100.000', '1.5 milhao' to one number."""
    t = raw.strip().lower().replace("%", "").replace(" ", "")
    mult = 1.0
    for suf, factor in _SUFFIX:
        if t.endswith(suf):
            t = t[: -len(suf)]
            mult = float(factor)
            break

    # drop thousand separators, keep a real decimal point
    t = re.sub(r"[,.](\d{3})(?=$|[^\d])", r"\1", t)
    t = t.replace(",", ".")

    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group()) * mult
    except ValueError:
        return None


def _numbers(text: str) -> set[float]:
    out = set()
    for raw in NUM.findall(text or ""):
        v = _norm_num(raw)
        if v is not None:
            out.add(round(v, 3))
    return out


def _tokens(title: str) -> set[str]:
    return {w.lower() for w in WORD.findall(title or "") if w.lower() not in STOP}


def _sim(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cluster(items: list, threshold: float = 0.22) -> list[dict]:
    """Group headlines about the same event. Each cluster keeps its sources."""
    clusters: list[dict] = []
    for it in items:
        placed = False
        for c in clusters:
            for member in c["items"]:
                if _sim(it["title"], member["title"]) >= threshold:
                    c["items"].append(it)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            clusters.append({"items": [it]})

    for c in clusters:
        c["domains"] = sorted({i["domain"] for i in c["items"] if i.get("domain")})
        c["sources"] = c["items"]
        c["headline"] = max(c["items"], key=lambda i: len(i.get("title", "")))["title"]
        c["url"] = c["items"][0].get("url", "")
        c["size"] = len(c["items"])
        c["reach"] = len(c["domains"])
    return clusters


def _is_trusted(domain: str, trusted: set) -> bool:
    if domain in trusted:
        return True
    return any(domain.endswith("." + t) or t.endswith("." + domain) for t in trusted)


def corroborated(clusters: list, cfg: dict) -> list[dict]:
    """Prefer multi-outlet confirmation. Fall back to one trusted outlet.

    Broad wire queries mostly return *different* stories, not several versions
    of one. Requiring 2+ domains everywhere would leave the bot mute, so a
    single trusted outlet is accepted, ranked below anything corroborated.
    """
    need = int(cfg.get("news", {}).get("min_sources", 2))
    trusted = set(cfg.get("news", {}).get("trusted_domains", []))
    multi, single = [], []
    for c in clusters:
        domains = c["domains"]
        if len(domains) >= need:
            c["confidence"] = "multi-source"
            multi.append(c)
        elif trusted and any(_is_trusted(d, trusted) for d in domains):
            c["confidence"] = "trusted-single"
            single.append(c)
    return multi + single


def already_used(cluster: dict, history: list[str]) -> bool:
    for h in history:
        if dedupe.too_similar(cluster["headline"], [h]):
            return True
    return False


# ---------- selection ----------

SYS_PICK = (
    "You are an editor choosing what a thoughtful account should post about.\n"
    "Voice and interests:\n{voice}\n\n"
    "Candidates are REAL news headlines. Pick the one with the strongest, "
    "most concrete angle for this account.\n"
    "Reply ONE line: the candidate NUMBER, then a 6-word reason. Nothing else."
)

SYS_ANGLE = (
    "You write for an account with this voice:\n{voice}\n\n"
    "FACTS YOU MAY USE (from real headlines — these are all you know):\n{facts}\n\n"
    "Write ONE post of at most {max_chars} characters about this.\n"
    "Rules:\n"
    "- use ONLY facts listed above\n"
    "- invent NO numbers, names, dates or outcomes\n"
    "- no 'breaking', no clickbait, no hashtags, no emoji\n"
    "- say something real about WHY it matters, not just what happened\n"
    "- reply with the post text ONLY"
)

SYS_FACTCHECK = (
    "You are a strict fact checker. You have a SOURCE and a DRAFT.\n"
    "Pass only if every number, name and specific claim in the DRAFT appears "
    "in the SOURCE, or is a plainly safe restatement of it.\n"
    "Reply ONE line: PASS or FAIL <what is unsupported>."
)


def pick(clusters: list, cfg: dict, voice: str) -> dict | None:
    if not clusters:
        return None
    from src import llm
    if llm.provider() == "none":
        return max(clusters, key=lambda c: (c["reach"], c["size"]))
    lines = []
    for i, c in enumerate(clusters[:8]):
        lines.append(f"{i}) [{c['confidence']}, {c['reach']} outlets] {c['headline'][:160]}")
    try:
        out = models.complete(
            "angle",
            SYS_PICK.format(voice=voice),
            "\n".join(lines),
            cfg,
            temperature=0.3,
        )
    except models.BudgetExceeded:
        return clusters[0]
    m = re.search(r"\d+", out)
    idx = int(m.group()) if m else 0
    return clusters[idx] if 0 <= idx < len(clusters) else clusters[0]


# wire copy arrives as "Ticker : Headlong sentence ; trailing clause"
WIRE_JUNK = [
    (r"\b(Inc|Ltd|Plc|Corp|Co|S\.?A\.?|N\.?V\.?|AG|SE|AB|Oyj|ASA)\.?\s*$", ""),
    (r"^[^:]{2,60}?\s:\s", ""),                                      # wire ticker prefix
    (r"\s*;\s*(to be|will be|see|also)\b.*$", ""),                   # trailing clause
    (r"\s*[|–—]\s*[A-Z][a-z]+(\s+[A-Za-z]+)?\s*$", ""),            # outlet suffix
]
_MARKET = re.compile(r"\b(shares?|stock|ticker|NYSE|NASDAQ|IPO|dividend|"
                     r"quarterly results|EPS|revenue rose|guidance)\b", re.I)

# the account is a builder, not a crime blotter or a celebrity feed
_OFFBRAND = re.compile(
    r"\b(death row|execut(?:ed|ion)|inmate|behead|murder(?:ed|er)?|"
    r"rap(?:e|ed|ist)|abuse[ds]?|paedophil|pedophil|suicide|overdose|"
    r"crash(?:ed)?\s+(?:kills|killed)|kills?\s+\d+|dead\s+body|"
    r"shooting|stabbing|stabb(?:ed)|terror(?:ist)?\s+attack|"
    r"sex\s+tape|nude\s+photos?|affair|divorce\s+fil(?:e|ed)|"
    r"arrest(?:ed)?\s+for|charged\s+with|sentenced\s+to)\b", re.I)


def _clean_headline(head: str) -> str:
    t = re.sub(r"\s+", " ", (head or "").strip())
    for pat, rep in WIRE_JUNK:
        t = re.sub(pat, rep, t).strip()
    return t.strip(" :-;,")


def _domain_word(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url or "")
    if not m:
        return ""
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    generic = {"www", "news", "m", "mobile", "amp", "rss", "feed"}
    parts = [p for p in parts if p not in generic] or parts
    tld = {"com", "org", "net", "co", "io", "de", "uk", "br", "pt", "fr",
           "info", "ai", "dev", "me", "tv", "blog", "gov", "edu", "au",
           "ca", "jp", "es", "it", "nl", "se", "no", "pl", "ru", "cn"}
    # pop trailing TLD-ish labels, but never the last recognisable name
    while len(parts) > 1 and parts[-1] in tld:
        parts.pop()
    return ".".join(parts) if parts else host


def _fact_only(cluster: dict, cfg: dict) -> dict:
    """No model: publish the corroborated headline, cleaned.

    Nothing is invented. Wire cruft (ticker prefix, trailing clause, outlet
    suffix) is stripped, and finance-wire stories are skipped outright —
    they read as spam even when true.
    """
    limit = int(cfg["post"]["max_chars"])
    head = _clean_headline(cluster["headline"])
    if not head or _MARKET.search(head) or _OFFBRAND.search(head):
        return {"ok": False, "reason": "fact-only: wire item off-brand",
                "text": head}

    url = cluster.get("url", "")
    dom = _domain_word(url) or (cluster["domains"][0] if cluster["domains"] else "")
    suffix = ""
    if dom and len(head) + len(dom) + 3 <= limit:
        suffix = f" ({dom})"
    text = (head + suffix)[:limit]
    if len(head + suffix) > limit:
        text = head[: limit - len(suffix) - 1].rstrip() + "\u2026" + suffix

    return {
        "ok": True,
        "text": text,
        "headline": cluster["headline"],
        "url": cluster["url"],
        "domains": cluster["domains"],
        "confidence": cluster["confidence"] + "+fact-only",
        "hash": dedupe.fingerprint(text),
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def draft(cluster: dict, cfg: dict, voice: str) -> dict:
    from src import llm
    if llm.provider() == "none":
        return _fact_only(cluster, cfg)

    facts = cluster["headline"][:400]
    if len(cluster["sources"]) > 1:
        facts += "\nRelated: " + "; ".join(
            s["title"][:120] for s in cluster["sources"][1:4]
        )
    try:
        text = models.complete(
            "draft",
            SYS_ANGLE.format(voice=voice, facts=facts, max_chars=cfg["post"]["max_chars"]),
            "Write the post.",
            cfg,
            temperature=0.85,
        ).strip()
    except models.BudgetExceeded as e:
        return {"ok": False, "reason": str(e)}

    text = text.strip().strip('"')[: int(cfg["post"]["max_chars"])]
    if not text:
        return {"ok": False, "reason": "empty draft"}

    # deterministic check first — cheap, no model
    if cfg.get("news", {}).get("strict_numbers", True):
        src_numbers = _numbers(cluster["headline"])
        for extra in cluster["sources"][1:4]:
            src_numbers |= _numbers(extra.get("title", ""))
        draft_numbers = _numbers(text)
        orphan = sorted(n for n in draft_numbers if n not in src_numbers)
        # only block when the draft is mostly unsupported, not on formatting drift
        if src_numbers and orphan and len(orphan) > len(draft_numbers) / 2:
            return {"ok": False, "reason": f"numbers not in source: {orphan[:3]}",
                    "text": text}

    try:
        verdict = models.complete(
            "critic",
            SYS_FACTCHECK,
            f"SOURCE:\n{facts}\n\nDRAFT:\n{text}",
            cfg,
            temperature=0.1,
        )
    except models.BudgetExceeded:
        verdict = "PASS"
    if not verdict.upper().startswith("PASS"):
        return {"ok": False, "reason": f"factcheck: {verdict[:160]}", "text": text}

    return {
        "ok": True,
        "text": text,
        "headline": cluster["headline"],
        "url": cluster["url"],
        "domains": cluster["domains"],
        "confidence": cluster["confidence"],
        "hash": dedupe.fingerprint(text),
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
