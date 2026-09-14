# Pulse Markets

Pulse Markets is a local paper-trading dashboard built with FastAPI, React, PostgreSQL, and an optional Ollama assistant. It includes customer authentication, role-based administration, simulated stock quotes, portfolios, trade history, persistent assistant conversations, and confirmation-gated AI trade proposals.

> This is an educational paper-trading project. It does not place real orders or provide financial advice.

## Features

- Customer registration and bearer-token sign-in
- Role-based access control for customers, analysts, admins, and the protected master account
- Simulated quotes, paper portfolios, buy/sell orders, and transaction history
- Local Ollama portfolio analysis with persistent conversations
- AI trade proposals that require explicit user confirmation before a paper order is created
- Authenticated, read-only MCP tools for portfolio and conversation data
- Playwright coverage for the main customer and administration flows
- Docker Compose stack for the app, PostgreSQL, and Ollama

## Architecture

```text
Browser ──HTTP──> FastAPI ─────> PostgreSQL
                    │
                    └──────────> Ollama

MCP client ──stdio──> app.mcp_server ──> PostgreSQL
```

The React application is served as static files by FastAPI. PostgreSQL stores accounts, roles, sessions, portfolios, trades, proposals, and assistant conversations. Ollama is local and receives only the signed-in user's supplied portfolio context. The MCP server is a separate read-only stdio process; it does not expose another HTTP port.

## Quick start with Docker Compose

### Requirements

- Docker Desktop with Docker Compose, or a compatible Podman Compose setup
- PowerShell for the included secret-generation helper
- Enough memory and disk space for the Ollama model if AI features are enabled

### 1. Generate local secrets

```powershell
.\scripts\init-secrets.ps1
```

This creates cryptographically random database and master-account secret files under `secrets/`. Their values are never printed, and the directory is ignored by Git.

### 2. Start the stack

```powershell
docker compose up --build -d
docker compose ps
```

Open <http://127.0.0.1:8081>. The default master email is `master@pulse.local`; its generated password is stored only in `secrets/master_password.txt`.

### 3. Install the local AI model

The dashboard works without a downloaded model by returning a limited fallback response. Install the configured model to enable full assistant replies and trade proposals:

```powershell
docker compose exec ollama ollama pull gpt-oss:20b
```

### Common operations

```powershell
# Follow application logs
docker compose logs -f app

# Rebuild after source changes
docker compose up --build -d app

# Stop containers but keep PostgreSQL and Ollama data
docker compose down
```

`docker compose down -v` also deletes the named database and model volumes. Use it only when you intentionally want to erase local data.

## Containers

| Service | Purpose | Host access |
| --- | --- | --- |
| `app` | FastAPI API and React UI | `127.0.0.1:8081` |
| `db` | PostgreSQL 16 persistent data store | `127.0.0.1:5432` |
| `ollama` | Local model runtime | `127.0.0.1:11434` |

The app image runs as a non-root user with a read-only root filesystem. Only temporary files under `/tmp` are writable. PostgreSQL and Ollama state use named volumes.

## Configuration

Compose defaults can be overridden in your shell before startup. Do not commit secret values or `.env` files.

| Variable | Default | Description |
| --- | --- | --- |
| `APP_PORT` | `8081` | Dashboard host port |
| `POSTGRES_PORT` | `5432` | PostgreSQL host port |
| `OLLAMA_PORT` | `11434` | Ollama host port |
| `POSTGRES_DB` | `pulsedb` | Database name |
| `POSTGRES_USER` | `pulse` | Database user |
| `PULSE_MASTER_EMAIL` | `master@pulse.local` | Protected bootstrap account email |
| `OLLAMA_MODEL` | `gpt-oss:20b` | Local assistant model |
| `OLLAMA_TIMEOUT_SECONDS` | `90` | Ollama request timeout |

Secret-bearing settings support file-based injection:

| Variable | Purpose |
| --- | --- |
| `DATABASE_PASSWORD_FILE` | File containing the PostgreSQL password used by the app |
| `PULSE_MASTER_PASSWORD_FILE` | File containing the bootstrap master password |
| `PULSE_ADMIN_PASSWORD_FILE` | Optional file containing a bootstrap admin password |
| `PULSE_MCP_TOKEN` | Short-lived dashboard bearer token used by the MCP process |

