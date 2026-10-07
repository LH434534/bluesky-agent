"""Find any working AI route from inside a GitHub runner.

Runs each candidate and records exactly what came back. Output goes to
stdout and state/aiprobe.json so CI can commit the evidence.
"""

import json
import os
import pathlib
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("GITHUB_TOKEN", "")

GH_CHAT = "https://models.github.ai/inference/chat/completions"
GH_RESP = "https://models.github.ai/inference/responses"
GH_CATALOG = "https://models.github.ai/catalog/models"

# keyless / alternate hosts worth one shot from the runner
ALT = [
    ("hf", "https://api-inference.huggingface.co/models/google/flan-t5-base",
     {"inputs": "say ok"}),
    ("groq-public", "https://api.groq.com/openai/v1/models", None),
    ("openrouter-public", "https://openrouter.ai/api/v1/models", None),
    ("ollama-cloud", "https://api.ollama.com/api/tags", None),
    ("cloudflare", "https://api.cloudflare.com/client/v4/accounts", None),
]

# keyless / alternate hosts worth one shot from the runner
ALT = [
    ("hf", "https://api-inference.huggingface.co/models/google/flan-t5-base",
     {"inputs": "say ok"}),
    ("groq-public", "https://api.groq.com/openai/v1/models", None),
    ("openrouter-public", "https://openrouter.ai/api/v1/models", None),
    ("ollama-cloud", "https://api.ollama.com/api/tags", None),
    ("cloudflare", "https://api.cloudflare.com/client/v4/accounts", None),
]


def call(url, payload, headers, timeout=40):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "body": r.read()[:400].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code,
                "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {str(e)[:150]}"}


def base_headers(extra=None):
    h = {"Authorization": f"Bearer {TOKEN}",
         "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def probe() -> dict:
    out = {}

    out["chat_min"] = call(GH_CHAT,
                           {"model": "openai/gpt-4o-mini",
                            "messages": [{"role": "user", "content": "say ok"}],
                            "max_tokens": 8},
                           base_headers())
    time.sleep(4)
    out["chat_no_max"] = call(GH_CHAT,
                              {"model": "openai/gpt-4o-mini",
                               "messages": [{"role": "user", "content": "say ok"}]},
                              base_headers())
    time.sleep(4)
    out["chat_accept"] = call(GH_CHAT,
                              {"model": "openai/gpt-4o-mini",
                               "messages": [{"role": "user", "content": "say ok"}],
                               "max_tokens": 8, "temperature": 0.1},
                              base_headers({"Accept": "application/json",
                                            "User-Agent": "bluesky-agent"}))
    time.sleep(4)
    out["chat_stream"] = call(GH_CHAT,
                              {"model": "openai/gpt-4o-mini",
                               "messages": [{"role": "user", "content": "say ok"}],
                               "max_tokens": 8, "stream": True},
                              base_headers({"Accept": "text/event-stream"}))
    time.sleep(4)
    out["responses"] = call(GH_RESP,
                            {"model": "openai/gpt-4o-mini",
                             "input": "say ok", "max_output_tokens": 8},
                            base_headers())

    # catalog is a GET
    try:
        req = urllib.request.Request(GH_CATALOG, headers=base_headers())
        with urllib.request.urlopen(req, timeout=30) as r:
            out["catalog"] = {"status": r.status,
                              "body": r.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        out["catalog"] = {"status": 0, "error": f"{type(e).__name__}: {str(e)[:150]}"}

    # api.github.com surface
    for path in ("/models", "/copilot/models", "/meta"):
        try:
            req = urllib.request.Request("https://api.github.com" + path, headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "bluesky-agent"})
            with urllib.request.urlopen(req, timeout=25) as r:
                out[f"api{path}"] = {"status": r.status,
                                     "body": r.read()[:200].decode("utf-8", "replace")}
        except Exception as e:
            out[f"api{path}"] = {"status": 0,
                                 "error": f"{type(e).__name__}: {str(e)[:120]}"}

    # classic PAT may be entitled where the Actions token is not
    pat = os.environ.get("GH_MODELS_TOKEN", "")
    if pat:
        old = TOKEN
        globals()["TOKEN"] = pat
        out["pat_chat"] = call(GH_CHAT,
                               {"model": "openai/gpt-4o-mini",
                                "messages": [{"role": "user", "content": "say ok"}],
                                "max_tokens": 8}, base_headers())
        globals()["TOKEN"] = old

    for name, url, payload in ALT:
        try:
            if payload is None:
                req = urllib.request.Request(url, headers={"User-Agent": "probe"})
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "probe"})
            with urllib.request.urlopen(req, timeout=20) as r:
                out[name] = {"status": r.status,
                             "body": r.read()[:200].decode("utf-8", "replace")}
        except Exception as e:
            out[name] = {"status": 0, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    # classic PAT may be entitled where the Actions token is not
    pat = os.environ.get("GH_MODELS_TOKEN", "")
    if pat:
        old = TOKEN
        globals()["TOKEN"] = pat
        out["pat_chat"] = call(GH_CHAT,
                               {"model": "openai/gpt-4o-mini",
                                "messages": [{"role": "user", "content": "say ok"}],
                                "max_tokens": 8}, base_headers())
        globals()["TOKEN"] = old

    for name, url, payload in ALT:
        try:
            if payload is None:
                req = urllib.request.Request(url, headers={"User-Agent": "probe"})
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "probe"})
            with urllib.request.urlopen(req, timeout=20) as r:
                out[name] = {"status": r.status,
                             "body": r.read()[:200].decode("utf-8", "replace")}
        except Exception as e:
            out[name] = {"status": 0, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    return out


def main() -> int:
    res = probe()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    dest = ROOT / "state" / "aiprobe.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
