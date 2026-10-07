"""Static dashboard generator. No server, no framework, no build step.

Reads state/*.json + ledger.json, writes one HTML file for GitHub Pages.
"""

import html
import json
import pathlib
from datetime import datetime, timedelta, timezone

from src import cfg as cfgmod
from src import models
from src import policy
from src import queue as q
from src import timing

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _ago(ts: str) -> str:
    if not ts:
        return "never"
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    d = datetime.now(timezone.utc) - then
    if d < timedelta(minutes=1):
        return "agora"
    if d < timedelta(hours=1):
        return f"{int(d.total_seconds()//60)}min"
    if d < timedelta(days=1):
        return f"{int(d.total_seconds()//3600)}h"
    return f"{d.days}d"


def _bars(cfg: dict) -> str:
    rows = []
    for kind in ("post", "reply", "like", "follow", "repost"):
        sec = cfg.get(kind, {})
        if not sec:
            continue
        used = policy.count_today(kind)
        budget = policy.daily_budget(kind, cfg)
        pct = 0 if budget == 0 else min(100, int(used / budget * 100))
        state = "ok" if pct < 70 else ("warn" if pct < 95 else "hot")
        rows.append(
            f"""<div class="row">
  <span class="k">{_esc(kind)}</span>
  <div class="bar"><i class="{state}" style="width:{pct}%"></i></div>
  <span class="v">{used}/{budget}</span>
</div>"""
        )
    return "\n".join(rows)


def _activity() -> str:
    rows = q.read("activity")[-40:]
    if not rows:
        return '<p class="empty">sem atividade ainda</p>'
    out = []
    for r in reversed(rows):
        ok = r.get("ok")
        cls = "ok" if ok else ("dry" if r.get("dry") else "bad")
        label = f'{r.get("kind","?")}'
        if r.get("to"):
            label += f' → @{_esc(r.get("to"))}'
        detail = r.get("text") or r.get("reason") or r.get("error") or ""
        out.append(
            f'<li class="{cls}"><b>{_esc(label)}</b>'
            f'<span class="t">{_ago(r.get("at",""))}</span>'
            f'<div class="d">{_esc(detail)[:180]}</div></li>'
        )
    return "<ul class=\"feed\">" + "\n".join(out) + "</ul>"


def _health(cfg: dict) -> tuple[str, str]:
    if policy.kill_switch_on(cfg):
        return "stopped", "kill switch ativo — nada executa"
    if policy.circuit_open(cfg):
        errs = policy.recent_errors(1)
        return "paused", f'circuit breaker aberto: {_esc(errs[-1].get("error","")[:120] if errs else "")}'
    led = policy.read_ledger()
    if not led["actions"]:
        return "idle", "nenhuma ação registrada ainda"
    last = led["actions"][-1]
    ts = last.get("at", "")
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - then).total_seconds() / 3600
    except ValueError:
        return "unknown", "timestamp inválido"
    if hours > 6:
        return "warn", f"última ação há {hours:.1f}h"
    return "live", f"ativo — última ação há {_ago(ts)}"


def _models() -> str:
    rows = []
    for model in sorted(models.CATALOG):
        used = models.spent(model)
        limit = models.cap(model)
        pct = min(100, int(used / limit * 100))
        state = "ok" if pct < 70 else ("warn" if pct < 95 else "hot")
        rows.append(
            f'<div class="row"><span class="k wide">{_esc(model.split("/")[-1])}</span>'
            f'<div class="bar"><i class="{state}" style="width:{pct}%"></i></div>'
            f'<span class="v">{used}/{limit}</span></div>'
        )
    return "\n".join(rows)


def _news() -> str:
    rows = q.read("newslog")[-12:]
    if not rows:
        return '<p class="empty">sem execu\u00e7\u00e3o de not\u00edcias ainda</p>'
    out = []
    for r in reversed(rows):
        cls = "ok" if r.get("ok") else "bad"
        head = r.get("headline") or r.get("reason") or ""
        doms = ", ".join((r.get("domains") or [])[:3])
        out.append(
            f'<li class="{cls}"><b>{_esc(r.get("confidence",""))}</b>'
            f'<span class="t">{_ago(r.get("at",""))}</span>'
            f'<div class="d">{_esc(head)[:150]}</div>'
            f'<div class="d">{_esc(doms)}</div></li>'
        )
    return '<ul class="feed">' + "\n".join(out) + "</ul>"


