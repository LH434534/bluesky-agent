"""Entrypoint for the autonomous action workflow."""

import json
import sys

from src import act


def main() -> int:
    out = act.run()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
