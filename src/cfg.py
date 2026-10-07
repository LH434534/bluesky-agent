"""Config loader. Env overrides file so CI can tune without commits."""

import os
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"


def _env(name: str, default):
    """Empty string counts as unset. CI passes empty vars for unset secrets."""
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v


def load() -> dict:
    cfg = yaml.safe_load((CONFIG_PATH).read_text(encoding="utf-8")) or {}
    cfg.setdefault("pds", "https://bsky.social")
    cfg.setdefault("model", "openai/gpt-4o-mini")
    cfg.setdefault("max_chars", 300)
    cfg.setdefault("dedupe_window", 200)
    cfg.setdefault("max_posts_per_run", 1)
    cfg.setdefault("langs", ["pt-BR"])
    cfg.setdefault("topics", [])
    cfg.setdefault("voice_file", "state/voice.md")

    cfg["autonomy"] = int(_env("AUTONOMY", cfg.get("autonomy", 2)))
    cfg["dry_run"] = str(_env("DRY_RUN", "0")) == "1"
    if cfg.get("pds"):
        os.environ.setdefault("PDS", cfg["pds"])
    if cfg.get("handle") and not os.environ.get("BSKY_HANDLE", "").strip():
        os.environ["BSKY_HANDLE"] = cfg["handle"]
    return cfg


def voice(cfg: dict) -> str:
    return (ROOT / cfg["voice_file"]).read_text(encoding="utf-8")
