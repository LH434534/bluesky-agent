"""Drain the queue to Bluesky. Autonomy gate lives here."""

import json
import sys
from datetime import datetime, timezone

from src import bluesky
from src import cfg as cfgmod
from src import queue as q


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    cfg = cfgmod.load()
    autonomy = int(cfg["autonomy"])

    if autonomy == 0:
        print("autonomy=0, queue waits for human merge")
        return 0

    if cfg.get("dry_run"):
        pending = q.read("queue")
        print(f"dry-run, {len(pending)} pending")
        return 0

    batch = q.take("queue", int(cfg["max_posts_per_run"]))
    if not batch:
        print("queue empty")
        return 0

    results = []
    for item in batch:
        text = item.get("text", "")
        if not text:
            continue
        try:
            res = bluesky.post(text, cfg["langs"])
            row = {**item, "uri": res.get("uri"), "cid": res.get("cid"), "postedAt": _now()}
            q.push("posted", row)
            q.cap("posted", 2000)
            results.append({"ok": True, "uri": row["uri"]})
        except bluesky.BskyError as e:
            item["error"] = str(e)[:300]
            q.push("queue", item)
            results.append({"ok": False, "error": item["error"]})
            break

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
