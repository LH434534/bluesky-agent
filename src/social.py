"""Full atproto actor surface: read, post, reply, like, repost, follow.

Fresh session per call block. Every write goes through policy.allow first.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from src import bluesky
from src.bluesky import BskyError, _pds, _request, _utc_now

TIMEOUT = 30


def _auth() -> tuple[str, str]:
    sess = bluesky.create_session()
    did, jwt = sess.get("did"), sess.get("accessJwt")
    if not did or not jwt:
        raise BskyError(f"session incomplete: keys={list(sess)}")
    return did, jwt


def _get(path: str, params: dict, jwt: str) -> dict:
    from urllib.parse import urlencode

    url = f"{_pds()}/xrpc/{path}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {jwt}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise BskyError(f"get {path} http {e.code}") from e


# ---------- read ----------

def timeline(jwt: str, limit: int = 30) -> list:
    return _get("app.bsky.feed.getTimeline", {"limit": limit}, jwt).get("feed", [])


def author_feed(jwt: str, actor: str, limit: int = 20) -> list:
    return _get("app.bsky.feed.getAuthorFeed", {"actor": actor, "limit": limit}, jwt).get("feed", [])


def search_posts(jwt: str, term: str, limit: int = 25) -> list:
    return _get(
        "app.bsky.feed.searchPosts", {"q": term, "limit": limit, "sort": "latest"}, jwt
    ).get("posts", [])


def feed_generator(jwt: str, uri: str, limit: int = 30) -> list:
    return _get("app.bsky.feed.getFeed", {"feed": uri, "limit": limit}, jwt).get("feed", [])


def profile(jwt: str, actor: str) -> dict:
    return _get("app.bsky.actor.getProfile", {"actor": actor}, jwt)


# ---------- write ----------

def _uri_cid(item: dict) -> tuple[str, str]:
    post = item.get("post", item)
    return post.get("uri", ""), post.get("cid", "")


def reply(text: str, root_uri: str, root_cid: str, parent_uri: str, parent_cid: str, langs: list) -> dict:
    did, jwt = _auth()
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": _utc_now(),
        "langs": langs,
        "reply": {
            "root": {"uri": root_uri, "cid": root_cid},
            "parent": {"uri": parent_uri, "cid": parent_cid},
        },
    }
    return _request(
        f"{_pds()}/xrpc/com.atproto.repo.createRecord",
        {"repo": did, "collection": "app.bsky.feed.post", "record": record},
        token=jwt,
    )


def like(uri: str, cid: str) -> dict:
    did, jwt = _auth()
    return _request(
        f"{_pds()}/xrpc/com.atproto.repo.createRecord",
        {
            "repo": did,
            "collection": "app.bsky.feed.like",
            "record": {
                "$type": "app.bsky.feed.like",
                "subject": {"uri": uri, "cid": cid},
                "createdAt": _utc_now(),
            },
        },
        token=jwt,
    )


def repost(uri: str, cid: str) -> dict:
    did, jwt = _auth()
    return _request(
        f"{_pds()}/xrpc/com.atproto.repo.createRecord",
        {
            "repo": did,
            "collection": "app.bsky.feed.repost",
            "record": {
                "$type": "app.bsky.feed.repost",
                "subject": {"uri": uri, "cid": cid},
                "createdAt": _utc_now(),
            },
        },
        token=jwt,
    )


def follow(did_target: str) -> dict:
    did, jwt = _auth()
    return _request(
        f"{_pds()}/xrpc/com.atproto.repo.createRecord",
        {
            "repo": did,
            "collection": "app.bsky.graph.follow",
            "record": {
                "$type": "app.bsky.graph.follow",
                "subject": did_target,
                "createdAt": _utc_now(),
            },
        },
        token=jwt,
    )


def my_did() -> str:
    did, _ = _auth()
    return did
