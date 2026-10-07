"""Validate credentials and wiring BEFORE the bot ever posts.

Reads secrets from environment only. Never echoes them, never writes them to
a file, never puts them in config.yml. Prints masked status lines.

    export BSKY_HANDLE=lh434534.github.io
    export BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
    export GITHUB_TOKEN=ghp_...
    python tools/preflight.py --write-config
"""

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = "bluesky-agent-preflight/1.0"
TIMEOUT = 20


def mask(v: str | None) -> str:
    if not v:
        return "(ausente)"
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:3]}{'*' * (len(v) - 7)}{v[-4:]}"


def _get(url: str, token: str | None = None) -> tuple[int, bytes]:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        return 0, str(e.reason).encode()


def _post(url: str, payload: dict | None = None, token: str | None = None) -> tuple[int, bytes]:
    data = json.dumps(payload or {}).encode()
    h = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        return 0, str(e.reason).encode()


def line(ok: bool, label: str, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:24} {detail}")
    return ok


def check_handle(handle: str, pds: str) -> tuple[bool, str]:
    """Resolve handle -> DID. Tries PDS, then the public AppView."""
    q = urllib.parse.urlencode({"handle": handle})
    for base in (pds, "https://public.api.bsky.app"):
        code, body = _get(f"{base}/xrpc/com.atproto.identity.resolveHandle?{q}")
        if code == 200:
            try:
                did = json.loads(body).get("did", "")
            except json.JSONDecodeError:
                continue
            if did:
                return True, did
    # github.io handles are usually resolved via well-known on the Pages domain
    code, body = _get(f"https://{handle}/.well-known/atproto-did")
    if code == 200:
        txt = body.decode("utf-8", "replace").strip()
        if txt.startswith("did:"):
            return True, txt
    return False, "handle nao resolveu"


def check_session(handle: str, password: str, pds: str) -> tuple[bool, str]:
    code, body = _post(
        f"{pds}/xrpc/com.atproto.server.createSession",
        {"identifier": handle, "password": password},
    )
    if code == 200:
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            return False, "resposta invalida"
        return True, f'sessao ok (did={d.get("did", "?")[:24]})'
    if code == 401:
        return False, "401 — app password errada ou nao e App Password"
    if code == 429:
        return False, "429 — rate limit, tente de novo em alguns minutos"
    if code == 0:
        return False, f"sem rede ({body.decode('utf-8','replace')[:60]})"
    return False, f"HTTP {code}: {body.decode('utf-8','replace')[:90]}"


def check_models(token: str, model: str) -> tuple[bool, str]:
    code, body = _post(
        "https://models.github.ai/inference/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": "reply with: ok"}],
         "max_tokens": 8},
        token=token,
    )
    if code == 200:
        return True, f"{model} respondeu"
    if code == 401:
        return False, "401 — token sem escopo models:read ou expirado"
    if code == 429:
        return False, "429 — cota do dia esgotada"
    if code == 0:
        return False, f"sem rede ({body.decode('utf-8','replace')[:60]})"
    return False, f"HTTP {code}: {body.decode('utf-8','replace')[:90]}"


def write_handle(handle: str) -> None:
    p = ROOT / "config.yml"
    s = p.read_text(encoding="utf-8")
    if re.search(r"^handle:\s*.*$", s, re.M):
        s = re.sub(r"^handle:\s*.*$", f"handle: {handle}", s, count=1, flags=re.M)
    else:
        s = s.replace("handle_env: BSKY_HANDLE", f"handle_env: BSKY_HANDLE\nhandle: {handle}", 1)
    p.write_text(s, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-config", action="store_true")
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--offline", action="store_true", help="roda contra o mock local")
    args = ap.parse_args()

    if args.offline:
        import tools.mocknet as mocknet
        mocknet.install()

    handle = os.environ.get("BSKY_HANDLE", "")
    password = os.environ.get("BSKY_APP_PASSWORD", "")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    pds = os.environ.get("PDS", "https://bsky.social").rstrip("/")

    print("=" * 64)
    print("PREFLIGHT")
    print("=" * 64)
    print(f"  handle  {handle or '(ausente)'}")
    print(f"  app pw  {mask(password)}")
    print(f"  gh tok  {mask(token)}")
    print(f"  pds     {pds}")
    print()

    results = []

    print("IDENTIDADE")
    if not handle:
        results.append(line(False, "handle", "BSKY_HANDLE nao definido"))
        did = ""
    else:
        ok, did = check_handle(handle, pds)
        line(ok, "resolve handle", did or "nao resolveu")

    print("\nBLUESKY")
    if not (handle and password):
        results.append(line(False, "createSession", "faltam credenciais"))
        session_ok = False
    else:
        session_ok, detail = check_session(handle, password, pds)
        results.append(line(session_ok, "createSession", detail))
        if session_ok and not did:
            print("  [WARN] resolve handle     sessao abriu, entao o handle serve "
                  "pra postar mesmo sem resolver publico")
        if not session_ok and handle and not handle.startswith("did:"):
            print("  [HINT] tente BSKY_HANDLE com o DID (did:plc:...) ou o handle "
                  "principal da conta")

    print("\nGITHUB MODELS")
    if args.skip_models:
        print("  [SKIP] verificado")
    elif not token:
        results.append(line(False, "chat/completions", "GITHUB_TOKEN nao definido"))
    else:
        ok, detail = check_models(token, "openai/gpt-4o-mini")
        results.append(line(ok, "chat/completions", detail))

    if args.write_config and handle:
        write_handle(handle)
        print(f"\n  handle gravado em config.yml: {handle}")
    elif handle:
        print(f"\n  (use --write-config para gravar '{handle}' em config.yml)")

    print()
    print("=" * 64)
    if all(results):
        print("TUDO OK — pode liberar o bot")
        print("rode primeiro: DRY_RUN=1 python -m src.act_run")
        return 0
    print("ALGO FALHOU — nao libere ate os FAILs sumirem")
    return 1


if __name__ == "__main__":
    sys.exit(main())
