"""Durable JSON queue. The repo is the database."""

import json
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state"

_MEMO: dict = {}


def _path(name: str) -> pathlib.Path:
    return STATE / f"{name}.json"


def read(name: str) -> list:
    if name in _MEMO:
        return _MEMO[name]
    p = _path(name)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write(name: str, rows: list) -> None:
    _MEMO[name] = rows
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def push(name: str, row: dict) -> None:
    rows = read(name)
    rows.append(row)
    write(name, rows)


def take(name: str, n: int) -> list:
    rows = read(name)
    head, tail = rows[:n], rows[n:]
    if head:
        write(name, tail)
    return head


def cap(name: str, limit: int = 500) -> None:
    rows = read(name)
    if len(rows) > limit:
        write(name, rows[-limit:])
