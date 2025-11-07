#!/usr/bin/env python3
import json, time, yaml, logging, re, unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

# --------------------------
# Load config
# --------------------------
CONFIG = yaml.safe_load(open("config.yaml"))
INPUT   = Path(CONFIG["paths"]["input"])
OUTPUT  = Path(CONFIG["paths"]["output"])
CACHE   = Path(CONFIG["paths"]["cache"])
LOGFILE = Path(CONFIG["paths"]["log"])
ASSETS  = Path(CONFIG["paths"]["assets"])

JELLYFIN_URL    = CONFIG["jellyfin"]["url"]
JELLYFIN_APIKEY = CONFIG["jellyfin"]["apikey"]

BATCH_SIZE        = int(CONFIG["behavior"]["batch_size"])
SHOW_PROGRESS_ETA = bool(CONFIG["behavior"].get("show_progress_eta", True))
LOG_LEVEL         = CONFIG["behavior"].get("log_level", "INFO").upper()

# --------------------------
# Logging setup
# --------------------------
LOGFILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOGFILE, encoding="utf-8"),
              logging.StreamHandler()],
    force=True,
)
log = logging.getLogger("fauxcable")

# --------------------------
# Generic poster mapping (case-insensitive)
# --------------------------
GENERIC_CATEGORY_MAP = {
    # --- News / Info ---
    "news": "generic_news.png",
    "newsmagazine": "generic_news.png",
    "weather": "generic_weather.png",
    "politics": "generic_news.png",
    "public affairs": "generic_publicaccess.png",

    # --- Religion / Spiritual ---
    "religious": "generic_religious.png",
    "religion": "generic_religious.png",
    "gospel": "generic_religious.png",
    "astrological guidance": "generic_religious.png",

    # --- Infomercials / Shopping ---
    "shopping": "generic_infomercial.png",
    "infomercial": "generic_infomercial.png",
    "consumer": "generic_infomercial.png",
    "paid programming": "generic_paidprogramming.png",
    "auction": "generic_infomercial.png",

    # --- Community / Local ---
    "community": "generic_publicaccess.png",
    "fundraiser": "generic_publicaccess.png",
    "local event": "generic_publicaccess.png",
    "parade": "generic_publicaccess.png",
    "town hall": "generic_publicaccess.png",

    # --- Off-air / Unknown ---
    "off air": "generic_unknown.png",
    "tba": "generic_unknown.png",
    "special": "generic_unknown.png",
    "event": "generic_unknown.png",
}

GENERIC_UNKNOWN = "generic_unknown.png"

# --------------------------
# Cache utilities
# --------------------------
def load_cache():
    if CACHE.exists():
        with open(CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"Loaded cache with {len(data)} entries.")
        return data
    log.info("No existing cache found. Starting fresh.")
    return {}

def save_cache(cache):
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

# --------------------------
# Normalization
# --------------------------
def normalize_title(title: str) -> str:
    """Normalize titles to prevent duplicate lookups (ᴺᵉʷ, etc.)."""
    title = unicodedata.normalize("NFKD", title)
    title = re.sub(r'[\s\-–]*\(?(?:new|ᴺᵉʷ|NEW)\)?$', '', title, flags=re.IGNORECASE)
    title = title.replace("\n", " ").strip()
    title = re.sub(r'\s{2,}', ' ', title)
    return title

# --------------------------
# Lookups
# --------------------------
def lookup_tvmaze_single(title: str) -> str | None:
    url = f"https://api.tvmaze.com/singlesearch/shows?q={requests.utils.quote(title)}"
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        data = r.json()
        img = data.get("image") or {}
        return img.get("original") or img.get("medium")
    return None

def refresh_jellyfin():
    """Trigger Jellyfin Live TV guide refresh."""
    try:
        url = f"{JELLYFIN_URL}/LiveTv/Guide/Refresh"
        headers = {"X-Emby-Token": JELLYFIN_APIKEY}
        r = requests.post(url, headers=headers, timeout=10)
        if r.status_code in (200, 204):
            log.info("🔄 Jellyfin guide refresh triggered successfully.")
        else:
            log.warning(f"Jellyfin refresh returned {r.status_code}: {r.text}")
    except Exception as e:
        log.warning(f"Could not trigger Jellyfin refresh: {e}")

# --------------------------
# Main
# --------------------------
def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    source_path = OUTPUT if OUTPUT.exists() else INPUT
    log.info(f"Loading XML from {source_path}")

    tree = ET.parse(source_path)
    root = tree.getroot()
    programmes = root.findall("programme")
    total = len(programmes)
    log.info(f"Found {total} programme entries.")

    cache = load_cache()
    added_new = added_cache = processed = 0
    started = time.time()

    for i, prog in enumerate(programmes, start=1):
        title_el = prog.find("title")
        if title_el is None:
            continue

        title_raw = (title_el.text or "").strip()
        if not title_raw:
            continue

        title = normalize_title(title_raw)

        # Skip if already has <icon>
        if prog.find("icon") is not None:
            continue

        # --- Cache lookup ---
        if title in cache:
            url = cache[title]
            if url:
                ET.SubElement(prog, "icon", {"src": url})
                added_cache += 1
                log.info(f"[{i}/{total}] (cache) {title}")
        else:
            # --- New lookup ---
            poster_url = lookup_tvmaze_single(title)
            cache[title] = poster_url
            if poster_url:
                ET.SubElement(prog, "icon", {"src": poster_url})
                added_new += 1
                log.info(f"[{i}/{total}] (new) {title}")
            else:
                log.debug(f"[{i}/{total}] No poster found for {title}")
            time.sleep(0.2)

        # --- Generic posters by category (lookup-first mode) ---
        if prog.find("icon") is None:
            categories = [(c.text or "").strip().lower() for c in prog.findall("category") if c.text]
            generic_match = next((GENERIC_CATEGORY_MAP.get(c) for c in categories if c in GENERIC_CATEGORY_MAP), None)
            if generic_match:
                ET.SubElement(prog, "icon", {"src": str(ASSETS / generic_match)})
                log.info(f"[{i}/{total}] Added generic poster for {title} ({categories})")

        # --- Fallback generic unknown ---
        if prog.find("icon") is None:
            ET.SubElement(prog, "icon", {"src": str(ASSETS / GENERIC_UNKNOWN)})
            log.info(f"[{i}/{total}] Added generic 'unknown' poster for {title}")

        processed += 1
        if processed % BATCH_SIZE == 0:
            save_cache(cache)
            tree.write(OUTPUT, encoding="utf-8", xml_declaration=True, method="xml")
            elapsed = time.time() - started
            pct = (processed / total) * 100
            if SHOW_PROGRESS_ETA and elapsed > 0:
                ips = processed / elapsed
                eta = (total - processed) / ips if ips > 0 else 0
                log.info(f"💾 Checkpoint: {processed}/{total} ({pct:.1f}%) | ETA ~ {eta/60:.1f} min")
            else:
                log.info(f"💾 Checkpoint: {processed}/{total} ({pct:.1f}%)")

    # --- Final write ---
    save_cache(cache)
    tree.write(OUTPUT, encoding="utf-8", xml_declaration=True, method="xml")
    elapsed = time.time() - started
    log.info(f"✅ Done in {elapsed/60:.1f} min — posters added: {added_new} new, {added_cache} cached.")
    refresh_jellyfin()

if __name__ == "__main__":
    main()
