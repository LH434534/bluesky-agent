"""Run the bot locally against the mock wire.

Real code, fake socket. Use this to watch the agent decide before you point
it at a live account.

    python tools/run_local.py --cycles 6 --dry 0
"""

import argparse
import io
import json
import os
import pathlib
import random
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.mocknet as mocknet  # noqa: E402

STATE = ROOT / "state"


def _reset():
    for f in ("activity", "queue", "posted", "rejected", "newslog"):
        (STATE / f"{f}.json").write_text("[]", encoding="utf-8")
    (STATE / "ledger.json").write_text("{}", encoding="utf-8")
    (STATE / "targets.json").write_text('{"accounts": [], "updated": ""}', encoding="utf-8")
    (STATE / "timing.json").write_text('{"hours": {}, "updated": ""}', encoding="utf-8")


def _banner(text: str) -> None:
    print("\n" + "=" * 62)
    print(text)
    print("=" * 62)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=6)
    ap.add_argument("--dry", type=int, default=0)
    ap.add_argument("--seed", type=int, default=4080)
    ap.add_argument("--advance-minutes", type=int, default=0,
                    help="age the ledger between cycles, simulating time passing")
    ap.add_argument("--reset", type=int, default=1)
    args = ap.parse_args()

    random.seed(args.seed)
    mocknet.install()

    os.environ["BSKY_HANDLE"] = "local.test.bsky.social"
    os.environ["BSKY_APP_PASSWORD"] = "xxxx-xxxx-xxxx-xxxx"
    os.environ["GITHUB_TOKEN"] = "ghp_localmock"
    os.environ["AUTONOMY"] = "2"
    os.environ["DRY_RUN"] = str(args.dry)

    if args.reset:
        _reset()

    from datetime import timedelta

    import queue as qmod
    from src import act, fame, policy, queue as q, site, timing

    def advance(minutes: int) -> None:
        """Shift every recorded timestamp back, so gaps and quotas release."""
        if minutes <= 0:
            return
        led = policy.read_ledger()
        for row in led.get("actions", []):
            at = row.get("at", "")
            try:
                then = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError:
                continue
            row["at"] = (then - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        for kind, at in list(led.get("last", {}).items()):
            try:
                then = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError:
                continue
            led["last"][kind] = (then - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        policy.write_ledger(led)

    q._MEMO.clear()
    policy._CACHE = None

    _banner(f"bluesky-agent  |  cycles={args.cycles}  dry_run={args.dry}  seed={args.seed}")
    print(f"started {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")

    for c in range(1, args.cycles + 1):
        print(f"\n--- cycle {c} ---")
        if c > 1:
            advance(args.advance_minutes)

        out = fame.run()
        if out.get("queued"):
            print(f"  NEWS   +1 queued  [{out.get('confidence')}] {out.get('headline','')[:58]}")
            print(f"         domains: {', '.join(out.get('domains', [])[:3])}")
        else:
            print(f"  NEWS   none: {out.get('reason','')[:58]}")

        res = act.run()
        done = res.get("ran") or []
        print(f"  SESSION {len(done)} action(s): {', '.join(done) if done else 'idle'}")
        if res.get("skipped"):
            print(f"  skip   {', '.join(res['skipped'])}")

        pend = len(q.read("queue"))
        posted = len(q.read("posted"))
        print(f"  queue={pend}  posted={posted}  rejected={len(q.read('rejected'))}")

    _banner("RESULTADO")

    print("\nPUBLISHED ON BLUESKY:")
    for i, rec in enumerate(mocknet.POSTED, 1):
        t = rec.get("$type", "")
        kind = t.split(".")[-1] if t else "record"
        subj = rec.get("subject", "")
        if isinstance(subj, dict):
            subj = subj.get("uri", "")
        body = rec.get("text") or subj or ""
        print(f'  {i}. [{kind:6}] {str(body)[:76]}')

    print(f"\nREJEICOES (ultimas 8):")
    for r in q.read("rejected")[-8:]:
        print(f'  - {str(r.get("reason",""))[:74]}')

    print(f"\nATIVIDADE (ultimas 12):")
    for a in q.read("activity")[-12:]:
        mark = "OK " if a.get("ok") else "NO "
        target = f'→ @{a.get("to")}' if a.get("to") else ""
        detail = (a.get("text") or a.get("reason") or a.get("error") or "")[:52]
        print(f'  {mark} {a.get("kind","?"):7} {target:22} {detail}')

    led = policy.read_ledger()
    from src import cfg as cfgmod
    cfg = cfgmod.load()
    print("\nCOTAS (hoje):")
    for kind in ("post", "reply", "like", "follow", "repost"):
        print(f"  {kind:7} {policy.count_today(kind):3}/{policy.daily_budget(kind, cfg)}")

    print("\nMODELO (chamadas hoje):")
    days = led.get("models") or {}
    for day, row in days.items():
        for m, used in sorted(row.items(), key=lambda kv: -kv[1]):
            from src import models as mmod
            print(f"  {m:32} {used:3}/{mmod.cap(m)}")
        if not row:
            print("  (nenhuma)")

    print(f"\nTIMING aprendido: {timing.summary()}")

    print(f"\nHTTP calls feitas: {len(mocknet.CALLS)}")
    seen = {}
    for u in mocknet.CALLS:
        seen[u] = seen.get(u, 0) + 1
    for u, n in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {n:3}x {u}")

    out = site.build()
    print(f"\npainel: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
