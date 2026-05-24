import os
import yaml
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("FAUXCABLE_CONFIG", "config.yaml"))


@dataclass
class Config:
    dispatcharr_epg_url: str = ""
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


def load_config() -> Config:
    cfg = Config()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg.dispatcharr_epg_url = data.get("dispatcharr_epg_url", cfg.dispatcharr_epg_url)
        cfg.base_url = data.get("base_url", cfg.base_url).rstrip("/")
        jf = data.get("jellyfin", {})
        cfg.jellyfin_url = jf.get("url", cfg.jellyfin_url)
        cfg.jellyfin_api_key = jf.get("api_key", cfg.jellyfin_api_key)
        tmdb = data.get("tmdb", {})
        cfg.tmdb_enabled = tmdb.get("enabled", cfg.tmdb_enabled)
        cfg.tmdb_api_key = tmdb.get("api_key", cfg.tmdb_api_key)
        beh = data.get("behavior", {})
        cfg.schedule_interval_hours = float(beh.get("schedule_interval_hours", cfg.schedule_interval_hours))
        cfg.concurrency = int(beh.get("concurrency", cfg.concurrency))
        cfg.rate_limit_delay = float(beh.get("rate_limit_delay", cfg.rate_limit_delay))
        cfg.retry_attempts = int(beh.get("retry_attempts", cfg.retry_attempts))
        cfg.retry_delay = int(beh.get("retry_delay", cfg.retry_delay))

    # env var overrides (useful for Docker secrets)
    cfg.dispatcharr_epg_url = os.environ.get("DISPATCHARR_EPG_URL", cfg.dispatcharr_epg_url)
    cfg.base_url = os.environ.get("FAUXCABLE_BASE_URL", cfg.base_url).rstrip("/")
    cfg.jellyfin_url = os.environ.get("JELLYFIN_URL", cfg.jellyfin_url)
    cfg.jellyfin_api_key = os.environ.get("JELLYFIN_API_KEY", cfg.jellyfin_api_key)
    cfg.tmdb_api_key = os.environ.get("TMDB_API_KEY", cfg.tmdb_api_key)
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
    """Write updated values back to config.yaml."""
    data: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    if "dispatcharr_epg_url" in updates:
        data["dispatcharr_epg_url"] = updates["dispatcharr_epg_url"]
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
    beh = data.setdefault("behavior", {})
    for key in ("schedule_interval_hours", "concurrency", "rate_limit_delay", "retry_attempts", "retry_delay"):
        if key in updates:
            beh[key] = updates[key]

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    reload_config()
