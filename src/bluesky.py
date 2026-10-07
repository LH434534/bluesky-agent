"""atproto client. Fresh session per call. Link facets included."""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 30
_URL = re.compile(r"https?://[^\s\]]+")
_SCHEME = re.compile(r"^(https?://)", re.IGNORECASE)


class BskyError(RuntimeError):
    pass


def _pds() -> str:
    return os.environ.get("PDS", "https://bsky.social").rstrip("/")


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise BskyError(f"{name} missing from environment")
    return v


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _request(url: str, payload: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise BskyError(f"bsky http {e.code}: {e.read()[:400].decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise BskyError(f"bsky network: {e.reason}") from e

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise BskyError(f"bsky bad json: {raw[:200]!r}")


_SESSION: dict | None = None


def create_session() -> dict:
    """Cached per process. Auth is expensive and rate limited; one session
    per run is enough because runners are short-lived."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    _SESSION = _request(
        f"{_pds()}/xrpc/com.atproto.server.createSession",
        {
            "identifier": _require("BSKY_HANDLE"),
            "password": _require("BSKY_APP_PASSWORD"),
        },
    )
    return _SESSION


def drop_session() -> None:
    global _SESSION
    _SESSION = None


def _facets(text: str) -> list:
    out = []
    encoded = text.encode("utf-8")
    for m in _URL.finditer(text):
        url = m.group(0)
        stripped = _SCHEME.sub("", url)
        byte_start = len(text[: m.start()].encode("utf-8"))
        byte_end = byte_start + len(stripped.encode("utf-8"))
        if byte_end > len(encoded):
            continue
        out.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )
    return out


def _facets_for(text: str, link: str | None) -> list:
    """Byte-offset facet so the URL renders as a real link, not raw text."""
    if not link:
        return []
    b = text.encode("utf-8")
    # link occupies the trailing "(domain)" token if present
    start = b.rfind(b"(")
    if start < 0:
        return []
    end = b.find(b")", start)
    if end < 0:
        return []
    return [{"index": {"byteStart": start + 1, "byteEnd": end},
             "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}]}]


def post(text: str, langs: list[str] | None = None, link: str | None = None) -> dict:
    sess = create_session()
    did = sess.get("did")
    jwt = sess.get("accessJwt")
    if not did or not jwt:
        raise BskyError(f"session incomplete: keys={list(sess)}")

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": _utc_now(),
        "langs": langs or ["pt-BR"],
    }
    facets = _facets_for(text, link)
    if facets:
        record["facets"] = facets
    facets = _facets(text)
    if facets:
        record["facets"] = facets

    return _request(
        f"{_pds()}/xrpc/com.atproto.repo.createRecord",
        {"repo": did, "collection": "app.bsky.feed.post", "record": record},
        token=jwt,
    )
