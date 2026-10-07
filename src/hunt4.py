"""Round 4. The Copilot backend answered 400 — it exists and our token
reached it. It rejected our integration header. So: try header shapes."""

import json
import os
import pathlib
import urllib.error
import urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN") or ""
ROOT = pathlib.Path(__file__).resolve().parent.parent
URL = "https://api.githubcopilot.com/chat/completions"


def probe(hdrs, payload):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": "hunt4"}
    h.update(hdrs)
    req = urllib.request.Request(URL, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return {"status": r.status, "body": r.read()[:300].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {str(e)[:110]}"}


BASE = {"Authorization": "Bearer " + TOKEN}
CHAT = {"model": "gpt-4o-mini", "max_tokens": 24,
        "messages": [{"role": "user", "content": "say: rat-alive"}],
        "stream": False}

COMBOS = [
    ("bare", {}),
    ("editor-only", {"Editor-Version": "vscode/1.95.0"}),
    ("vscode-chat", {"Editor-Version": "vscode/1.95.0",
                     "Copilot-Integration-Id": "vscode-chat"}),
    ("editor-plugin", {"Editor-Version": "vscode/1.95.0",
                       "Editor-Plugin-Version": "copilot/1.200.0"}),
    ("openai-intent", {"X-Initiator": "agent", "Editor-Version": "vscode/1.95.0"}),
    ("gh-actions", {"Editor-Version": "GitHubActions/1.0",
                    "Copilot-Integration-Id": "github-actions"}),
    ("neutral", {"Editor-Version": "Neovim/0.10.0",
                 "Copilot-Integration-Id": "copilot.lua"}),
    ("x-github", {"X-GitHub-Api-Version": "2022-11-28"}),
]

MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "claude-3.5-sonnet",
          "o3-mini", "gpt-3.5-turbo"]

out = {}
for name, extra in COMBOS:
    hdrs = dict(BASE); hdrs.update(extra)
    r = probe(hdrs, CHAT)
    out[name] = r
    b = str(r.get("body")).replace("\n", " ")[:150]
    print(f"  {str(r.get('status')):5} {name:16} {b}")

# if any combos returned 200, discover which models work on it
winner = [n for n, r in out.items() if r.get("status") == 200]
out["winners"] = winner
if winner:
    hdrs = dict(BASE); hdrs.update(dict(COMBOS)[winner[0]])
    out["models"] = {}
    for m in MODELS:
        r = probe(hdrs, dict(CHAT, model=m))
        out["models"][m] = r.get("status")
        print(f"    model {m:22} -> {r.get('status')}")

dest = ROOT / "state" / "hunt4.json"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("winners:", winner)
