# Compider

Website crawler and monitor. Crawl your entire site, track every page's HTTP status, title, and description. Compare scans to detect changes over time.

**Live:** [compider.com](https://compider.com)

## Features

- **Full Site Crawl** — Async crawler discovers all internal links, records HTTP status codes, page titles, and meta descriptions
- **Broken Link Detection** — Find 404s and other errors with referrer info showing which page links to each broken URL
- **Change Tracking** — Compare any two scans side by side: new pages, removed pages, title changes, status code changes
- **Scheduled Scans** — Automatic periodic crawling on a configurable schedule
- **Live URL Check** — One-click re-check on removed URLs to see current status and redirects
- **CSV Export** — Export scan results and diff reports
- **Multi-tenant** — JWT authentication with per-user data isolation

## Tech Stack

- **Backend:** Python, FastAPI, aiohttp, BeautifulSoup
- **Database:** SQLite (aiosqlite)
- **Auth:** JWT (httponly cookies) + bcrypt
- **Scheduler:** APScheduler
- **Deployment:** Docker, Apache reverse proxy, Cloudflare

## Quick Start

```bash
# Clone
git clone https://github.com/wacode4/compider.git
cd compider

# Run with Docker
cp .env.example .env  # edit secrets
docker compose up -d --build

# Or run locally
pip install -r requirements.txt
python run.py
```

The app runs on `http://localhost:8000`.

## Environment Variables

| Variable               | Description                                                       | Default                            |
| ---------------------- | ----------------------------------------------------------------- | ---------------------------------- |
| `COMPIDER_SECRET`      | JWT signing secret                                                | `change-this-secret-in-production` |
| `COMPIDER_INVITE_CODE` | Required invite code for registration (empty = open registration) | _(empty)_                          |
| `COMPIDER_DB`          | SQLite database path                                              | `compider.db`                      |

## License

MIT
