"""Real news ingestion. No invention, no guessing.

Sources, all keyless:
  - GDELT Doc API      global, 100+ languages, re-indexed every 15 min
  - Wikipedia Current Events  human-curated daily digest
  - Hacker News (Algolia)     tech/startup signal, fast
  - Google News RSS           opt-in, OFF by default (feed terms restrict it
                              to personal non-commercial use)

Every item carries a source domain and a url. Anything without both is dropped.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

UA = "bluesky-agent/1.0 (autonomous personal bot)"
TIMEOUT = 25


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError):
        return b""


def _domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------- RSS ----------

def rss(url: str) -> list:
    """Plain RSS/Atom reader. Deterministic, unlike scraping a rendered page."""
    raw = _fetch(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    nodes = list(root.iter("item")) + list(root.findall(".//atom:entry", ns))
    for node in nodes:
        def txt(*names):
            for n in names:
                el = node.find(n)
                if el is not None and (el.text or "").strip():
                    return (el.text or "").strip()
                # atom link carries the url in an attribute
                if n == "link":
                    el = node.find("atom:link", ns)
                    if el is not None and el.get("href"):
                        return el.get("href")
            return ""
        title = txt("title", "atom:title")
        link = txt("link", "atom:link")
        if not title or not link:
            continue
        out.append({
            "title": title,
            "url": link,
            "domain": _domain(link),
            "country": "",
            "language": "",
            "seen": txt("pubDate", "published", "updated", "atom:updated"),
            "source": "rss",
            "fetchedAt": _now_iso(),
        })
    return out


# ---------- GDELT ----------

_LAST_GDELT = [0.0]
GDELT_GAP = 5.5
_429_BACKOFF = [0.0]


def _throttle() -> None:
    """GDELT allows ~1 request/5s per IP. Hammering it only yields 429s."""
    now = time.time()
    wait = GDELT_GAP - (now - _LAST_GDELT[0])
    if _429_BACKOFF[0] > now:
        wait = max(wait, _429_BACKOFF[0] - now)
    if wait > 0:
        time.sleep(wait)
    _LAST_GDELT[0] = time.time()


def gdelt(query: str, maxrecords: int = 60, timespan: str = "24h",
          lang: str = "", country: str = "") -> list:
    q = query
    if lang:
        q += f" sourcelang:{lang}"
    if country:
        q += f" sourcecountry:{country}"
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        + urllib.parse.urlencode(
            {"query": q, "mode": "ArtList", "maxrecords": maxrecords,
             "format": "json", "timespan": timespan, "sort": "datedesc"}
        )
    )
    _throttle()
    raw = _fetch(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # GDELT answers throttling with plain text, not JSON
        _429_BACKOFF[0] = time.time() + 20
        return []
    out = []
    for a in data.get("articles", []) or []:
        u = a.get("url", "")
        t = (a.get("title") or "").strip()
        if not u or not t:
            continue
        out.append({
            "title": t,
            "url": u,
            "domain": a.get("domain") or _domain(u),
            "country": a.get("sourcecountry", ""),
            "language": a.get("language", ""),
            "seen": a.get("seendate", ""),
            "source": "gdelt",
            "fetchedAt": _now_iso(),
        })
    return out


# ---------- Wikipedia current events ----------

def wikipedia(lang: str = "en") -> list:
    url = f"https://{lang}.wikipedia.org/wiki/Portal:Current_events"
    raw = _fetch(url)
    if not raw:
        return []
    text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "replace"))
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s+", " ", text)
    out = []
    seen = set()
    # bullet-style items in the daily digest
    for chunk in re.findall(r"•\s*([^•]{40,400}?)(?=\s•|\s*\[\s*edit|$)", text):
        clean = chunk.strip()
        if len(clean) < 40 or clean in seen:
            continue
        if not re.search(r"[A-Za-zÀ-ÿ]", clean):
            continue
        seen.add(clean)
        out.append({
            "title": clean[:300],
            "url": url,
            "domain": f"{lang}.wikipedia.org",
            "country": "",
            "language": lang,
            "seen": "",
            "source": "wikipedia",
            "fetchedAt": _now_iso(),
        })
        if len(out) >= 40:
            break
    return out


# ---------- Hacker News ----------

def hackernews(hits: int = 40, min_points: int = 30) -> list:
    url = (
        "https://hn.algolia.com/api/v1/search?tags=story"
        f"&numericFilters=points%3E{min_points}&hitsPerPage={hits}"
    )
    raw = _fetch(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for h in data.get("hits", []) or []:
        title = (h.get("title") or "").strip()
        if not title or int(h.get("points") or 0) < min_points:
            continue
        u = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append({
            "title": title,
            "url": u,
            "domain": _domain(u),
            "country": "",
            "language": "en",
            "seen": h.get("created_at", ""),
            "source": "hackernews",
            "points": int(h.get("points") or 0),
            "comments": int(h.get("num_comments") or 0),
            "fetchedAt": _now_iso(),
        })
    return out


# ---------- Google News RSS (opt-in) ----------

def google_news(query: str, hl: str = "pt-BR", gl: str = "BR", ceid: str = "BR:pt-419") -> list:
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + f"&hl={hl}&gl={gl}&ceid={urllib.parse.quote(ceid)}"
    )
    raw = _fetch(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        src_el = item.find("source")
        domain = (src_el.text or "").strip() if src_el is not None else _domain(link)
        out.append({
            "title": title,
            "url": link,
            "domain": domain or "news.google.com",
            "country": gl,
            "language": hl[:2],
            "seen": item.findtext("pubDate") or "",
            "source": "google-news",
            "fetchedAt": _now_iso(),
        })
    return out


KEEP_LANGS = {"english", "portuguese", "spanish", ""}


def language_ok(item: dict) -> bool:
    """Keep only languages the account actually writes in.

    GDELT covers 100+ languages; posting German wire copy to a pt-BR
    account is worse than posting nothing.
    """
    lang = (item.get("language") or "").strip().lower()
    if not lang:
        return True
    return lang in KEEP_LANGS


def gather(cfg: dict) -> tuple[list, dict]:
    """Pull from every enabled source. Never raises.

    Returns (items, stats) where stats records what each source gave, so a
    silent "no news" is diagnosable instead of mysterious.
    """
    n = cfg.get("news", {})
    items: list = []
    stats: dict = {}
    seen_urls: set[str] = set()

    def add(batch: list) -> None:
        for it in batch:
            u = it.get("url", "")
            if u in seen_urls:
                continue
            if not language_ok(it):
                continue
            seen_urls.add(u)
            items.append(it)

    if n.get("gdelt", {}).get("enabled", True):
        got = 0
        for q in n["gdelt"].get("queries", []):
            try:
                batch = gdelt(
                    q,
                    maxrecords=int(n["gdelt"].get("maxrecords", 60)),
                    timespan=n["gdelt"].get("timespan", "24h"),
                    lang=n["gdelt"].get("language", ""),
                    country=n["gdelt"].get("country", ""),
                )
            except Exception as e:
                stats.setdefault("errors", []).append(f"gdelt:{type(e).__name__}")
                continue
            got += len(batch)
            add(batch)
        stats["gdelt_raw"] = got

    if n.get("wikipedia", {}).get("enabled", True):
        got = 0
        for lang in n["wikipedia"].get("langs", ["en"]):
            try:
                batch = wikipedia(lang)
            except Exception as e:
                stats.setdefault("errors", []).append(f"wiki:{type(e).__name__}")
                continue
            got += len(batch)
            add(batch)
        stats["wikipedia_raw"] = got

    got = 0
    for url in n.get("rss", {}).get("feeds", []):
        try:
            batch = rss(url)
        except Exception as e:
            stats.setdefault("errors", []).append(f"rss:{type(e).__name__}")
            continue
        got += len(batch)
        add(batch)
    stats["rss_raw"] = got

    if n.get("hackernews", {}).get("enabled", True):
        try:
            batch = hackernews(
                hits=int(n["hackernews"].get("hits", 40)),
                min_points=int(n["hackernews"].get("min_points", 30)),
            )
            stats["hn_raw"] = len(batch)
            add(batch)
        except Exception as e:
            stats.setdefault("errors", []).append(f"hn:{type(e).__name__}")

    if n.get("google_news", {}).get("enabled", False):
        got = 0
        for q in n["google_news"].get("queries", []):
            try:
                batch = google_news(q, **n["google_news"].get("locale", {}))
            except Exception:
                continue
            got += len(batch)
            add(batch)
        stats["gnews_raw"] = got

    stats["kept"] = len(items)
    return items, stats
