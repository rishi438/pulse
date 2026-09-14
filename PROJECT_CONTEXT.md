# Project context

## Purpose

Pulse Markets is a local paper-trading dashboard used to learn FastAPI, React, PostgreSQL, Ollama, MCP, and Playwright.

## Run locally

```powershell
cd C:\Users\AMD\OneDrive\Desktop\test\MCP\stock-dashboard
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8081 --reload
```

Open `http://127.0.0.1:8081`. PostgreSQL runs in Podman but is reached by the Windows backend at `127.0.0.1:5432`; database `pulsedb`.

## Main files

- `app/main.py` — FastAPI routes, RBAC, PostgreSQL schema/migrations, paper orders, and Ollama integration.
- `web/app.js` — React UI and API calls.
- `web/styles.css` — dashboard styling.
- `web/assistant.css` — Assistant conversation layout and sidebar styling.
- `web/index.html` — frontend asset loading; Assistant assets use a cache-version query string.

## Implemented features

- Bearer authentication and role-based permissions. Customers land on Dashboard, not Management.
- Master account `master@pulse.local` is permanently protected from edit/delete.
- Master/admin Management area has DataTables-backed user management, role editing, and protected APIs.
- PostgreSQL persists users, roles, sessions, portfolios, holdings, transactions, and assistant chats.
- Paper buy/sell screen, personal transaction history, authenticated quotes, and portfolio view.
- Local read-only portfolio Assistant uses Ollama `qwen3:14b` by default. It has no trading tool access.
- Playwright browser coverage verifies customer routing, paper buy/sell and history, saved Assistant chat restoration, and protected-master deletion rejection. Run `npm run test:e2e`.

## Assistant conversations

- A conversation belongs to exactly one user.
- `assistant_conversations` stores each chat title and timestamps.
- `assistant_messages` stores question/reply messages and references its conversation.
- `GET /api/assistant/conversations` returns the signed-in user's sidebar summaries.
- `GET /api/assistant/history?conversation_id=<id>` loads one owned conversation.
- `POST /api/assistant/chat` creates a chat when `conversation_id` is omitted, or appends to the owned chat when it is supplied.
- Legacy saved messages are migrated into `Previous conversation` on startup.

## Important troubleshooting

- If a new API route is missing, check `http://127.0.0.1:8081/openapi.json`; an older Uvicorn process may still be running.
- If the browser looks stale, open `http://127.0.0.1:8081/#assistant` after restarting with `--reload`. The Assistant assets use `?v=conversations-1` to defeat caching.
- Quotes require a logged-in Bearer token. A 401 without one is expected.

## Suggested next work

1. Add a confirmation-gated agent flow before allowing any future trade automation.
