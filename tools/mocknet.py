"""Fake network at the socket boundary, so the REAL code paths execute.

Nothing in src/ is patched. urllib.request.urlopen is replaced, so news.py
parses real-shaped payloads, social.py builds real atproto records, policy.py
gates for real, ground.py clusters for real. Only the wire is fake.
"""

import io
import json
import random
import urllib.request

CALLS: list[str] = []
POSTED: list[dict] = []


class Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


TIMELINE = [
    {"uri": "at://p/1", "cid": "c1",
     "record": {"text": "Migrei meu homelab pra caddy + sqlite. 90 linhas de config viraram 9."},
     "author": {"did": "did:a", "handle": "dev1", "displayName": "Ana",
                "description": "infra, self-hosted, linux", "followersCount": 4200},
     "likeCount": 40, "replyCount": 6, "repostCount": 3},
    {"uri": "at://p/2", "cid": "c2",
     "record": {"text": "Crypto airdrop free mint!!! DM me #nft #drop #pump #moon 🚀🚀🚀🚀🚀🚀"},
     "author": {"did": "did:spam", "handle": "spammer", "displayName": "x",
                "description": "", "followersCount": 12},
     "likeCount": 0, "replyCount": 0, "repostCount": 0},
    {"uri": "at://p/3", "cid": "c3",
     "record": {"text": "Small web voltou. Site de 40kb carregando em 200ms, sem framework nenhum."},
     "author": {"did": "did:c", "handle": "webdev", "displayName": "Rafa",
                "description": "small web, static sites, rss", "followersCount": 8800},
     "likeCount": 120, "replyCount": 14, "repostCount": 22},
    {"uri": "at://p/4", "cid": "c4",
     "record": {"text": "Python script meu roda em cron há 3 anos sem eu encostar. Simples vence."},
     "author": {"did": "did:d", "handle": "pydev", "displayName": "Lucas",
                "description": "python, automation, devops", "followersCount": 15000},
     "likeCount": 300, "replyCount": 40, "repostCount": 60},
    {"uri": "at://p/5", "cid": "c5",
     "record": {"text": "Automação residencial com sqlite: 40k writes/dia, zero manutenção."},
     "author": {"did": "did:f", "handle": "homelab", "displayName": "Bia",
                "description": "homelab, infrastructure, open source", "followersCount": 6300},
     "likeCount": 90, "replyCount": 11, "repostCount": 8},
    {"uri": "at://p/6", "cid": "c6",
     "record": {"text": "Meu script python de automacao em cron: crypto airdrop free mint dm me #nft #drop #pump #moon"},
     "author": {"did": "did:x", "handle": "wolf", "displayName": "w",
                "description": "python automation", "followersCount": 900},
     "likeCount": 2, "replyCount": 0, "repostCount": 0},
]

GDELT_ARTICLES = [
    {"url": "https://esa.int/a", "title": "ESA launches new weather satellite from Kourou",
     "domain": "esa.int", "sourcecountry": "France", "language": "English", "seendate": "20261007T090000Z"},
    {"url": "https://reuters.com/a", "title": "New weather satellite launches from French Guiana",
     "domain": "reuters.com", "sourcecountry": "France", "language": "English", "seendate": "20261007T091500Z"},
    {"url": "https://bbc.co.uk/a", "title": "Europe launches weather satellite to improve storm forecasts",
     "domain": "bbc.co.uk", "sourcecountry": "United Kingdom", "language": "English", "seendate": "20261007T093000Z"},
    {"url": "https://github.blog/a", "title": "Open source project reaches 100k stars on GitHub",
     "domain": "github.blog", "sourcecountry": "United States", "language": "English", "seendate": "20261007T080000Z"},
    {"url": "https://hn.algolia.com/a", "title": "Open source project hits 100000 GitHub stars",
     "domain": "hn.algolia.com", "sourcecountry": "United States", "language": "English", "seendate": "20261007T081000Z"},
    {"url": "https://ft.com/a", "title": "Central banks hold rates steady as inflation cools",
     "domain": "ft.com", "sourcecountry": "United Kingdom", "language": "English", "seendate": "20261007T070000Z"},
    {"url": "https://imf.org/a", "title": "Central banks keep rates unchanged amid cooling inflation",
     "domain": "imf.org", "sourcecountry": "United States", "language": "English", "seendate": "20261007T071500Z"},
    {"url": "https://clickfarm.biz/a", "title": "SHOCKING one weird trick doctors hate click now",
     "domain": "clickfarm.biz", "sourcecountry": "Unknown", "language": "English", "seendate": "20261007T060000Z"},
]

