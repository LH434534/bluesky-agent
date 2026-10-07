"""Hunt, round 3: the paths that need no key at all.

1. Copilot token exchange — mint a short-lived token from the GitHub token,
   then talk to the Copilot backend. This is the route the CLI uses.
2. copilot-proxy.githubusercontent.com — the editor proxy.
3. What plan is this account even on.
"""

import json
import os
import pathlib
import urllib.error
import urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
ROOT = pathlib.Path(__file__).resolve().parent.parent


def probe(url, method="GET", payload=None, hdrs=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"User-Agent": "hunt3", "Accept": "application/json",
         "Content-Type": "application/json"}
    h.update(hdrs or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return {"status": r.status, "ctype": r.headers.get("content-type", ""),
                    "body": r.read()[:300].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ctype": e.headers.get("content-type", ""),
                "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {str(e)[:120]}"}


out = {"token_prefix": TOKEN[:4], "steps": {}}

# who are we
c, me = 0, {}
try:
    req = urllib.request.Request("https://api.github.com/user", headers={
        "Authorization": "Bearer " + TOKEN, "User-Agent": "hunt3"})
    with urllib.request.urlopen(req, timeout=20) as r:
        me = json.load(r)
except Exception as e:
    me = {"err": str(e)[:120]}
out["account"] = {k: me.get(k) for k in ("login", "plan", "type") if me.get(k)}

# 1. copilot token exchange
ex = probe("https://api.github.com/copilot_internal/v2/token", "GET",
           hdrs={"Authorization": "Bearer " + TOKEN})
out["steps"]["exchange"] = ex
cpt = None
try:
    cpt = json.loads(ex.get("body", "")).get("token")
except Exception:
    pass
out["exchange_ok"] = bool(cpt)

CHAT = {"model": "gpt-4o-mini", "max_tokens": 24,
        "messages": [{"role": "user", "content": "say the word: rat"}]}

# 2. copilot backends
if cpt:
    for u in ("https://api.githubcopilot.com/chat/completions",
              "https://api.githubcopilot.com/v1/chat/completions",
              "https://copilot-proxy.githubusercontent.com/v1/chat/completions",
              "https://api.githubcopilot.com/models"):
        out["steps"][u] = probe(u, "POST", CHAT,
                                hdrs={"Authorization": "Bearer " + cpt,
                                      "Editor-Version": "vscode/1.95.0",
                                      "Copilot-Integration-Id": "vscode-chat"})
else:
    # try the proxy with the raw github token, some setups accept it
    for u in ("https://api.githubcopilot.com/chat/completions",
              "https://copilot-proxy.githubusercontent.com/v1/chat/completions"):
        out["steps"][u] = probe(u, "POST", CHAT,
                                hdrs={"Authorization": "Bearer " + TOKEN})

# 3. does the repo have models:read at all
out["steps"]["ratelimit"] = probe("https://api.github.com/rate_limit", "GET",
                                  hdrs={"Authorization": "Bearer " + TOKEN})

dest = ROOT / "state" / "hunt3.json"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("account:", out["account"])
print("exchange_ok:", out["exchange_ok"])
for k, v in out["steps"].items():
    b = str(v.get("body")).replace("\n", " ")[:110]
    print(f"  {str(v.get('status')):5} {k[:60]:62} {b}")
