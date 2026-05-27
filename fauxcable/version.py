from __future__ import annotations
import os
import subprocess


def _read_commit_id() -> str:
    # Baked in at Docker build time via ARG/ENV GIT_COMMIT
    env_val = os.environ.get("GIT_COMMIT", "").strip()
    if env_val and env_val != "unknown":
        return env_val[:7]  # full SHA from github.sha — trim to short form
    # Fall back to git for local (non-Docker) development
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


COMMIT_ID = _read_commit_id()