HN_HITS = [{"title": "Self-hosting everything for 3 years: what broke", "url": "https://blog.example/x",
            "points": 340, "num_comments": 120, "objectID": "1", "created_at": "2026-10-07T08:00:00Z"}]

WIKI_HTML = (
    "<html><body><ul><li>"
    "&bull; A new weather satellite is launched from the Kourou space centre "
    "in French Guiana by the European Space Agency "
    "&bull; Central banks in several countries announce that they will hold "
    "interest rates steady "
    "&bull; An open source project passes one hundred thousand stars on a code "
    "hosting platform"
    "</li></ul></body></html>"
).encode("utf-8")

REPLIES = [
    "Sqlite aguenta mais do que parece. Uso em tres servicos e nunca precisei tunar.",
    "Nove linhas de config que ninguem precisa debugar as 3 da manha. Isso e vitoria.",
    "O que sobrevive e o simples. Todo dashboard que eu instalei morreu em dois anos.",
]
DRAFTS = [
    "Tres agencias independentes confirmaram o lancamento do satelite. Previsao de tempestade melhora quando os dados chegam.",
    "Projeto open source passou de 100k estrelas. Crescimento sem marketing e a prova que utilidade distribui.",
    "Bancos centrais mantiveram juros. Inflacao esfriando muda calculo de quem segura caixa.",
]


def _chat(payload: dict) -> dict:
    msgs = payload.get("messages", [])
    sys = " ".join(m.get("content", "") for m in msgs if m["role"] == "system")
    user = " ".join(m.get("content", "") for m in msgs if m["role"] == "user")
    low = (sys + user).lower()

    if "ruthless editor" in low or "strict fact checker" in low:
        out = "PASS concrete, dry, every claim traces to source"
    elif "choosing what a thoughtful account" in low:
        out = "0 strongest concrete angle"
    elif "worth following" in low or "good place for us to be visible" in low:
        out = "YES"
    elif "score how well" in low:
        out = "0.8 infra"
    elif "replying as a real person" in low or "your reply" in low:
        out = random.choice(REPLIES)
    elif "write one post" in low or "write the post" in low or "one post of at most" in low:
        out = random.choice(DRAFTS)
    else:
        out = random.choice(DRAFTS)

    return {"choices": [{"message": {"content": out, "role": "assistant"}}]}


def _route(url: str, data: bytes | None) -> Resp:
    CALLS.append(url.split("?")[0][:70])

    if "models.github.ai" in url:
        return Resp(json.dumps(_chat(json.loads(data or b"{}"))).encode())

    if "gdeltproject.org" in url:
        return Resp(json.dumps({"articles": GDELT_ARTICLES}).encode())

    if "wikipedia.org" in url:
        return Resp(WIKI_HTML)

    if "hn.algolia.com" in url:
        return Resp(json.dumps({"hits": HN_HITS}).encode())

    if "bsky.social" in url or "xrpc" in url:
        if "resolveHandle" in url:
            return Resp(json.dumps({"did": "did:plc:me"}).encode())
        if "createSession" in url:
            return Resp(json.dumps({"did": "did:plc:me", "accessJwt": "jwt.fake"}).encode())
        if "getTimeline" in url:
            return Resp(json.dumps({"feed": [{"post": p} for p in TIMELINE]}).encode())
        if "searchPosts" in url:
            return Resp(json.dumps({"posts": TIMELINE[:4]}).encode())
        if "getAuthorFeed" in url:
            return Resp(json.dumps({"feed": [
                {"post": {**p, "likeCount": 30, "replyCount": 5, "repostCount": 2}} for p in TIMELINE[:3]
            ]}).encode())
        if "createRecord" in url:
            body = json.loads(data)
            rec = body.get("record", {})
            POSTED.append(rec)
            return Resp(json.dumps({"uri": f"at://did:plc:me/app.bsky.feed.post/{len(POSTED)}",
                                    "cid": f"cid{len(POSTED)}"}).encode())
        return Resp(b"{}")

    return Resp(b"{}")


def install() -> None:
    def fake_urlopen(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        return _route(url, req.data if hasattr(req, "data") else None)

    urllib.request.urlopen = fake_urlopen