def build() -> pathlib.Path:
    cfg = cfgmod.load()
    status, note = _health(cfg)
    led = policy.read_ledger()

    posted = q.read("posted")
    pending = q.read("queue")
    rejected = q.read("rejected")

    kinds: dict[str, int] = {}
    for a in led["actions"]:
        kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1

    doc = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>bluesky-agent — painel</title>
<meta http-equiv="refresh" content="300">
<style>
:root{{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--dim:#8b949e;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--dry:#58a6ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}
.wrap{{max-width:860px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:18px;margin:0 0 4px}}
.sub{{color:var(--dim);font-size:12px;margin-bottom:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:16px;margin-bottom:16px}}
.card h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
color:var(--dim);margin:0 0 12px;font-weight:600}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}}
.live{{background:var(--ok)}}.warn{{background:var(--warn)}}
.bad,.stopped{{background:var(--bad)}}.paused{{background:var(--dry)}}
.idle,.unknown{{background:var(--dim)}}
.row{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}
.k{{width:64px;color:var(--dim);font-size:12px}}
.k.wide{{width:150px}}
.bar{{flex:1;height:8px;background:#0d1117;border-radius:4px;overflow:hidden}}
.bar i{{display:block;height:100%;border-radius:4px}}
.bar .ok{{background:var(--ok)}}.bar .warn{{background:var(--warn)}}
.bar .hot{{background:var(--bad)}}
.v{{width:56px;text-align:right;font-size:12px;color:var(--dim)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px}}
.stat{{background:#0d1117;border:1px solid var(--line);border-radius:6px;padding:10px}}
.stat b{{display:block;font-size:20px}}
.stat span{{font-size:11px;color:var(--dim)}}
.feed{{list-style:none;margin:0;padding:0;max-height:460px;overflow:auto}}
.feed li{{border-bottom:1px solid var(--line);padding:8px 0;font-size:12px}}
.feed li:last-child{{border:0}}
.feed .ok b{{color:var(--ok)}}.feed .bad b{{color:var(--bad)}}
.feed .dry b{{color:var(--dry)}}
.feed .t{{float:right;color:var(--dim);font-size:11px}}
.feed .d{{color:var(--dim);margin-top:2px;word-break:break-word}}
.empty{{color:var(--dim);font-size:12px}}
code{{background:#0d1117;padding:2px 5px;border-radius:4px;font-size:12px}}
</style>
</head>
<body><div class="wrap">
<h1><span class="dot {status}"></span>bluesky-agent</h1>
<div class="sub">{_esc(note)} · autonomy {_esc(cfg.get("autonomy"))} · gera a cada 5min</div>

<div class="card">
<h2>Cotas hoje (anti-ban)</h2>
{_bars(cfg)}
</div>

<div class="card">
<h2>Volume</h2>
<div class="grid">
<div class="stat"><b>{len(posted)}</b><span>posts</span></div>
<div class="stat"><b>{len(pending)}</b><span>na fila</span></div>
<div class="stat"><b>{len(rejected)}</b><span>rejeitados</span></div>
<div class="stat"><b>{len(led["followed"])}</b><span>seguindo</span></div>
<div class="stat"><b>{sum(kinds.values())}</b><span>ações total</span></div>
<div class="stat"><b>{len(led["errors"])}</b><span>erros</span></div>
</div>
</div>

<div class="card">
<h2>Not\u00edcias confirmadas</h2>
{_news()}
</div>

<div class="card">
<h2>Or\u00e7amento de modelo (GitHub Models)</h2>
{_models()}
</div>

<div class="card">
<h2>Melhores hor\u00e1rios</h2>
<p class="empty">{_esc(timing.summary())}<br>pico agora: {_esc(timing.best_hours(cfg))} · boost {_esc(timing.boost(cfg))}</p>
</div>

<div class="card">
<h2>Atividade</h2>
{_activity()}
</div>

<div class="card">
<h2>Controle</h2>
<p class="empty">
Crie <code>state/STOP</code> no repo para parar tudo instantaneamente.
Apague o arquivo para retomar. Nenhuma ação roda com ele presente.
</p>
</div>
</div></body></html>
"""

    out = ROOT / cfg.get("site_output", "docs/index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())
