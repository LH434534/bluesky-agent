"""Map what the runner can actually reach. Evidence, not guesswork."""

import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

HOSTS = [
    "https://api.github.com/zen",
    "https://models.github.ai/catalog/models",
    "https://models.github.ai/inference/chat/completions",
    "https://models.inference.ai.azure.com/chat/completions",
    "https://api.openai.com/v1/models",
    "https://generativelanguage.googleapis.com/v1beta/models",
    "https://api-inference.huggingface.co/models",
    "https://openrouter.ai/api/v1/models",
    "https://text.pollinations.ai/openai/models",
    "https://image.pollinations.ai/models",
    "https://bsky.social/xrpc/_health",
    "https://public.api.bsky.app/xrpc/_health",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=x&mode=ArtList&format=json&maxrecords=1",
    "https://en.wikipedia.org/wiki/Main_Page",
    "https://hn.algolia.com/api/v1/search?query=x&hitsPerPage=1",
]


def check(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "netprobe"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"status": r.status, "bytes": len(r.read()[:4000]),
                    "head": r.read()[:0].decode() if False else ""}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read()[:120].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def pollinations_post() -> str:
    import json as _j
    try:
        out = subprocess.run([
            "curl", "-sS", "-i", "-m", "30", "-X", "POST",
            "-H", "Content-Type: application/json",
            "-d", _j.dumps({"model": "openai", "temperature": 0.1,
                            "messages": [{"role": "user", "content": "say ok"}]}),
            "https://text.pollinations.ai/openai/chat/completions"],
            capture_output=True, text=True, timeout=40)
        return (out.stdout or "")[:1200] + ("\nSTDERR: " + out.stderr[:200] if out.stderr else "")
    except Exception as e:
        return f"curl failed: {e}"


def curl_raw(url: str) -> str:
    try:
        out = subprocess.run(["curl", "-sS", "-i", "-m", "20", "-X", "POST",
                              "-H", "Content-Type: application/json",
                              "-H", f"Authorization: Bearer {os.environ.get('GITHUB_TOKEN','')}",
                              "-d", '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}',
                              url],
                             capture_output=True, text=True, timeout=30)
        return (out.stdout or "")[:1200] + ("\nSTDERR: " + out.stderr[:300] if out.stderr else "")
    except Exception as e:
        return f"curl failed: {e}"


def run() -> dict:
    res = {"hosts": {}, "curl_models": ""}
    for h in HOSTS:
        res["hosts"][h] = check(h)
    res["curl_models"] = curl_raw("https://models.github.ai/inference/chat/completions")
    res["curl_pollinations"] = pollinations_post()
    dest = ROOT / "state" / "netprobe.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    r = run()
    for h, v in r["hosts"].items():
        print(f"  {str(v.get('status')):5} {h[:62]:64} {str(v.get('body',''))[:50]}")
    print("\n--- curl raw (models) ---")
    print(r["curl_models"][:800])
    print("\n--- curl raw (pollinations) ---")
    print(r["curl_pollinations"][:1200])
