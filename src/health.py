"""Fail loudly if the pipeline stopped producing."""

import sys

from src import queue as q


def main() -> int:
    pending = len(q.read("queue"))
    posted = len(q.read("posted"))
    rejected = len(q.read("rejected"))
    print(f"pending={pending} posted={posted} rejected={rejected}")
    if pending == 0 and posted == 0:
        print("FATAL: agent never produced anything")
        return 1
    if pending == 0:
        print("WARN: queue empty")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
