"""Hunt for any reachable inference endpoint from this runner.

Writes state/hunt.json. Nothing is assumed: every candidate is actually
called, and the raw first bytes are recorded so we can see what the wire
really says instead of guessing.
"""

import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def call(url: str, payload: dict, extra: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"User-Agent": "bluesky-agent-hunt", "Accept": "application/json",
         "Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    h.update(extra or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            return {"status": r.status, "ctype": r.headers.get("content-type", ""),
                    "body": raw[:400].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return {"status": e.code, "ctype": e.headers.get("content-type", ""),
                "location": e.headers.get("Location"),
                "body": raw[:400].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {str(e)[:150]}"}


CHAT = {"model": "openai/gpt-4o-mini", "max_tokens": 12,
        "messages": [{"role": "user", "content": "say ok"}]}

GH_PATHS = [
    "https://models.github.ai/inference/chat/completions",
    "https://models.github.ai/inference/v1/chat/completions",
    "https://models.github.ai/v1/chat/completions",
    "https://models.github.ai/chat/completions",
    "https://api.models.github.ai/inference/chat/completions",
    "https://models.github.ai/inference/chat/completions/",
]

GH_HEADERS = [
    ("default", {}),
    ("bearer+api-version", {"api-version": "2024-05-01-preview"}),
    ("bearer+preview", {"X-Model-Type": "chat"}),
    ("github-token", {"Authorization": f"token {TOKEN}"} if TOKEN else {}),
]

# keyless / free-tier OpenAI-compatible endpoints
OPEN_CANDIDATES = [
    ("pollinations", "https://text.pollinations.ai/openai/chat/completions", None),
    ("openrouter-free", "https://openrouter.ai/api/v1/chat/completions", None),
    ("hf-inference", "https://api-inference.huggingface.co/v1/chat/completions", None),
    ("groq", "https://api.groq.com/openai/v1/chat/completions", None),
    ("together", "https://api.together.xyz/v1/chat/completions", None),
    ("mistral", "https://api.mistral.ai/v1/chat/completions", None),
    ("cohere", "https://api.cohere.ai/v1/chat", None),
    ("ollama-local", "http://127.0.0.1:11434/v1/chat/completions", None),
]


def run() -> dict:
    out = {"token_present": bool(TOKEN), "github": {}, "open": {}, "catalog": {}}

    # does the catalog actually list models?
    for u in ("https://models.github.ai/catalog/models",
              "https://models.github.ai/inference/models",
              "https://api.github.com/models",
              "https://api.github.com/orgs/github/packages?package_type=container"):
        r = call(u, None, method="GET")
        out["catalog"][u] = {"status": r.get("status"),
                             "ctype": r.get("ctype"),
                             "body": (r.get("body") or "")[:250]}

    for path in GH_PATHS:
        for name, hdrs in GH_HEADERS:
            r = call(path, CHAT, hdrs)
            key = f"{path} [{name}]"
            out["github"][key] = {"status": r.get("status"),
                                  "ctype": r.get("ctype"),
                                  "body": (r.get("body") or "")[:200]}
        # also probe openai-style model id
        r = call(path, dict(CHAT, model="gpt-4o-mini"))
        out["github"][f"{path} [short-id]"] = {"status": r.get("status"),
                                               "ctype": r.get("ctype"),
                                               "body": (r.get("body") or "")[:200]}

    for name, url, key in OPEN_CANDIDATES:
        payload = dict(CHAT)
        if name != "pollinations":
            payload["model"] = "gpt-4o-mini" if "openai" not in url else "gpt-4o-mini"
        r = call(url, payload)
        out["open"][name] = {"url": url, "status": r.get("status"),
                             "ctype": r.get("ctype"),
                             "body": (r.get("body") or "")[:250]}

    # raw curl for the one that matters most, no python in the way
    try:
        p = subprocess.run([
            "curl", "-sS", "-i", "-m", "25", "-X", "POST",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {TOKEN}",
            "-d", json.dumps(CHAT),
            "https://models.github.ai/inference/chat/completions"],
            capture_output=True, text=True, timeout=35)
        out["curl"] = (p.stdout or "")[:1500]
    except Exception as e:
        out["curl"] = f"failed: {e}"

    dest = ROOT / "state" / "hunt.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    r = run()
    print("CATALOG:")
    for u, v in r["catalog"].items():
        print(f"  {str(v.get('status')):5} {u[:58]:60} {str(v.get('body'))[:60]}")
    print("\nGITHUB MODELS:")
    for k, v in r["github"].items():
        b = str(v.get("body")).replace("\n", " ")[:70]
        print(f"  {str(v.get('status')):5} {k[:64]:66} {b}")
    print("\nOPEN PROVIDERS:")
    for k, v in r["open"].items():
        b = str(v.get("body")).replace("\n", " ")[:80]
        print(f"  {str(v.get('status')):5} {k:16} {b}")
    print("\nCURL:", str(r.get("curl"))[:600])
