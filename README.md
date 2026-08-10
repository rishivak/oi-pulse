# OI Pulse — Options Analytics Terminal

Real-time Open Interest analytics for NSE/BSE options using the official Upstox Developer API.

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.12+ |
| Node.js | 20+ |
| Podman Desktop | 5.x+ |
| WSL2 | Enabled |
| Upstox Developer Account | — |

---

## 1 — Register an Upstox App

1. Go to <https://developer.upstox.com> → **My Apps** → **Create App**.
2. Set **Redirect URI** to `http://localhost:8080/api/auth/callback` (local Podman dev).
3. Copy **Client ID** and **Client Secret**.

---

## 2 — Environment Setup

```bash
# From repo root
cp .env.example .env
```

Edit `.env` and fill in:

```
UPSTOX_CLIENT_ID=your_client_id
UPSTOX_CLIENT_SECRET=your_client_secret
UPSTOX_REDIRECT_URI=http://localhost:8080/api/auth/callback

# Generate a Fernet key:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=<generated>

# Generate a session secret:
# python -c "import secrets; print(secrets.token_hex(32))"
SESSION_SECRET_KEY=<generated>
```

---

## 3 — Start Podman

```bash
podman machine start
```

---

## 4 — Start Local Infrastructure With Podman

Start only Postgres and Redis:

```powershell
.\infra\scripts\podman-up.ps1 -InfraOnly
```

Start the full local stack under Podman:

```powershell
.\infra\scripts\podman-up.ps1 -Full
```

Preferred one-command startup for local development:

```powershell
.\scripts\start-local.ps1
```

This starts the full local stack in Podman, including the frontend, backend, worker, Postgres, Redis, and nginx.

This starts:

1. `oi-pulse-postgres`
2. `oi-pulse-redis`
3. `oi-pulse-api`
4. `oi-pulse-worker`
5. `oi-pulse-nginx`

The browser entrypoint is:

```text
http://localhost:8080
```

---

## 5 — Frontend Setup

The one-command Podman flow runs the frontend in a container and exposes it on port `3000`.

If you want to run the frontend directly on the host for UI iteration instead, use:

```bash
cd frontend
npm install

# Copy env
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local

node .\node_modules\next\dist\bin\next dev --hostname 0.0.0.0
```

Frontend dev server: <http://localhost:3000>

Open the app through the Podman nginx proxy: <http://localhost:8080>

---

## 6 — Optional Host-Run Backend Workflow

If you want to debug Python directly on the host, keep Postgres/Redis in Podman and run the backend locally:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# Run DB migrations
alembic upgrade head

# Start API server
python run_api.py
```

API: <http://localhost:8000/api/docs>

---

## 7 — Worker Setup

In a second terminal (same venv, same .env):

```bash
cd backend
python run_worker.py
```

---

## 8 — Connect Upstox

1. Open <http://localhost:8080/settings>.
2. Click **Re-connect Upstox Account** — this redirects to Upstox OAuth.
3. After approval, you're redirected back and a session cookie is set.

---

## 9 — Start the Collector

In the Settings page, choose **NIFTY**, **5m**, click **Start Collector**.

The worker will start collecting snapshots every 5 minutes during market hours (09:15–15:30 IST).

---

## 10 — Podman Operations

Stop the local stack:

```powershell
.\infra\scripts\podman-down.ps1
```

Check local app status:

```powershell
.\scripts\status-local.ps1
```

Reset local Podman data volumes and network:

```powershell
.\infra\scripts\podman-reset-data.ps1
```

Check running containers:

```powershell
podman ps
```

Tail logs:

```powershell
podman logs -f oi-pulse-api
podman logs -f oi-pulse-worker
```

If Podman is installed but not running:

```powershell
.\infra\scripts\podman-machine-up.ps1
```

---

## Project Structure

```
oi-pulse/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # FastAPI route handlers
│   │   ├── core/             # Config, security, deps
│   │   ├── db/               # SQLAlchemy models + engine
│   │   ├── integrations/     # Upstox REST client
│   │   └── services/         # Snapshot, analytics, event bus
│   ├── worker/               # APScheduler jobs
│   ├── alembic/              # DB migrations
│   ├── run_api.py            # API server entry
│   └── run_worker.py         # Worker entry
├── frontend/
│   ├── app/                  # Next.js App Router pages
│   ├── components/           # UI components
│   └── lib/                  # API client, SSE, types, utils
└── infra/
    ├── docker-compose.yml
    ├── podman-compose.yml
    ├── nginx/nginx.conf
    ├── nginx/nginx.podman.conf
    └── scripts/
```

---

## Architecture Summary

```
Browser ──HTTPS──► Next.js (Vercel)
                        │ /api/* rewrites (same-origin cookies)
                        ▼
               FastAPI Backend (Railway)
               ├── OAuth handler (Upstox OAuth 2.0)
               ├── OI data REST endpoints
               ├── SSE stream endpoint
               └── Collector control API
                        │
                   PostgreSQL (Supabase/Neon)
                        │
               Worker Process (Railway)
               ├── APScheduler (per-interval jobs)
               ├── Upstox Option Chain API fetcher
               ├── Idempotent snapshot writer
               └── Outbox event publisher
                        │
                    Redis (Upstash)
                    └── pub/sub → SSE → Browser
```

---

## Security Notes

- Upstox tokens are encrypted at rest using Fernet (AES-256 + HMAC).
- Session state lives server-side; the browser holds only an HMAC-signed opaque cookie.
- `TOKEN_ENCRYPTION_KEY` and `SESSION_SECRET_KEY` must never be committed to Git.
- Token values are never logged — check `audit_logs` for auth events.

---

## Podman Notes

- The local Podman stack is intentionally script-driven because `podman compose` on Windows may delegate to an external compose provider that is not fully Docker-free.
- The browser entrypoint is `http://localhost:8080` so OAuth callback URLs and cookies stay same-origin through nginx.
- The hardened Podman flow runs the frontend in a container so nginx can proxy internally without depending on Windows host networking quirks.
- Host-run frontend remains optional for direct UI work, but the Podman-backed app URL is [http://localhost:8080](http://localhost:8080).

---

## Development Roadmap

| Phase | Scope |
|-------|-------|
| ✅ Phase 1 | OAuth, 5m collector, OI snapshots, Dashboard, Trending OI, Option Chain |
| Phase 2 | Multi-interval UX, OI Heatmap, OI Charts, History replay |
| Phase 3 | OI velocity/acceleration, Alerts, Notifications |
| Phase 4 | Multi-user, advanced analytics, AI explanations |
