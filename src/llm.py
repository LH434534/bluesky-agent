"""Provider-agnostic model layer.

In order:
  1. LLM_BASE_URL + LLM_API_KEY  — any OpenAI-compatible endpoint
     (OpenRouter, Groq, Together, OpenAI, a local server). Best option.
  2. GitHub Models               — free, but not always serving.
  3. none                        — no generation. The bot degrades to
                                   fact-only posts and rule-based engagement
                                   instead of crashing or inventing text.

Availability is probed once per run and cached.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

GH_ENDPOINT = "https://models.github.ai/inference/chat/completions"
POLLINATIONS = "https://text.pollinations.ai/openai"
TIMEOUT = 45

_STATE: dict = {"provider": None, "checked": False}


class NoProvider(RuntimeError):
    """No model available. Callers must degrade, not crash."""


class ProviderError(RuntimeError):
    """Provider answered, but not usefully."""


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def _post(url: str, payload: dict, token: str) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "bluesky-agent",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise ProviderError(f"http {e.code}: {e.read()[:200].decode('utf-8','replace')}")
    except urllib.error.URLError as e:
        raise ProviderError(f"network: {e.reason}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ProviderError(f"non-json: {raw[:120]!r}")


def _extract(body: dict) -> str:
    try:
        return (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"bad payload: {json.dumps(body)[:200]}")


def _gh_token() -> str:
    return _token()


def token_diagnosis() -> str:
    """Say plainly why a token cannot reach GitHub Models.

    Classic PATs (ghp_) are rejected by Copilot and by models.github.ai,
    which answers them with a bare 200 "OK" that parses as nothing.
    """
    t = _token()
    if not t:
        return "sem GITHUB_TOKEN"
    if t.startswith("ghp_"):
        return ("classic PAT (ghp_) — Copilot e GitHub Models exigem "
                "fine-grained PAT com permissao models:read")
    if t.startswith("github_pat_"):
        return "fine-grained PAT — ok"
    return "token de instalacao — ok se o workflow tem models: read"


POLLI_MODELS = ["openai", "openai-large", "qwen-coder", "mistral", "llama"]


def _pollinations(model: str, system: str, user: str, temperature: float) -> str:
    """Keyless OpenAI-compatible endpoint. Last resort, but it writes.

    Its model names are its own; a GitHub-style id like 'openai/gpt-4o-mini'
    404s. Try the short names until one answers.
    """
    want = (model or "").lower()
    order = [m for m in POLLI_MODELS if want.startswith(m)] or POLLI_MODELS
    order = order + [m for m in POLLI_MODELS if m not in order]
    last = ""
    for name in order:
        try:
            body = _post(f"{POLLINATIONS}/chat/completions",
                         {"model": name, "temperature": temperature,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]}, "")
            out = _extract(body)
            if out:
                _STATE["pollinations_model"] = name
                return out
        except ProviderError as e:
            last = str(e)
    raise ProviderError(f"pollinations: {last}"[:200])


def probe() -> str:
    """Provider to use: 'custom', 'github', 'pollinations', or 'none'."""
    if _STATE["checked"]:
        return _STATE["provider"]

    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "")

    if base and key and model:
        try:
            body = _post(f"{base}/chat/completions",
                         {"model": model, "messages": [{"role": "user", "content": "say ok"}],
                          "max_tokens": 8}, key)
            if _extract(body):
                _STATE.update({"provider": "custom", "checked": True,
                               "base": base, "model": model})
                return "custom"
        except ProviderError:
            pass

    if _gh_token():
        try:
            body = _post(GH_ENDPOINT,
                         {"model": "openai/gpt-4o-mini",
                          "messages": [{"role": "user", "content": "say ok"}],
                          "max_tokens": 8}, _gh_token())
            if _extract(body):
                _STATE.update({"provider": "github", "checked": True})
                return "github"
        except ProviderError:
            pass

    try:
        out = _pollinations("", "be terse", "say ok", 0.1)
        if out:
            _STATE.update({"provider": "pollinations", "checked": True})
            return "pollinations"
    except ProviderError:
        pass

    _STATE.update({"provider": "none", "checked": True})
    return "none"


def provider() -> str:
    return probe()


def complete(system: str, user: str, model: str, temperature: float = 0.9) -> str:
    kind = probe()
    if kind == "custom":
        body = _post(f"{_STATE['base']}/chat/completions",
                     {"model": _STATE["model"], "temperature": temperature,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                     os.environ["LLM_API_KEY"])
        return _extract(body)
    if kind == "github":
        body = _post(GH_ENDPOINT,
                     {"model": model, "temperature": temperature,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                     _gh_token())
        return _extract(body)
    if kind == "pollinations":
        return _pollinations(model, system, user, temperature)
    raise NoProvider("no model provider reachable")


def status() -> dict:
    return {"provider": provider(),
            "token": token_diagnosis(),
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
