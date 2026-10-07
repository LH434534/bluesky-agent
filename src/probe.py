"""Probe GitHub Models empirically.

The wire is not documented well and drifts, so this tries several endpoint,
payload and header shapes against one model and records exactly what comes
back — status, redirect target, first bytes — into state/models.json.
"""

import json
import pathlib
import urllib.error
import urllib.request

TOKEN = __import__("os").environ.get("GITHUB_TOKEN", "")
ROOT = pathlib.Path(__file__).resolve().parent.parent

BASE = "https://models.github.ai/inference/chat/completions"
MODEL = "openai/gpt-4o-mini"

VARIANTS = [
    ("messages+temp", {"model": MODEL, "temperature": 0.1,
                       "messages": [{"role": "user", "content": "say ok"}]}),
    ("messages+max_tokens", {"model": MODEL, "max_tokens": 8,
                             "messages": [{"role": "user", "content": "say ok"}]}),
    ("system+user", {"model": MODEL, "temperature": 0.1, "max_tokens": 8,
                     "messages": [{"role": "system", "content": "be terse"},
                                  {"role": "user", "content": "say ok"}]}),
    ("no-temp", {"model": MODEL, "messages": [{"role": "user", "content": "say ok"}]}),
]


def call(payload: dict, extra_headers: dict | None = None, allow_redirect: bool = True):
    data = json.dumps(payload).encode()
    h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
         "Accept": "application/json", "User-Agent": "bluesky-agent-probe"}
    h.update(extra_headers or {})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener()
    if not allow_redirect:
        opener = urllib.request.build_opener(NoRedirect)

    req = urllib.request.Request(BASE, data=data, headers=h)
    try:
        with opener.open(req, timeout=40) as r:
            return {"status": r.status, "url": r.geturl(),
                    "body": r.read()[:300].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        return {"status": e.code, "location": e.headers.get("Location"),
                "body": body}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {e}"}


def run() -> dict:
    out = {"base": BASE, "model": MODEL, "token_prefix": TOKEN[:4], "variants": {}}
    out["no_redirect"] = call(VARIANTS[0]["__x"] if False else VARIANTS[0][1],
                              allow_redirect=False)
    for name, payload in VARIANTS:
        out["variants"][name] = call(payload)

    # auth sanity: catalog listing
    try:
        req = urllib.request.Request("https://models.github.ai/catalog/models",
                                     headers={"Authorization": f"Bearer {TOKEN}",
                                              "User-Agent": "bluesky-agent-probe",
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
            names = [m.get("name") for m in body if isinstance(m, dict)]
            out["catalog"] = {"status": r.status, "count": len(names),
                              "sample": names[:25]}
    except Exception as e:
        out["catalog"] = {"error": f"{type(e).__name__}: {e}"}

    dest = ROOT / "state" / "models.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    r = run()
    print("no-redirect:", json.dumps(r["no_redirect"], ensure_ascii=False)[:400])
    for k, v in r["variants"].items():
        print(f"  {k:22} status={v.get('status')} body={str(v.get('body'))[:90]!r}")
    print("catalog:", json.dumps(r.get("catalog"), ensure_ascii=False)[:600])
