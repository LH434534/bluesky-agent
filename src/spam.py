"""Anti-spam content filter.

Two layers: cheap deterministic rules (no model call, no cost, always run),
then a model pass for the ambiguous middle.
"""

import re
import unicodedata
from datetime import datetime, timezone

_HASHTAGS = re.compile(r"#\w+")
_URLS = re.compile(r"https?://\S+")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
_CAPS = re.compile(r"[A-ZÀ-Ý]{4,}")
_REPEAT = re.compile(r"(.{6,}?)\1{2,}", re.DOTALL)


def _age_hours(created_at: str) -> float:
    if not created_at:
        return 999.0
    try:
        then = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 999.0
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


def scan(text: str, cfg: dict, author_did: str = "", seen: set | None = None) -> tuple[bool, str]:
    """Return (is_clean, reason). Cheap checks only."""
    if not text or not text.strip():
        return False, "empty"

    low = text.lower()

    for term in cfg["safety"]["blocklist_terms"]:
        if term.lower() in low:
            return False, f"blocklist: {term}"

    tags = _HASHTAGS.findall(text)
    if len(tags) > 3:
        return False, f"hashtag spam ({len(tags)})"

    emoji = _EMOJI.findall(text)
    if len(emoji) > 5:
        return False, f"emoji wall ({len(emoji)})"

    urls = _URLS.findall(text)
    if len(urls) > 2:
        return False, f"link spam ({len(urls)})"

    words = low.split()
    if words:
        uniq = len(set(words)) / len(words)
        if len(words) > 12 and uniq < 0.4:
            return False, "repetitive text"

    if len(_CAPS.findall(text)) > 3:
        return False, "shouting"

    if _REPEAT.search(text):
        return False, "repeated block"

    if seen and author_did in seen:
        return False, "author already handled"

    return True, "clean"


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def vocabulary(cfg_or_topics, extra: list[str] | None = None) -> set:
    """Interest vocabulary: topics + discovery terms + tech lexicon."""
    if isinstance(cfg_or_topics, dict):
        words = list(cfg_or_topics.get("topics", []))
        words += list(cfg_or_topics.get("discovery", {}).get("search_terms", []))
    else:
        words = list(cfg_or_topics)
    words += list(extra or [])
    return {w for w in (_fold(x) for x in re.findall(r"\w{4,}", " ".join(words).lower())) if w}


LEXICON = """
sqlite postgres redis nginx caddy docker kubernetes linux server servidor
script cron automacao automacao python shell bash terminal infra infraestrutura
selfhosted homelab host selfhost deploy backup cache monitor logs dados data
smallweb website static sites blog feed rss archive arquivo arquivos
network rede protocol packet proxy firewall vpn dns ssh hosting
build builds building codigo code sistema system pipeline automation
ferramenta tools simples simple rapido fast leve lightweight memory disk
ai model models llm agent agents machine learning neural inference token
open source opensource github git commit release version update
climate energy solar wind nuclear battery grid emission carbon
economy market inflation rate bank central currency trade tariff
space satellite launch rocket nasa esa orbit mission moon mars
security vulnerability patch breach malware ransom encryption
health vaccine hospital disease outbreak research study science
election government policy law court regulation vote president
chip semiconductor processor cpu gpu hardware device phone
startup funding investor company business job layoff remote
browser web app application software service api cloud
water food transport train plane car traffic city urban
brazil brasil governo economia empresa tecnologia mercado
""".split()


def relevance(our_topics: list[str], text: str) -> float:
    """Cheap lexical overlap. Accent-folded, vocabulary-expanded."""
    if not text:
        return 0.0
    topic_tokens = vocabulary(our_topics, LEXICON)
    tokens = {_fold(t) for t in re.findall(r"\w{4,}", text)}
    if not tokens or not topic_tokens:
        return 0.0
    hits = tokens & topic_tokens
    if not hits:
        return 0.0
    denom = min(len(tokens), len(topic_tokens))
    return min(1.0, len(hits) / denom)
