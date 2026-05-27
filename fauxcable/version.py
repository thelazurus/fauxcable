from __future__ import annotations
import subprocess


def _read_commit_id() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


COMMIT_ID = _read_commit_id()
