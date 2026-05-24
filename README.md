# 📺 FauxCable

A self-hosted EPG enrichment tool. Pulls your XMLTV guide data from [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr), fetches poster art from TVMaze and TMDB, and serves the enriched feed at `/epg.xml` — ready for Jellyfin to consume directly.

Replaces the plain channel icons in your guide with proper show/movie poster art, with a web UI to manage matches, fix misidentified shows, and build generic fallback posters for categories like news, kids, and sports.

---

## Features

- **Auto-enrichment** — matches EPG programme titles to TVMaze (TV shows) and TMDB (movies), fetches poster URLs
- **Scheduled pipeline** — runs on a configurable interval; Jellyfin refresh triggered automatically after each run
- **Web UI** — dashboard, match queue, poster library, settings, generic builder
- **Manual match queue** — review items that got a generic fallback, search TVMaze/TMDB, pin the correct poster
- **Category filtering** — bulk-skip or bulk-fix by EPG category (news, kids, sports, etc.)
- **Poster library** — browse all cached posters, re-match any item, assign a generic
- **Generic poster builder** — create fallback posters with Pillow: custom background, blur, text overlay, font upload
- **SQLite cache** — fast restarts, no re-fetching already matched shows
- **Docker-ready** — single container, three volume mounts

---

## Stack

- [FastAPI](https://fastapi.tiangolo.com/) + [Starlette](https://www.starlette.io/) — web server
- [HTMX](https://htmx.org/) + [Tailwind CSS](https://tailwindcss.com/) — UI (no build step)
- [Pillow](https://python-pillow.org/) — generic poster rendering
- [aiosqlite](https://github.com/omnilib/aiosqlite) — async SQLite
- [APScheduler](https://apscheduler.readthedocs.io/) — scheduled pipeline runs
- [aiohttp](https://docs.aiohttp.org/) — async HTTP for EPG fetch and API calls

---

## Quick Start

### Unraid / Docker Compose Manager

The easiest deployment — no files to copy, no SSH required. Paste the compose below into your Compose Manager, fill in the five environment variables, and deploy.

```yaml
services:
  fauxcable:
    build: https://github.com/thelazurus/fauxcable.git
    ports:
      - "8000:8000"
    environment:
      # --- Required ---
      - DISPATCHARR_EPG_URL=http://your-dispatcharr-host/output/epg
      - FAUXCABLE_BASE_URL=http://your-server-ip:8000
      - JELLYFIN_URL=http://your-jellyfin-host:8096
      - JELLYFIN_API_KEY=your-jellyfin-api-key
      - TMDB_API_KEY=your-tmdb-api-key
      # --- Optional ---
      # - SCHEDULE_INTERVAL_HOURS=6
      # - CONCURRENCY=10
    volumes:
      - ./data:/app/data
      - ./generics:/app/generics
      - ./fonts:/app/fonts
    restart: unless-stopped
```

### Standard Docker

```bash
# Download just the compose file
curl -O https://raw.githubusercontent.com/thelazurus/fauxcable/main/docker-compose.yml

# Edit the environment variables
nano docker-compose.yml

# Build and run
docker compose up -d --build
```

### After starting

1. Open `http://your-server-ip:8000`
2. Hit **Run Now** on the dashboard to kick off the first pipeline
3. In Jellyfin → Dashboard → Live TV → EPG sources, replace your Dispatcharr EPG URL with:
   ```
   http://your-server-ip:8000/epg.xml
   ```

---

## Configuration

All configuration is set via environment variables in your compose file. No config file required.

| Variable | Required | Description |
|----------|----------|-------------|
| `DISPATCHARR_EPG_URL` | ✅ | Your Dispatcharr EPG output URL |
| `FAUXCABLE_BASE_URL` | ✅ | FauxCable's externally-reachable address — **must be reachable by Jellyfin**, not `localhost` |
| `JELLYFIN_URL` | ✅ | Jellyfin internal URL |
| `JELLYFIN_API_KEY` | ✅ | Jellyfin API key (Dashboard → API Keys) |
| `TMDB_API_KEY` | ✅ | TMDB API key — [get one free](https://www.themoviedb.org/settings/api) |
| `SCHEDULE_INTERVAL_HOURS` | — | How often to re-run enrichment (default: `6`) |
| `CONCURRENCY` | — | Parallel poster lookups per batch (default: `10`) |

### Config priority

Settings are applied in this order, with later sources winning:

1. **Environment variables** — your compose file (primary)
2. **Settings page** — changes saved in the UI are written to `data/config.yaml` inside the persistent data volume and take precedence over env vars on next restart

This means you can set baseline config in your compose file and fine-tune anything from the Settings page without touching the compose file again.

---

## Volume Mounts

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./data/` | `/app/data/` | SQLite database, enriched EPG XML, and Settings page overrides |
| `./generics/` | `/app/generics/` | Generic fallback poster PNGs |
| `./fonts/` | `/app/fonts/` | Uploaded TTF/OTF fonts for the poster builder |

---

## Generic Posters

When FauxCable can't match a programme to TVMaze or TMDB, it falls back to a category-based generic poster (e.g. `generic_news.png` for news programmes).

Build your generic posters in the **Generics** section of the UI — choose a background colour or image, blur radius, text label, and font, preview with Pillow, and save directly to the `generics/` folder.

Suggested starter set: `kids`, `news`, `series`, `infomercial`, `unknown`, `sports`, `talk`, `reality`, `documentary`, `movie`.

Filenames must follow the pattern `generic_{category}.png` where `{category}` matches EPG category names (lowercase).

---

## How It Works

1. Fetches XMLTV from Dispatcharr over HTTP
2. Bulk-loads the poster cache and manual overrides from SQLite into memory
3. Processes all `<programme>` elements in parallel batches:
   - **Override** → uses your manually pinned poster
   - **Cache hit** → uses previously fetched poster
   - **TVMaze/TMDB lookup** → fetches, caches, uses
   - **Generic fallback** → uses `generic_{category}.png`, queues for manual review
4. Writes enriched XMLTV to `data/enriched.xml`
5. Triggers Jellyfin metadata refresh
6. Updates the match queue in the UI

---

## Ports

| Port | Use |
|------|-----|
| `8000` | Web UI + `/epg.xml` endpoint |

---

## License

MIT
