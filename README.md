# FauxCable
**Smart EPG Poster Injector for IPTV / Jellyfin / Emby**

FauxCable fills in missing artwork for Live TV guide data by combining
**Schedules Direct XMLTV**, **TVMaze metadata**, and **category-based fallback posters**.
It works as a post-processor — drop in an XMLTV file, and it outputs a new one with `<icon>` entries.

---

## ✨ Features
- ✅ Adds high-quality poster art from **TVMaze**
- ✅ Caches lookups for repeat shows (restart-safe)
- ✅ Generates generic category artwork for filler content (news, infomercials, etc.)
- ✅ Automatically triggers a **Jellyfin Live TV guide refresh**
- ✅ YAML configuration
- ✅ Detailed progress logging + ETA
- ✅ Unicode-safe title normalization

---

## 🧱 Installation

```bash
git clone https://github.com/<yourname>/FauxCable.git
cd FauxCable
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
