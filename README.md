# Do.iT — Digital To-do List

Flask + PostgreSQL (SQLite for quick local tests). Classic navy-and-white interface that adapts from small phones to wide desktops.

## Features
- **Tasks**: quick add with plain text (`Call Ada tomorrow 5pm !high #family`), priorities, tags, notes, checklists, repeating tasks, smart views, search, calendar view, undo
- **Weekly timetable**: editable table (Mon–Sun), table and day views, colours, print; reminders **30 min before, 5 min before and at the start time**
- **Diary**: ruled-paper notes with autosave, mood, pinning, search
- **Voice typing** in the diary and quick-add (speak, and it types; say “full stop”, “comma”, “new line”)
- **AI assistant** (plan my day, break a goal into a checklist, tidy/summarise/reflect on diary entries) using Google Gemini (free) or Anthropic Claude
- **Account recovery**: forgot username/password by email, email confirmation
- **Automatic email reminders** for tasks and timetable, plus an optional morning summary
- **Alarms** that ring (sound, vibration, full-screen alert, notifications), focus timer, streaks and badges
- **SEO**: landing page, meta tags, Open Graph, JSON-LD, robots.txt, sitemap.xml; installable on phones

## Run locally
```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then edit .env (see below)
flask --app server db upgrade     # creates / upgrades the database tables
flask --app server run --debug
```
Open http://127.0.0.1:5000

## Database
By default the app uses a local SQLite file. To use **PostgreSQL**, put its address in `.env`:
```
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```
Then run `flask --app server db upgrade` once. To copy old SQLite data across (optional):
`python scripts/sqlite_to_postgres.py todo.db`

## Environment variables
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (leave empty for SQLite) |
| `SECRET_KEY` | Long random string for sessions |
| `SITE_URL` | Public address, used in SEO tags and the sitemap |
| `COOKIE_SECURE=1` | HTTPS-only cookies (set in production) |
| `MAIL_FROM`, `BREVO_API_KEY` | Sender address and Brevo key for emails (free: 300/day) |
| `CRON_SECRET` | Secret that protects `/api/cron/reminders?key=...` (call it every 5 minutes) |
| `DEFAULT_TZ`, `DIGEST_HOUR` | Fallback timezone and hour of the morning summary |
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | Turns on the AI assistant (`AI_MODEL` optional) |

## Deploy (Render + Neon, both free)
Build command: `bash build.sh` · Start command: `gunicorn server:app` · Health check path: `/healthz`

## Layout
```
server.py            models, JSON API, auth, SEO routes
migrations/          Alembic migrations (commit these)
scripts/             sqlite_to_postgres.py
templates/           base, landing, login, sign-up, dashboard (home.html), 404
static/css/app.css   theme   ·   static/js/app.js   app logic   ·   static/sw.js   notifications
```
