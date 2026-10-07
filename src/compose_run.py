"""Entrypoint for the compose workflow."""

import json
import sys

from src import compose


def main() -> int:
    out = compose.run(attempts=3)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("queued") else 1


if __name__ == "__main__":
    sys.exit(main())
