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

## Quick Start (Docker)

**1. Clone and configure**
```bash
git clone https://github.com/thelazurus/fauxcable.git
cd fauxcable
cp config.example.yaml config.yaml
nano config.yaml   # fill in your values — see Configuration below
```

**2. Start**
```bash
docker compose up -d --build
```

**3. Open the UI**
```
http://your-server-ip:8000
```

**4. Point Jellyfin at FauxCable**

In Jellyfin → Dashboard → Live TV → EPG sources, replace your Dispatcharr EPG URL with:
```
http://your-server-ip:8000/epg.xml
```

FauxCable will run its first pipeline automatically based on your schedule, or hit **Run Now** on the dashboard.

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and fill in:

| Key | Description |
|-----|-------------|
| `dispatcharr_epg_url` | Your Dispatcharr EPG output URL |
| `base_url` | FauxCable's externally-reachable address — **must be reachable by Jellyfin**, not `localhost` |
| `jellyfin.url` | Jellyfin internal URL |
| `jellyfin.api_key` | Jellyfin API key (Dashboard → API Keys) |
| `tmdb.api_key` | TMDB API key ([get one free](https://www.themoviedb.org/settings/api)) |
| `behavior.schedule_interval_hours` | How often to re-run enrichment (default: 6) |

All settings are also editable via the Settings page in the UI.

---

## Volume Mounts

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./config.yaml` | `/app/config.yaml` | Configuration |
| `./data/` | `/app/data/` | SQLite database + enriched EPG XML |
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
