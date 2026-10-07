"""Self-audit. Runs the whole pipeline against fixtures and reports failures.

Called from CI on every push and on a schedule. Exit 0 means every invariant
holds; nonzero means a real bug, and the run log shows which one.
"""

import json
import os
import pathlib
import sys
import tempfile
import traceback
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        PASSES.append(name)
    except AssertionError as e:
        FAILS.append(f"{name}: {e}")
    except Exception:
        FAILS.append(f"{name}: {traceback.format_exc(limit=3).strip()[-300:]}")


def eq(got, want, label: str) -> None:
    assert got == want, f"{label}: got {got!r}, want {want!r}"


def truthy(v, label: str) -> None:
    assert v, f"{label}: falsy ({v!r})"


def _isolate() -> pathlib.Path:
    """Point state at a temp dir so audits never touch real history."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="audit-"))
    os.environ["AUDIT_STATE"] = str(tmp)
    return tmp


def audit_numbers() -> None:
    from src import ground
    cases = {
        "100k": 100000, "100,000": 100000, "100.000": 100000,
        "1.5 milhao": 1500000, "2.5 million": 2500000,
        "1,234": 1234, "2026": 2026, "40k writes": 40000,
    }
    for raw, want in cases.items():
        got = ground._numbers(raw)
        truthy(want in got, f"_numbers({raw!r}) -> {got}")


def audit_headline_clean() -> None:
    from src import ground
    dirty = ("Smith & Nephew Plc : Smith+Nephew revolutionary implant "
             "launches in Europe ; to be featured at ICRS Summit")
    clean = ground._clean_headline(dirty)
    assert "Smith & Nephew Plc :" not in clean, f"ticker prefix survived: {clean}"
    assert "to be featured" not in clean, f"trailing clause survived: {clean}"


def audit_offbrand_skip() -> None:
    from src import ground
    for h in ("death row inmate awake after failed execution",
              "man arrested for stabbing outside stadium"):
        c = {"headline": h, "domains": ["x.com"], "url": "https://x.com/a",
             "confidence": "multi-source"}
        r = ground._fact_only(c, {"post": {"max_chars": 300}})
        eq(r.get("ok"), False, f"off-brand must be skipped: {h}")


def audit_market_skip() -> None:
    from src import ground
    c = {"headline": "Acme shares rose 5% after quarterly results beat guidance",
         "domains": ["x.com"], "url": "https://x.com/a", "confidence": "multi-source"}
    r = ground._fact_only(c, {"post": {"max_chars": 300}})
    eq(r.get("ok"), False, "finance wire must be skipped")


def audit_fact_only() -> None:
    from src import ground
    c = {"headline": "Europe launches weather satellite to improve storm forecasts",
         "domains": ["bbc.co.uk"], "url": "https://www.bbc.co.uk/news/1",
         "confidence": "multi-source"}
    r = ground._fact_only(c, {"post": {"max_chars": 300}})
    truthy(r.get("ok"), "fact_only ok")
    truthy("satellite" in r["text"], "headline preserved")
    truthy(len(r["text"]) <= 300, "under 300 chars")


def audit_domain_word() -> None:
    from src import ground
    eq(ground._domain_word("https://www.bbc.co.uk/news/1"), "bbc", "bbc")
    eq(ground._domain_word("https://github.blog/a"), "github", "github.blog")
    truthy(ground._domain_word("https://www.wired.com/story/x") == "wired", "wired")


def audit_spam_gate() -> None:
    from src import cfg as cfgmod, spam
    cfg = cfgmod.load()
    truthy("blocklist_terms" in cfg["safety"], "safety.blocklist_terms present")
    ok, why = spam.scan("crypto airdrop free mint dm me", cfg)
    truthy(not ok, f"blocklist must catch spam (why={why})")
    ok2, _ = spam.scan("Migrei meu homelab pra caddy e caiu de 90 pra 9 linhas", cfg)
    truthy(ok2, "clean text must pass")


def audit_dedupe() -> None:
    from src import dedupe
    base = "Bancos centrais mantiveram juros e inflacao esfriando"
    truthy(dedupe.too_similar(base, [base]), "identical text is a duplicate")
    truthy(not dedupe.too_similar(base, ["Gato dorme no sol a tarde inteira"]),
           "unrelated text is not a duplicate")


def audit_policy() -> None:
    from src import cfg as cfgmod, policy
    cfg = cfgmod.load()
    for kind in ("post", "reply", "like", "follow", "repost"):
        truthy(policy.daily_budget(kind, cfg) > 0, f"{kind} budget positive")
    for kind in ("post", "reply", "like"):
        truthy(kind in cfg, f"config missing {kind}")


def audit_facets() -> None:
    from src import bluesky
    text = "Europe launches weather satellite (bbc)"
    f = bluesky._facets_for(text, "https://bbc.co.uk/a")
    truthy(len(f) == 1, "one facet")
    b = text.encode()
    got = b[f[0]["index"]["byteStart"]:f[0]["index"]["byteEnd"]].decode()
    eq(got, "bbc", "facet byte window")


def audit_rss_parse() -> None:
    from src import news
    FIX = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
           b'<item><title>Brazil central bank holds rates</title>'
           b'<link>https://www.bbc.co.uk/news/1</link></item>'
           b'</channel></rss>')
    news._fetch = lambda u: FIX
    items = news.rss("x")
    truthy(len(items) == 1, "one rss item")
    eq(items[0]["domain"], "bbc.co.uk", "rss domain")


def audit_config_valid() -> None:
    from src import cfg as cfgmod
    c = cfgmod.load()
    for key in ("topics", "post", "safety", "news", "voice_file"):
        truthy(key in c, f"config missing {key}")
    truthy(len(c["topics"]) > 0, "topics non-empty")


def audit_workflows_syntax() -> None:
    import yaml
    wf = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    truthy(len(wf) >= 5, "workflows present")
    for f in wf:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        truthy(isinstance(d, dict) and "jobs" in d, f"{f.name}: no jobs")


def audit_no_secrets() -> None:
    import re
    # a real gh token is ghp_ + 36 alnum; a doc placeholder is not
    _ghp = re.compile(r"ghp_[A-Za-z0-9]{30,}")
    _pat = re.compile(r"github_pat_[A-Za-z0-9_]{30,}")
    _sk = re.compile(r"sk-[A-Za-z0-9]{24,}")
    pats = (_ghp, _pat, _sk)
    for p in ROOT.rglob("*"):
        if not p.is_file() or ".git" in p.parts or "__pycache__" in p.parts:
            continue
        if p.stat().st_size > 700_000:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # .env.example is allowed to show the shape of a key, never a real one
        if p.name == ".env.example":
            continue
        for pat in pats:
            m = pat.search(t)
            if m:
                raise AssertionError(f"{p.relative_to(ROOT)}: {m.group()[:8]}...")


def audit_compiles() -> None:
    import py_compile
    for p in sorted((ROOT / "src").glob("*.py")):
        py_compile.compile(str(p), doraise=True)


def main() -> int:
    _isolate()
    checks = [
        ("numbers", audit_numbers),
        ("offbrand-skip", audit_offbrand_skip),
        ("headline-clean", audit_headline_clean),
        ("market-skip", audit_market_skip),
        ("fact-only", audit_fact_only),
        ("domain-word", audit_domain_word),
        ("spam-gate", audit_spam_gate),
        ("dedupe", audit_dedupe),
        ("policy-budget", audit_policy),
        ("facets", audit_facets),
        ("rss-parse", audit_rss_parse),
        ("config-valid", audit_config_valid),
        ("workflow-syntax", audit_workflows_syntax),
        ("no-secrets", audit_no_secrets),
        ("compiles", audit_compiles),
    ]
    for name, fn in checks:
        check(name, fn)

    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report = {"at": stamp, "passed": len(PASSES), "failed": len(FAILS),
              "passes": PASSES, "fails": FAILS}
    print(json.dumps(report, ensure_ascii=False, indent=2))

    dest = ROOT / "state" / "audit.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
