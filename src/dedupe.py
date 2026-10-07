"""Repetition guard. Normalized sha256 over recent history."""

import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = _PUNCT.sub("", t)
    return _WS.sub(" ", t).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def shingles(text: str, k: int = 4) -> set[str]:
    words = normalize(text).split()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def too_similar(text: str, history: list[str], threshold: float = 0.42) -> bool:
    cand = shingles(text)
    if not cand:
        return False
    for old in history:
        prev = shingles(old)
        if not prev:
            continue
        overlap = len(cand & prev) / len(cand | prev)
        if overlap >= threshold:
            return True
    return False
