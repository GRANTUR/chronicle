# chronicle

Unified calendar assistant: syncs Google Calendar + Outlook, surfaces the day ahead on Discord, and runs short LLM analyses over the combined schedule.

Built to solve a personal problem — my work calendar (Outlook) and personal calendar (Google) never talked to each other, and neither one alerted me to double-bookings or back-to-back meetings I'd regret the next morning.

## What it does

- **Polls** Google Calendar and Outlook on a schedule (APScheduler).
- **Stores** events in a local SQLite DB (`models.py` — SQLAlchemy).
- **Posts** a morning briefing to Discord at 7 AM ET with the day's events, flagged conflicts, and an LLM-generated "what to pay attention to" note.
- **Responds** to Discord slash commands for ad-hoc queries ("what's on for Thursday?", "reschedule standup to 10am").

## Stack

| Layer | Tool |
|---|---|
| API | FastAPI + uvicorn |
| Scheduler | APScheduler (in-process async) |
| Calendar APIs | google-api-python-client, msal (Outlook) |
| Discord | discord.py |
| LLM | Anthropic Claude via `anthropic` SDK, with fallback to a local Ollama endpoint reachable from the cluster |
| Storage | SQLite |
| Deploy | Kubernetes (see `k8s/`), sealed-secret for OAuth + Discord credentials |

## Deployment

Runs in the `ecosystem` namespace of my homelab k3s cluster. See [GRANTUR/homelab](https://github.com/GRANTUR/homelab) for the cluster itself. The manifests in `k8s/` are:

- `chronicle.yml` — Deployment, PVC for the SQLite DB, Service.
- `traefik-chronicle.yml` — IngressRoute exposing the FastAPI endpoints for OAuth callbacks.

LLM calls to the self-hosted Ollama instance route through a socat relay from the cluster to a Windows workstation on the Tailscale mesh — the infrastructure pattern is documented in the homelab repo.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Provide config via env vars — see config.py for the full list:
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, OUTLOOK_CLIENT_ID, OUTLOOK_TENANT_ID,
#   DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, ANTHROPIC_API_KEY, OLLAMA_URL
export $(cat .env | xargs)

uvicorn app:app --reload --port 8000
```

First run opens the Google OAuth consent flow in a browser; token refresh is handled thereafter.

## Status

Actively deployed. Uptime over 22 days on my homelab at time of writing. Morning briefing has caught two schedule conflicts that I would have missed otherwise, which is the bar.
