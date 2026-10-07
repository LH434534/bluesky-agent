"""Entrypoint for the news pipeline workflow."""

import json
import sys

from src import fame


def main() -> int:
    out = fame.run()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("queued") else 1


if __name__ == "__main__":
    sys.exit(main())
