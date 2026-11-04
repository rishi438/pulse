"""Read-only Pulse Markets MCP Server.

Provides authenticated, multi-user tools for Model Context Protocol (MCP) clients
to inspect simulated stock market quotes, paper portfolios, trade transactions,
and assistant conversation history from Pulse Markets.

Architecture:
    In production (Streamable HTTP):
        Requests carry an 'Authorization: Bearer <user_token>' HTTP header.
        The ASGI authentication middleware validates the token against PostgreSQL
        user sessions and sets a request-scoped context variable, guaranteeing
        isolated, per-user data access across concurrent multi-user requests.

    In local development (stdio transport):
        Falls back to PULSE_MCP_TOKEN environment variable for single-developer workflows.

Environment Variables:
    PULSE_MCP_TOKEN: (Optional fallback for stdio) Dashboard bearer token.
    PGHOST: PostgreSQL server host (default: 127.0.0.1).
    PGPORT: PostgreSQL server port (default: 5432).
    PGDATABASE: PostgreSQL database name (default: pulsedb).
    PGUSER: PostgreSQL database user (default: pulse).
    DATABASE_PASSWORD / DATABASE_PASSWORD_FILE: PostgreSQL credentials.
"""
from __future__ import annotations

import contextvars
import os
from datetime import datetime
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.fastmcp import FastMCP
from .main import db_connection, require_user, portfolio, quotes

# Context variable ensuring multi-user, per-request isolation in HTTP mode
current_user_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "current_user", default=None
)

mcp = FastMCP("Pulse Markets Read-Only", stateless_http=True)
mcp.settings.streamable_http_path = "/"
_mcp_asgi_app: ASGIApp | None = None


def user() -> dict[str, Any]:
    """Retrieve and validate the authenticated user for the active context.

    In multi-user HTTP mode:
        Retrieves the authenticated user set by the Bearer token middleware.

    In single-user stdio mode:
        Reads PULSE_MCP_TOKEN from the environment and validates it against PostgreSQL.

    Returns:
        dict[str, Any]: Authenticated user record with keys 'id', 'email', and 'roles'.

    Raises:
        ValueError: If neither a request context user nor PULSE_MCP_TOKEN is found.
        HTTPException: If the token is invalid or expired.
    """
    ctx_user = current_user_var.get()
    if ctx_user is not None:
        return ctx_user

    token = os.getenv("PULSE_MCP_TOKEN", "")
    if not token:
        raise ValueError(
            "Authentication required: No Bearer token in request context "
            "and PULSE_MCP_TOKEN environment variable is not set."
        )
    return require_user(f"Bearer {token}")


def iso(value: datetime | Any) -> str | Any:
    """Convert a datetime value to an ISO 8601 string format.

    Args:
        value (datetime | Any): An object to convert, usually a datetime instance.

    Returns:
        str | Any: ISO 8601 formatted string if value is a datetime, else the value as-is.
    """
    return value.isoformat() if isinstance(value, datetime) else value


@mcp.tool()
def get_portfolio() -> dict[str, Any]:
    """Read the authenticated user's paper trading portfolio.

    Retrieves current available cash balance, total portfolio market value,
    aggregated holdings value, and details of each stock position including
    shares held, average cost basis, current market price, and market value.

    Returns:
        dict[str, Any]: Portfolio summary containing:
            - cash (float): Available cash balance.
            - holdings_value (float): Total market value of all held stock shares.
            - total_value (float): Sum of cash and holdings market value.
            - holdings (list[dict]): List of positions with symbol, name,
              shares, average_cost, current_price, and market_value.
    """
    u = user()
    with db_connection() as db:
        return portfolio(db, u["id"])


@mcp.tool()
def get_quotes() -> dict[str, list[dict[str, Any]]]:
    """Read the simulated stock market watchlist and current quotes.

    Returns current simulated stock market quotes for active watchlist symbols
    (e.g., AAPL, MSFT, NVDA, TSLA, AMZN), including company name, latest price,
    and opening price. Requires a valid authenticated session.

    Returns:
        dict[str, list[dict[str, Any]]]: Dictionary containing:
            - quotes (list[dict]): List of quote objects with keys:
                - symbol (str): Ticker symbol.
                - name (str): Full company name.
                - price (float): Current trading price.
                - open (float): Opening price for the current session.
    """
    user()
    return {"quotes": [{"symbol": s, **q} for s, q in quotes.items()]}


