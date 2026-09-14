"""Read-only Pulse Markets MCP server; it never sends orders or prompts to Ollama."""
import os
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from .main import db_connection, require_user, portfolio, quotes

mcp = FastMCP("Pulse Markets Read-Only")
def user():
    token=os.getenv("PULSE_MCP_TOKEN", "")
    if not token: raise ValueError("PULSE_MCP_TOKEN must contain a dashboard bearer token")
    return require_user(f"Bearer {token}")
def iso(value): return value.isoformat() if isinstance(value,datetime) else value
@mcp.tool()
def get_portfolio():
    """Read the authenticated user's paper portfolio."""
    u=user()
    with db_connection() as db:return portfolio(db,u["id"])
@mcp.tool()
def get_quotes():
    """Read the simulated watchlist."""
    user(); return {"quotes":[{"symbol":s,**q} for s,q in quotes.items()]}
@mcp.tool()
def get_transactions(limit:int=20):
    """Read newest paper trades."""
    u=user(); limit=max(1,min(limit,100))
    with db_connection() as db: rows=db.execute("SELECT id,side,symbol,quantity,price,total,executed_at FROM transactions WHERE user_id=%s ORDER BY executed_at DESC LIMIT %s",(u["id"],limit)).fetchall()
    return {"transactions":[{**x,"price":float(x["price"]),"total":float(x["total"]),"executed_at":iso(x["executed_at"])} for x in rows]}
@mcp.tool()
def list_conversations():
    """List the authenticated user's saved chat summaries."""
    u=user()
    with db_connection() as db: rows=db.execute("SELECT id,title,updated_at FROM assistant_conversations WHERE user_id=%s ORDER BY updated_at DESC",(u["id"],)).fetchall()
    return {"conversations":[{**x,"updated_at":iso(x["updated_at"])} for x in rows]}
@mcp.tool()
def get_conversation_history(conversation_id:int):
    """Read one owned conversation."""
    u=user()
    with db_connection() as db: rows=db.execute("SELECT role,content,created_at FROM assistant_messages WHERE conversation_id=%s AND user_id=%s ORDER BY id",(conversation_id,u["id"])).fetchall()
    return {"messages":[{**x,"created_at":iso(x["created_at"])} for x in rows]}
if __name__ == "__main__": mcp.run(transport="stdio")