Direct environment forms (`DATABASE_PASSWORD`, `PULSE_MASTER_PASSWORD`, and `PULSE_ADMIN_PASSWORD`) are supported for development, but mounted secret files are preferred. Set `PULSE_ADMIN_EMAIL` together with an admin password only if a bootstrap admin is required. `PULSE_RESET_BOOTSTRAP_PASSWORDS=true` intentionally updates existing bootstrap-account hashes and should not normally be enabled.

## Run locally without the app container

Start the Compose database first and reuse its secret file:

```powershell
.\scripts\init-secrets.ps1
docker compose up -d db ollama

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:PGHOST = "127.0.0.1"
$env:PGPORT = "5432"
$env:PGDATABASE = "pulsedb"
$env:PGUSER = "pulse"
$env:DATABASE_PASSWORD_FILE = (Resolve-Path ".\secrets\postgres_password.txt")
$env:PULSE_MASTER_PASSWORD_FILE = (Resolve-Path ".\secrets\master_password.txt")
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081 --reload
```

The Compose Ollama service and a native Ollama installation both use `127.0.0.1:11434` by default, so the host-run application works without another setting.

## API overview

FastAPI's interactive schema is available at <http://127.0.0.1:8081/docs>.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/register` | Register a customer account |
| `POST /api/auth/login` | Sign in and receive a bearer token |
| `GET /api/quotes` | Read the simulated watchlist |
| `GET /api/portfolio` | Read the signed-in user's paper portfolio |
| `POST /api/orders` | Place a signed-in user's paper buy/sell order |
| `GET /api/transactions` | Read paper-trade history |
| `GET /api/assistant/conversations` | List saved assistant conversations |
| `GET /api/assistant/history` | Read one owned conversation |
| `POST /api/assistant/chat` | Ask the local portfolio assistant |
| `POST /api/assistant/proposals` | Create a pending AI paper-trade proposal |
| `POST /api/assistant/proposals/confirm` | Explicitly confirm and execute a stored paper proposal |
| `POST /api/assistant/proposals/{id}/cancel` | Cancel a pending proposal |
| `GET /api/admin/overview` | Read the administration overview |

Protected endpoints require `Authorization: Bearer <token>`. See [PRIVATE_API_REGISTRY.md](PRIVATE_API_REGISTRY.md) for the permission registry.

## Read-only MCP server

`app/mcp_server.py` exposes `get_portfolio`, `get_quotes`, `get_transactions`, `list_conversations`, and `get_conversation_history`. It never exposes an order-execution tool.

Run it from an MCP client as a local stdio command:

```toml
[mcp_servers.pulse_markets]
command = "C:\\absolute\\path\\to\\stock-dashboard\\.venv\\Scripts\\python.exe"
args = ["-m", "app.mcp_server"]
cwd = "C:\\absolute\\path\\to\\stock-dashboard"

[mcp_servers.pulse_markets.env]
PULSE_MCP_TOKEN = "<fresh-token-from-login>"
PGHOST = "127.0.0.1"
PGPORT = "5432"
PGDATABASE = "pulsedb"
PGUSER = "pulse"
DATABASE_PASSWORD_FILE = "C:\\absolute\\path\\to\\stock-dashboard\\secrets\\postgres_password.txt"
```

Keep the real token in the MCP client's private configuration, never in this repository. Restart the client after changing its MCP configuration.

## End-to-end tests

The browser suite covers registration, customer routing, paper buy/sell history, assistant persistence, and master-account protection. It generates temporary test credentials at runtime instead of storing passwords in source.

```powershell
npm install
npx playwright install chromium
npm run test:e2e
```

The test runner uses port `8082` and deliberately disables Ollama calls for deterministic assistant fallback behavior. Point it only at disposable test data because it creates users, sessions, conversations, and trades.

## Security notes

- No passwords, database connection strings, or bearer tokens should be committed.
- Secret files under `secrets/` and local `.env` files are ignored by Git.
- Tokens are currently stored in PostgreSQL and browser local storage; add expiry, revocation, HTTPS, and hardened cookie/token handling before any internet-facing deployment.
- Quote data is simulated. Replace the quote simulation in `app/main.py` with a licensed market-data provider before using real data.
- Trade execution is paper-only. AI proposals remain pending until the signed-in owner explicitly confirms them.

For implementation history and current design notes, see [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).
