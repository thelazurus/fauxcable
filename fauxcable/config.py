import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# Written by the Settings page — lives in the persistent data volume
_SETTINGS_PATH = Path("data/config.yaml")
# Optional local file for dev convenience (not used in Docker)
_LOCAL_PATH = Path("config.yaml")


@dataclass
class Config:
    epg_url: str = ""
    base_url: str = "http://localhost:8000"
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""
    tmdb_enabled: bool = True
    tmdb_api_key: str = ""
    schedule_interval_hours: float = 6.0
    concurrency: int = 10
    rate_limit_delay: float = 0.10
    retry_attempts: int = 3
    retry_delay: int = 2
    ai_provider: str = "cloudflare"  # "cloudflare" | "fal"
    ai_account_id: str = ""          # Cloudflare Account ID (cloudflare provider only)
    ai_api_key: str = ""
    default_poster_url: str = ""     # Fallback poster when no category generic exists


def _apply_yaml(cfg: Config, data: dict) -> None:
    if v := data.get("epg_url") or data.get("dispatcharr_epg_url"):
        cfg.epg_url = v
    if "base_url" in data:
        cfg.base_url = data["base_url"].rstrip("/")
    jf = data.get("jellyfin", {})
    if "url" in jf:
        cfg.jellyfin_url = jf["url"]
    if "api_key" in jf:
        cfg.jellyfin_api_key = jf["api_key"]
    tmdb = data.get("tmdb", {})
    if "enabled" in tmdb:
        cfg.tmdb_enabled = tmdb["enabled"]
    if "api_key" in tmdb:
        cfg.tmdb_api_key = tmdb["api_key"]
    ai = data.get("ai", {})
    if "provider" in ai:
        cfg.ai_provider = str(ai["provider"])
    if "account_id" in ai:
        cfg.ai_account_id = str(ai["account_id"])
    if "api_key" in ai:
        cfg.ai_api_key = str(ai["api_key"])
    if v := data.get("default_poster_url"):
        cfg.default_poster_url = str(v)
    beh = data.get("behavior", {})
    if "schedule_interval_hours" in beh:
        cfg.schedule_interval_hours = float(beh["schedule_interval_hours"])
    if "concurrency" in beh:
        cfg.concurrency = int(beh["concurrency"])
    if "rate_limit_delay" in beh:
        cfg.rate_limit_delay = float(beh["rate_limit_delay"])
    if "retry_attempts" in beh:
        cfg.retry_attempts = int(beh["retry_attempts"])
    if "retry_delay" in beh:
        cfg.retry_delay = int(beh["retry_delay"])


def _apply_env(cfg: Config) -> None:
    if v := os.environ.get("EPG_URL") or os.environ.get("DISPATCHARR_EPG_URL"):
        cfg.epg_url = v
    if v := os.environ.get("FAUXCABLE_BASE_URL"):
        cfg.base_url = v.rstrip("/")
    if v := os.environ.get("JELLYFIN_URL"):
        cfg.jellyfin_url = v
    if v := os.environ.get("JELLYFIN_API_KEY"):
        cfg.jellyfin_api_key = v
    if v := os.environ.get("TMDB_API_KEY"):
        cfg.tmdb_api_key = v
    if v := os.environ.get("SCHEDULE_INTERVAL_HOURS"):
        cfg.schedule_interval_hours = float(v)
    if v := os.environ.get("CONCURRENCY"):
        cfg.concurrency = int(v)
    if v := os.environ.get("AI_PROVIDER"):
        cfg.ai_provider = v
    if v := os.environ.get("AI_ACCOUNT_ID"):
        cfg.ai_account_id = v
    if v := os.environ.get("AI_API_KEY"):
        cfg.ai_api_key = v


def load_config() -> Config:
    cfg = Config()

    # 1. Local config.yaml — dev convenience, ignored in Docker (no volume mount)
    if _LOCAL_PATH.exists():
        with open(_LOCAL_PATH, "r", encoding="utf-8") as f:
            _apply_yaml(cfg, yaml.safe_load(f) or {})

    # 2. Environment variables — primary config source in Docker
    _apply_env(cfg)

    # 3. Persistent Settings saves — written by the UI, stored in the data volume
    #    Takes highest priority so UI changes survive restarts
    if _SETTINGS_PATH.exists():
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            _apply_yaml(cfg, yaml.safe_load(f) or {})

    return cfg


_cfg: Config | None = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def reload_config() -> Config:
    global _cfg
    _cfg = load_config()
    return _cfg


def save_config(updates: dict):
    """Persist changes from the Settings page to data/config.yaml."""
    _SETTINGS_PATH.parent.mkdir(exist_ok=True)
    data: dict = {}
    if _SETTINGS_PATH.exists():
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    if "epg_url" in updates:
        data["epg_url"] = updates["epg_url"]
    if "base_url" in updates:
        data["base_url"] = updates["base_url"]
    if "jellyfin_url" in updates or "jellyfin_api_key" in updates:
        jf = data.setdefault("jellyfin", {})
        if "jellyfin_url" in updates:
            jf["url"] = updates["jellyfin_url"]
        if "jellyfin_api_key" in updates:
            jf["api_key"] = updates["jellyfin_api_key"]
    if "tmdb_enabled" in updates or "tmdb_api_key" in updates:
        tmdb = data.setdefault("tmdb", {})
        if "tmdb_enabled" in updates:
            tmdb["enabled"] = updates["tmdb_enabled"]
        if "tmdb_api_key" in updates:
            tmdb["api_key"] = updates["tmdb_api_key"]
    if any(k in updates for k in ("ai_provider", "ai_account_id", "ai_api_key")):
        ai = data.setdefault("ai", {})
        if "ai_provider" in updates:
            ai["provider"] = updates["ai_provider"]
        if "ai_account_id" in updates:
            ai["account_id"] = updates["ai_account_id"]
        if "ai_api_key" in updates:
            ai["api_key"] = updates["ai_api_key"]
    if "default_poster_url" in updates:
        data["default_poster_url"] = updates["default_poster_url"]
    beh = data.setdefault("behavior", {})
    for key in ("schedule_interval_hours", "concurrency", "rate_limit_delay", "retry_attempts", "retry_delay"):
        if key in updates:
            beh[key] = updates[key]

    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    reload_config()