@mcp.tool()
def get_transactions(limit: int = 20) -> dict[str, list[dict[str, Any]]]:
    """Read the most recent paper trading transactions for the authenticated user.

    Fetches historical executed buy and sell orders, sorted in reverse
    chronological order (newest first). The query limit is clamped between
    1 and 100 transactions.

    Args:
        limit (int, optional): Maximum number of transactions to retrieve.
            Clamped between 1 and 100. Defaults to 20.

    Returns:
        dict[str, list[dict[str, Any]]]: Dictionary containing:
            - transactions (list[dict]): List of transaction records with keys:
                - id (int): Unique transaction identifier.
                - side (str): Order side ('buy' or 'sell').
                - symbol (str): Ticker symbol.
                - quantity (int): Number of shares traded.
                - price (float): Execution price per share.
                - total (float): Total transaction value.
                - executed_at (str): ISO 8601 execution timestamp.
    """
    u = user()
    limit = max(1, min(limit, 100))
    with db_connection() as db:
        rows = db.execute(
            "SELECT id,side,symbol,quantity,price,total,executed_at FROM transactions WHERE user_id=%s ORDER BY executed_at DESC LIMIT %s",
            (u["id"], limit),
        ).fetchall()
    return {
        "transactions": [
            {
                **x,
                "price": float(x["price"]),
                "total": float(x["total"]),
                "executed_at": iso(x["executed_at"]),
            }
            for x in rows
        ]
    }


@mcp.tool()
def list_conversations() -> dict[str, list[dict[str, Any]]]:
    """List saved AI assistant conversation threads for the authenticated user.

    Returns summary metadata for all persistent chat conversations owned by
    the authenticated user, ordered from most recently updated to oldest.

    Returns:
        dict[str, list[dict[str, Any]]]: Dictionary containing:
            - conversations (list[dict]): List of conversation metadata objects:
                - id (int): Conversation thread identifier.
                - title (str): Conversation title or topic summary.
                - updated_at (str): ISO 8601 timestamp of the last message update.
    """
    u = user()
    with db_connection() as db:
        rows = db.execute(
            "SELECT id,title,updated_at FROM assistant_conversations WHERE user_id=%s ORDER BY updated_at DESC",
            (u["id"],),
        ).fetchall()
    return {"conversations": [{**x, "updated_at": iso(x["updated_at"])} for x in rows]}


@mcp.tool()
def get_conversation_history(conversation_id: int) -> dict[str, list[dict[str, Any]]]:
    """Read the full message transcript of a specific owned conversation thread.

    Retrieves all messages exchanged between the user and the AI assistant
    within the specified conversation, sorted in chronological order. Only
    conversations owned by the authenticated user can be accessed.

    Args:
        conversation_id (int): Unique identifier of the conversation to retrieve.

    Returns:
        dict[str, list[dict[str, Any]]]: Dictionary containing:
            - messages (list[dict]): Chronological list of chat messages:
                - role (str): Message author ('user' or 'assistant').
                - content (str): Message body text.
                - created_at (str): ISO 8601 timestamp when message was recorded.
    """
    u = user()
    with db_connection() as db:
        rows = db.execute(
            "SELECT role,content,created_at FROM assistant_messages WHERE conversation_id=%s AND user_id=%s ORDER BY id",
            (conversation_id, u["id"]),
        ).fetchall()
    return {"messages": [{**x, "created_at": iso(x["created_at"])} for x in rows]}


class MCPAuthMiddleware:
    """Production-grade ASGI middleware enforcing per-request Bearer token authentication."""

    def __init__(self, inner: ASGIApp):
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path in ("/mcp", "/mcp/"):
                scope["path"] = "/"
            elif path.startswith("/mcp/"):
                scope["path"] = path[4:]

            # Preflight requests pass through to CORS handlers
            if scope.get("method") == "OPTIONS":
                await self.inner(scope, receive, send)
                return

            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if not auth_header.startswith("Bearer "):
                response = JSONResponse(
                    {"error": "Unauthorized: Missing Bearer token in Authorization header"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="missing_token"'},
                )
                await response(scope, receive, send)
                return

            token = auth_header.removeprefix("Bearer ").strip()
            try:
                authenticated_user = require_user(f"Bearer {token}")
            except Exception:
                response = JSONResponse(
                    {"error": "Unauthorized: Invalid or expired Bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )
                await response(scope, receive, send)
                return

            reset_token = current_user_var.set(authenticated_user)
            try:
                await self.inner(scope, receive, send)
            finally:
                current_user_var.reset(reset_token)
            return

        await self.inner(scope, receive, send)


_raw_streamable_http_app = mcp.streamable_http_app


def _authenticated_streamable_http_app():
    global _mcp_asgi_app
    if _mcp_asgi_app is None:
        _mcp_asgi_app = MCPAuthMiddleware(_raw_streamable_http_app())
    return _mcp_asgi_app


mcp.streamable_http_app = _authenticated_streamable_http_app
mcp.settings.port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8085")))


def get_mcp_asgi_app() -> ASGIApp:
    """Return the authenticated Streamable HTTP MCP ASGI app."""
    return mcp.streamable_http_app()


def get_mcp_session_manager():
    """Return the session manager after ensuring the Streamable HTTP app is initialized."""
    get_mcp_asgi_app()
    return mcp.session_manager


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
