# Pulse Markets

A compact FastAPI + React stock-watchlist demo. The backend simulates price movement on every poll so it works locally without an API key.

For the current architecture, feature status, and next work, see [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## Run

```powershell
cd stock-dashboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8081 --reload
```

Open http://127.0.0.1:8081.

## Database

All identity, role, session, portfolio, holding, trade-history, and private-API-registry data is persisted in PostgreSQL. By default the app connects to:

```text
postgresql://master%40pulse.local:MasterPulse!2026@127.0.0.1:5432/pulsedb
```

The dashboard runs directly on Windows, so it reaches the PostgreSQL Podman container through its published host port. If the default host port changes, override `DATABASE_URL`, for example:

```powershell
$env:DATABASE_URL = "postgresql://master%40pulse.local:MasterPulse!2026@127.0.0.1:5432/pulsedb"
uvicorn app.main:app --host 127.0.0.1 --port 8081 --reload
```

For production data, replace `tick_prices()` in `app/main.py` with a licensed provider integration (for example, Finnhub, Polygon, or Alpha Vantage) and keep the frontend `/api/quotes` contract unchanged.

## Access APIs

The demo seeds these local-only accounts on startup:

| Role | Username | Password |
| --- | --- | --- |
| Master (all access) | `master@pulse.local` | `MasterPulse!2026` |
| Admin | `admin@pulse.local` | `ChangeMe123!` |

First call `POST /api/auth/login` with that JSON body and pass its `access_token` as `Authorization: Bearer <token>` for protected endpoints.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login` | Authenticate and receive a bearer token. |
| `POST /api/auth/register` | Create a customer account; the role is always `customer`. |
| `GET /api/portfolio` | Read the signed-in user's paper cash and holdings. |
| `POST /api/orders` | Place a bearer-authenticated paper order: `{"side":"buy","symbol":"AAPL","quantity":2}`. |
| `GET /api/transactions` | Read the signed-in user's buy/sell history. |
| `GET /api/assistant/conversations` | Read the signed-in user's saved chat summaries. |
| `GET /api/assistant/history?conversation_id=1` | Read one of the signed-in user's saved chats. |
| `POST /api/assistant/chat` | Ask the read-only portfolio assistant: `{"message":"What is my largest exposure?"}`. |
| `POST /api/assistant/proposals` | Ask the local agent to analyze and create one pending paper-trade proposal. |
| `POST /api/assistant/proposals/confirm` | Confirm a pending proposal with `{"proposal_id":"..."}`; only this creates a paper order. |
| `POST /api/assistant/proposals/{proposal_id}/cancel` | Cancel a pending proposal without placing an order. |

The portfolio assistant uses local Ollama `gpt-oss:20b` by default at `http://127.0.0.1:11434`. Override with `OLLAMA_MODEL` or `OLLAMA_CHAT_URL` if needed. It sends only the signed-in user's portfolio context to the local model and never grants it order access. Trade proposals are stored as pending for 10 minutes; they can only become paper orders after the signed-in user presses **Confirm paper order**. The confirmation route rechecks proposal ownership, expiry, cash, and shares before filling at the current simulated quote.
| `DELETE /api/users/{user_id}` | Delete a user; only `master@pulse.local` may call it. The protected master account cannot be edited or deleted. |
| `POST /api/users` | Create a user: `{"email":"analyst@example.com","password":"SecurePass1!","roles":["analyst"]}`. |
| `POST /api/roles` | Create a role: `{"name":"support","permissions":["quotes:read"]}`. |
| `PUT /api/users/{user_id}/roles` | Replace a user's roles: `{"roles":["viewer"]}`. |
| `PUT /api/roles/{role_name}/permissions` | Replace a role's permissions: `{"permissions":["quotes:read"]}`. |
| `GET /api/admin/overview` | Master-only user, role, and permission overview. |
| `GET /api/admin/users` | Admin/master DataTables server-side source for user search, sorting, and pagination. |

For deployment, keep the connection string in a secret manager, add token expiry, use HTTPS, and use a proven password/authentication library.

## Read-only MCP server

`app/mcp_server.py` exposes five stdio tools to Codex: `get_portfolio`, `get_quotes`, `get_transactions`, `list_conversations`, and `get_conversation_history`. It connects to PostgreSQL directly but only reads the authenticated user's rows. It has no order tool and never calls Ollama.

First sign in through `POST /api/auth/login`, then place its `access_token` in your Codex MCP configuration (for example, `C:\Users\AMD\.codex\config.toml`):

```toml
[mcp_servers.pulse_markets]
command = "C:\\Users\\AMD\\OneDrive\\Desktop\\test\\MCP\\stock-dashboard\\.venv\\Scripts\\python.exe"
args = ["-m", "app.mcp_server"]
cwd = "C:\\Users\\AMD\\OneDrive\\Desktop\\test\\MCP\\stock-dashboard"
env = { PULSE_MCP_TOKEN = "SCEU6PMQwLKiZ3fIWxzt2u5azUoMM0pLeljUV6nm1Mw" }
```

Restart Codex after saving the configuration. Then ask, for example: `Use Pulse Markets to show my portfolio and my last five paper trades.` The token scopes every tool call to that one dashboard user; obtain a fresh token after deleting the session or changing environments.

## Playwright end-to-end tests

The browser suite covers customer sign-in/routing, paper buy/sell and history, saved Assistant chats/sidebar restoration, and master-account deletion protection. It starts an isolated dashboard instance on port `8081` and creates timestamped test customers. The test server forces the Assistant's deterministic fallback, so chat-persistence tests do not depend on Ollama availability. React is bundled under `web/vendor/`, so the core UI and browser tests do not rely on `esm.sh`.

```powershell
npm install
npx playwright install chromium
npm run test:e2e
```
