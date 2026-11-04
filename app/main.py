"""Pulse Markets FastAPI application backend.

Handles authentication, role-based access control, paper trading, portfolio
management, transaction history, and local AI assistant integration.
"""
from __future__ import annotations

import hashlib, json, os, random, secrets
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(title="Pulse Markets API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
DATABASE_URL = os.getenv("DATABASE_URL")
OLLAMA_CHAT_URL = os.getenv("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
MASTER = os.getenv("PULSE_MASTER_EMAIL", "master@pulse.local").strip().lower()
ROLES = {"master": {"*"}, "admin": {"users:write", "roles:write", "permissions:write", "quotes:read", "trades:write"}, "analyst": {"quotes:read", "trades:write"}, "viewer": {"quotes:read", "trades:write"}, "customer": {"quotes:read", "trades:write"}}
quotes = {"AAPL": {"name": "Apple Inc.", "price": 218.74, "open": 217.20}, "MSFT": {"name": "Microsoft Corp.", "price": 451.62, "open": 448.91}, "NVDA": {"name": "NVIDIA Corp.", "price": 141.93, "open": 143.26}, "TSLA": {"name": "Tesla Inc.", "price": 326.15, "open": 320.84}, "AMZN": {"name": "Amazon.com Inc.", "price": 227.88, "open": 226.44}}

def secret_value(name):
    value = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE")
    if value and file_name: raise RuntimeError(f"Set either {name} or {name}_FILE, not both")
    if file_name:
        try: value = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as error: raise RuntimeError(f"Could not read {name}_FILE") from error
    return value.strip() if value else None

def database_connection():
    if DATABASE_URL: return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    options = {
        "host": os.getenv("PGHOST", "127.0.0.1"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "pulsedb"),
        "user": os.getenv("PGUSER", "pulse"),
        "connect_timeout": int(os.getenv("PGCONNECT_TIMEOUT", "2")),
    }
    password = secret_value("DATABASE_PASSWORD")
    if password: options["password"] = password
    return psycopg.connect(row_factory=dict_row, **options)

@contextmanager
def db_connection():
    """Context manager providing an active PostgreSQL database connection.

    Yields:
        psycopg.Connection: Database connection with dict_row row factory.

    Raises:
        HTTPException: 503 error if the PostgreSQL database is unavailable.
    """
    try:
        with database_connection() as db: yield db
    except psycopg.OperationalError as error: raise HTTPException(503, "PostgreSQL is unavailable") from error

def private_api(permission, summary):
    def deco(fn): fn.__private_api__ = {"permission": permission, "summary": summary}; return fn
    return deco
def password_hash(password):
    salt = secrets.token_hex(16); return f"{salt}${hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000).hex()}"
def password_matches(password, stored):
    salt, digest = stored.split("$", 1); return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex(), digest)
def roles_for(db, user_id): return {r["role_name"] for r in db.execute("SELECT role_name FROM user_roles WHERE user_id=%s", (user_id,))}
def public_user(user): return {"id": user["id"], "email": user["email"], "roles": sorted(user["roles"])}

def require_user(authorization: Annotated[str | None, Header()] = None):
    """Authenticate a user by inspecting the Bearer token in the Authorization header.

    Args:
        authorization (str | None): HTTP Authorization header value.

    Returns:
        dict: User row from the database with attached user roles.

    Raises:
        HTTPException: 401 error if the token is missing, invalid, or expired.
    """
    token = authorization.removeprefix("Bearer ") if authorization else ""
    with db_connection() as db:
        row = db.execute("SELECT u.id,u.email,u.password_hash FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=%s", (token,)).fetchone()
        if row: row["roles"] = roles_for(db, row["id"])
    if not row: raise HTTPException(status.HTTP_401_UNAUTHORIZED, "A valid Bearer token is required")
    return row
def require_permission(permission):
    def check(user: Annotated[dict, Depends(require_user)]):
        with db_connection() as db: granted = {x["permission"] for x in db.execute("SELECT permission FROM role_permissions WHERE role_name=ANY(%s)", (list(user["roles"]),))}
        if "*" not in granted and permission not in granted: raise HTTPException(403, f"Missing permission: {permission}")
        return user
    return check

class Login(BaseModel): email: str; password: str
class RegisterCustomer(Login): password: str = Field(min_length=8)
class Order(BaseModel): side: Literal["buy", "sell"]; symbol: str = Field(pattern=r"^[A-Z]{1,8}$"); quantity: int = Field(gt=0, le=100_000)
class AssistantQuestion(BaseModel): message: str = Field(min_length=1, max_length=5000); conversation_id: int | None = Field(None, gt=0)
class ProposalRequest(BaseModel): message: str = Field(min_length=1, max_length=1000)
class ProposalDraft(Order): rationale: str = Field(min_length=1, max_length=600)
class ProposalConfirmation(BaseModel): proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
class RolePermissions(BaseModel): permissions: set[str]
INITIAL_PAPER_CASH = 100000.0

class CreateRole(RolePermissions): name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
class UpdateUser(BaseModel): email: str | None = None; password: str | None = Field(None, min_length=8); roles: set[str] | None = None

def portfolio(db, user_id):
    """Calculate and return the current paper trading portfolio for a user.

    Initializes the user's paper portfolio with $100,000 cash if not already created,
    then aggregates cash, holdings, and total portfolio valuation against live simulated quotes.

    Args:
        db: Active database connection.
        user_id (int): ID of the user.

    Returns:
        dict: Portfolio data with cash, holdings_value, total_value, and holdings list.
    """
    db.execute("INSERT INTO portfolios(user_id,cash) VALUES (%s,100000) ON CONFLICT(user_id) DO NOTHING", (user_id,))
    cash = float(db.execute("SELECT cash FROM portfolios WHERE user_id=%s", (user_id,)).fetchone()["cash"]); holdings=[]
    for h in db.execute("SELECT symbol,shares,average_cost FROM holdings WHERE user_id=%s ORDER BY symbol", (user_id,)):
        q=quotes[h["symbol"]]; value=round(h["shares"]*q["price"],2); holdings.append({"symbol":h["symbol"],"name":q["name"],"shares":h["shares"],"average_cost":round(float(h["average_cost"]),2),"current_price":q["price"],"market_value":value})
    value=round(sum(h["market_value"] for h in holdings),2); return {"cash":round(cash,2),"holdings_value":value,"total_value":round(cash+value,2),"holdings":holdings}
def transaction_history(db, user_id, limit=100):
    rows=db.execute("SELECT id,side,symbol,quantity,price,total,executed_at FROM transactions WHERE user_id=%s ORDER BY executed_at DESC LIMIT %s",(user_id,limit)).fetchall()
    return [{**row,"price":float(row["price"]),"total":float(row["total"]),"executed_at":row["executed_at"].isoformat()} for row in rows]
def context(db, user_id):
    return {"initial_funding":INITIAL_PAPER_CASH,"portfolio":portfolio(db,user_id),"transactions":transaction_history(db,user_id),"watchlist":[{"symbol":s,"price":q["price"],"open":q["open"]} for s,q in quotes.items()]}
def performance_summary(ctx):
    pf=ctx["portfolio"]; holdings=[]
    for holding in pf["holdings"]:
        cost=round(holding["shares"]*holding["average_cost"],2); unrealized=round(holding["market_value"]-cost,2)
        holdings.append({**holding,"cost_basis":cost,"unrealized_pl":unrealized})
    overall=round(pf["total_value"]-ctx["initial_funding"],2); unrealized=round(sum(h["unrealized_pl"] for h in holdings),2)
    return {"initial_funding":ctx["initial_funding"],"cash":pf["cash"],"holdings_value":pf["holdings_value"],"total_value":pf["total_value"],"overall_pl":overall,"overall_percent":round(overall/ctx["initial_funding"]*100,2),"realized_pl":round(overall-unrealized,2),"unrealized_pl":unrealized,"holdings":holdings}
def asks_for_performance(message):
    text=message.lower(); return any(term in text for term in ("profit", "loss", "p/l", "performance", "return", "gain", "summary", "portfolio value", "portfolio worth"))
def transaction_view(ctx, message):
    text=message.lower(); wants_sales=any(term in text for term in ("sold", "sell trades", "sell transactions", "my sells"))
    wants_history=any(term in text for term in ("trade history", "transaction history", "all trades", "transactions"))
    if not (wants_sales or wants_history): return None
    rows=[row for row in ctx["transactions"] if not wants_sales or row["side"]=="sell"]
    return {"title":"Sell transactions" if wants_sales else "Transaction history","rows":rows}
def execute_order(db, user_id, order):
    if order.symbol not in quotes: raise HTTPException(404,"Unknown stock symbol")
    price=quotes[order.symbol]["price"]; total=round(price*order.quantity,2); db.execute("INSERT INTO portfolios(user_id,cash) VALUES (%s,100000) ON CONFLICT(user_id) DO NOTHING",(user_id,))
    cash=float(db.execute("SELECT cash FROM portfolios WHERE user_id=%s FOR UPDATE",(user_id,)).fetchone()["cash"]); hold=db.execute("SELECT shares,average_cost FROM holdings WHERE user_id=%s AND symbol=%s FOR UPDATE",(user_id,order.symbol)).fetchone()
    if order.side=="buy":
        if total>cash: raise HTTPException(422,"Insufficient paper cash")
        old=hold["shares"] if hold else 0; cost=float(hold["average_cost"])*old if hold else 0; db.execute("UPDATE portfolios SET cash=cash-%s WHERE user_id=%s",(total,user_id)); db.execute("INSERT INTO holdings(user_id,symbol,shares,average_cost) VALUES (%s,%s,%s,%s) ON CONFLICT(user_id,symbol) DO UPDATE SET shares=EXCLUDED.shares,average_cost=EXCLUDED.average_cost",(user_id,order.symbol,old+order.quantity,round((cost+total)/(old+order.quantity),4)))
    else:
        if not hold or hold["shares"]<order.quantity: raise HTTPException(422,"Insufficient shares to sell")
        db.execute("UPDATE portfolios SET cash=cash+%s WHERE user_id=%s",(total,user_id)); db.execute("DELETE FROM holdings WHERE user_id=%s AND symbol=%s",(user_id,order.symbol)) if hold["shares"]==order.quantity else db.execute("UPDATE holdings SET shares=shares-%s WHERE user_id=%s AND symbol=%s",(order.quantity,user_id,order.symbol))
    oid=secrets.token_hex(8); at=datetime.now(timezone.utc); db.execute("INSERT INTO transactions(id,user_id,side,symbol,quantity,price,total,executed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(oid,user_id,order.side,order.symbol,order.quantity,price,total,at)); return {"id":oid,"side":order.side,"symbol":order.symbol,"quantity":order.quantity,"price":price,"total":total,"executed_at":at.isoformat()},portfolio(db,user_id)

def ollama_reply(ctx, message):
    system_prompt="""You are a concise local paper-trading portfolio assistant. Use only supplied data; never claim execution.

For P/L calculations: total account value is cash plus holdings value. Overall P/L is total account value minus initial_funding. Unrealized P/L is holdings market value minus holdings cost basis. Realized P/L is overall P/L minus unrealized P/L. Never subtract current holdings cost basis from total account value, because that incorrectly counts cash as profit. When asked for performance, show initial funding, cash, holdings value, total account value, overall P/L and percentage, realized P/L, unrealized P/L, and each holding's cost basis, market value, and unrealized P/L. The supplied transactions list is the authoritative record for buy and sell history; use it to answer trade-history questions and never infer that current holdings mean no prior sales."""
    payload={"model":OLLAMA_MODEL,"stream":False,"messages":[{"role":"system","content":system_prompt},{"role":"user","content":f"Context: {json.dumps(ctx)}\nQuestion: {message}"}]}
    with urlopen(Request(OLLAMA_CHAT_URL,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST"),timeout=OLLAMA_TIMEOUT_SECONDS) as r: answer=json.loads(r.read().decode()).get("message",{}).get("content","").strip()
    if not answer: raise ValueError("Empty Ollama response")
    return answer

@app.post("/api/auth/register",status_code=201)
def register(p: RegisterCustomer):
    with db_connection() as db:
        if db.execute("SELECT 1 FROM users WHERE lower(email)=lower(%s)",(p.email,)).fetchone(): raise HTTPException(409,"Email already exists")
        user=db.execute("INSERT INTO users(email,password_hash) VALUES (%s,%s) RETURNING id,email",(p.email,password_hash(p.password))).fetchone(); db.execute("INSERT INTO user_roles VALUES (%s,'customer')",(user["id"],)); db.execute("INSERT INTO portfolios VALUES (%s,100000)",(user["id"],)); user["roles"]={"customer"}
    return public_user(user)
@app.post("/api/auth/login")
def login(p: Login):
    with db_connection() as db:
        user=db.execute("SELECT id,email,password_hash FROM users WHERE lower(email)=lower(%s)",(p.email,)).fetchone()
        if not user or not password_matches(p.password,user["password_hash"]): raise HTTPException(401,"Invalid email or password")
        user["roles"]=roles_for(db,user["id"]); token=secrets.token_urlsafe(32); db.execute("INSERT INTO sessions(token,user_id) VALUES (%s,%s)",(token,user["id"]))
    return {"access_token":token,"token_type":"bearer","user":public_user(user)}
@app.get("/api/quotes")
@private_api("quotes:read","Read the live stock watchlist")
def get_quotes(_: Annotated[dict,Depends(require_permission("quotes:read"))]):
    for q in quotes.values(): q["price"]=round(max(1,q["price"]*(1+random.uniform(-.003,.003))),2)
    return {"quotes":[{"symbol":s,**q,"change":round(q["price"]-q["open"],2),"change_percent":round((q["price"]/q["open"]-1)*100,2)} for s,q in quotes.items()]}
@app.get("/api/portfolio")
@private_api("trades:write","Read the signed-in user's paper portfolio")
def get_portfolio(u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db:return portfolio(db,u["id"])
@app.post("/api/orders",status_code=201)
@private_api("trades:write","Place a paper buy or sell order")
def order(p:Order,u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db: o,pf=execute_order(db,u["id"],p)
    return {"order":o,"portfolio":pf}
@app.get("/api/transactions")
@private_api("trades:write","Read the signed-in user's paper trade history")
def transactions(u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db: rows=db.execute("SELECT * FROM transactions WHERE user_id=%s ORDER BY executed_at DESC",(u["id"],)).fetchall()
    return {"transactions":[{**x,"price":float(x["price"]),"total":float(x["total"]),"executed_at":x["executed_at"].isoformat()} for x in rows]}

@app.get("/api/assistant/conversations")
@private_api("trades:write","Read the signed-in user's saved chat summaries")
def conversations(u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db: rows=db.execute("SELECT c.id,c.title,c.updated_at,COUNT(m.id) message_count,COALESCE((SELECT content FROM assistant_messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1),'') preview FROM assistant_conversations c LEFT JOIN assistant_messages m ON m.conversation_id=c.id WHERE c.user_id=%s GROUP BY c.id ORDER BY c.updated_at DESC",(u["id"],)).fetchall()
    return {"conversations":[{**x,"updated_at":x["updated_at"].isoformat()} for x in rows]}
@app.get("/api/assistant/history")
@private_api("trades:write","Read messages from one saved Assistant conversation")
def history(conversation_id:Annotated[int,Query(gt=0)],u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db:
        if not db.execute("SELECT 1 FROM assistant_conversations WHERE id=%s AND user_id=%s",(conversation_id,u["id"])).fetchone(): raise HTTPException(404,"Conversation not found")
        rows=db.execute("SELECT role,content,created_at FROM assistant_messages WHERE conversation_id=%s ORDER BY id",(conversation_id,)).fetchall()
    return {"messages":[{**x,"created_at":x["created_at"].isoformat()} for x in rows]}
@app.post("/api/assistant/chat")
@private_api("trades:write","Ask the local portfolio assistant")
def chat(p:AssistantQuestion,u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db:
        if p.conversation_id:
            c=db.execute("SELECT id FROM assistant_conversations WHERE id=%s AND user_id=%s",(p.conversation_id,u["id"])).fetchone()
            if not c: raise HTTPException(404,"Conversation not found")
            cid=c["id"]
        else: cid=db.execute("INSERT INTO assistant_conversations(user_id,title) VALUES (%s,%s) RETURNING id",(u["id"]," ".join(p.message.split())[:80])).fetchone()["id"]
        ctx=context(db,u["id"])
    show_performance=asks_for_performance(p.message); trades=transaction_view(ctx,p.message)
    if show_performance: answer="Here is your current portfolio performance."; mode="calculated"
    elif trades: answer=f"Here are your {trades['title'].lower()}."; mode="calculated"
    else:
        try: answer=ollama_reply(ctx,p.message); mode="ollama"
        except (URLError,TimeoutError,ValueError,json.JSONDecodeError,OSError): answer=f"Your portfolio is worth ${ctx['portfolio']['total_value']:,.2f}."; mode="fallback"
    with db_connection() as db: db.execute("INSERT INTO assistant_messages(user_id,conversation_id,role,content) VALUES (%s,%s,%s,%s),(%s,%s,%s,%s)",(u["id"],cid,"user",p.message.strip(),u["id"],cid,"assistant",answer)); db.execute("UPDATE assistant_conversations SET updated_at=NOW() WHERE id=%s",(cid,))
    return {"answer":answer,"conversation_id":cid,"mode":mode,"model":OLLAMA_MODEL if mode=="ollama" else None,"performance":performance_summary(ctx) if show_performance else None,"transactions":trades}

@app.post("/api/assistant/proposals",status_code=201)
@private_api("trades:write","Ask the local agent to propose a confirmation-gated paper trade")
def proposal(p:ProposalRequest,u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db: ctx=context(db,u["id"])
    payload={"model":OLLAMA_MODEL,"stream":False,"format":"json","messages":[{"role":"system","content":"Return JSON only: side, symbol, quantity, rationale. Use only supplied data. Proposal only; never execute."},{"role":"user","content":f"{json.dumps(ctx)}\n{p.message}"}]}
    try:
        with urlopen(Request(OLLAMA_CHAT_URL,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST"),timeout=OLLAMA_TIMEOUT_SECONDS) as r: draft=ProposalDraft.model_validate_json(json.loads(r.read().decode())["message"]["content"])
        if draft.symbol not in quotes: raise ValueError()
    except Exception as e: raise HTTPException(503,"Local agent could not create a valid proposal") from e
    pid=secrets.token_hex(16); expires=datetime.now(timezone.utc)+timedelta(minutes=10); price=quotes[draft.symbol]["price"]
    with db_connection() as db: db.execute("INSERT INTO trade_proposals(id,user_id,side,symbol,quantity,estimated_price,rationale,status,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s)",(pid,u["id"],draft.side,draft.symbol,draft.quantity,price,draft.rationale,expires))
    return {"proposal":{"id":pid,"side":draft.side,"symbol":draft.symbol,"quantity":draft.quantity,"estimated_price":price,"estimated_total":round(price*draft.quantity,2),"rationale":draft.rationale,"status":"pending","expires_at":expires.isoformat()},"model":OLLAMA_MODEL}
@app.post("/api/assistant/proposals/confirm",status_code=201)
@private_api("trades:write","Confirm one pending agent paper-trade proposal")
def confirm(p:ProposalConfirmation,u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db:
        x=db.execute("SELECT * FROM trade_proposals WHERE id=%s AND user_id=%s FOR UPDATE",(p.proposal_id,u["id"])).fetchone()
        if not x: raise HTTPException(404,"Proposal not found")
        if x["status"]!="pending" or x["expires_at"]<=datetime.now(timezone.utc): raise HTTPException(409,"Proposal is no longer pending")
        o,pf=execute_order(db,u["id"],Order(side=x["side"],symbol=x["symbol"],quantity=x["quantity"])); db.execute("UPDATE trade_proposals SET status='confirmed',order_id=%s WHERE id=%s",(o["id"],p.proposal_id))
    return {"order":o,"portfolio":pf,"proposal_id":p.proposal_id}
@app.post("/api/assistant/proposals/{proposal_id}/cancel",status_code=204)
@private_api("trades:write","Cancel one pending agent paper-trade proposal")
def cancel(proposal_id:str,u:Annotated[dict,Depends(require_permission("trades:write"))]):
    with db_connection() as db:
        if not db.execute("UPDATE trade_proposals SET status='cancelled' WHERE id=%s AND user_id=%s AND status='pending'",(proposal_id,u["id"])).rowcount: raise HTTPException(404,"Pending proposal not found")

@app.get("/api/admin/overview")
@private_api("roles:write","Read administration overview")
def overview(u:Annotated[dict,Depends(require_permission("roles:write"))]):
    with db_connection() as db:
        users=[]
        for row in db.execute("SELECT id,email FROM users ORDER BY id"): row["roles"]=sorted(roles_for(db,row["id"])); users.append(row)
        roles={r["name"]:sorted(x["permission"] for x in db.execute("SELECT permission FROM role_permissions WHERE role_name=%s",(r["name"],))) for r in db.execute("SELECT name FROM roles ORDER BY name")}
    return {"users":users,"roles":roles,"current_user":public_user(u),"private_apis":[]}
@app.get("/api/admin/users")
@private_api("roles:write","Search and paginate users for Management")
def admin_users(_:Annotated[dict,Depends(require_permission("roles:write"))],draw:int=0,start:int=Query(0,ge=0),length:int=Query(10,ge=1,le=100),search:str=Query("",alias="search[value]"),order_column:int=Query(0,alias="order[0][column]"),order_direction:str=Query("asc",alias="order[0][dir]")):
    with db_connection() as db:
        rows=[]
        for row in db.execute("SELECT id,email FROM users ORDER BY id"):
            roles=sorted(roles_for(db,row["id"])); rows.append({"id":row["id"],"email":row["email"],"roles":roles,"roles_display":", ".join(roles)})
    needle=search.strip().lower(); filtered=[row for row in rows if not needle or needle in row["email"].lower() or needle in row["roles_display"].lower()]
    filtered.sort(key=(lambda row:row["roles_display"].lower()) if order_column==1 else lambda row:row["email"].lower(),reverse=order_direction.lower()=="desc")
    return {"draw":draw,"recordsTotal":len(rows),"recordsFiltered":len(filtered),"data":filtered[start:start+length]}
@app.delete("/api/users/{user_id}",status_code=204)
@private_api("users:write","Delete a user account")
def delete_user(user_id:int,u:Annotated[dict,Depends(require_permission("users:write"))]):
    with db_connection() as db:
        target=db.execute("SELECT email FROM users WHERE id=%s",(user_id,)).fetchone()
        if not target: raise HTTPException(404,"User not found")
        if target["email"].lower()==MASTER: raise HTTPException(403,"The master account is permanently protected")
        db.execute("DELETE FROM users WHERE id=%s",(user_id,))

SCHEMA="""
CREATE TABLE IF NOT EXISTS roles(name TEXT PRIMARY KEY,built_in BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS role_permissions(role_name TEXT REFERENCES roles(name) ON DELETE CASCADE,permission TEXT NOT NULL,PRIMARY KEY(role_name,permission));
CREATE TABLE IF NOT EXISTS users(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_roles(user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,role_name TEXT REFERENCES roles(name),PRIMARY KEY(user_id,role_name));
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id BIGINT REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS portfolios(user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,cash NUMERIC(14,2) NOT NULL DEFAULT 100000);
CREATE TABLE IF NOT EXISTS holdings(user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,symbol TEXT NOT NULL,shares INTEGER NOT NULL,average_cost NUMERIC(14,4) NOT NULL,PRIMARY KEY(user_id,symbol));
CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY,user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,side TEXT NOT NULL,symbol TEXT NOT NULL,quantity INTEGER NOT NULL,price NUMERIC(14,2) NOT NULL,total NUMERIC(14,2) NOT NULL,executed_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS assistant_conversations(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS assistant_messages(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,conversation_id BIGINT REFERENCES assistant_conversations(id) ON DELETE CASCADE,role TEXT NOT NULL,content TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS trade_proposals(id TEXT PRIMARY KEY,user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,side TEXT NOT NULL,symbol TEXT NOT NULL,quantity INTEGER NOT NULL,estimated_price NUMERIC(14,2) NOT NULL,rationale TEXT NOT NULL,status TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL,order_id TEXT REFERENCES transactions(id));
"""
def initialize_database():
    try:
        with db_connection() as db:
            db.execute(SCHEMA)
            for role,permissions in ROLES.items():
                db.execute("INSERT INTO roles(name,built_in) VALUES (%s,true) ON CONFLICT(name) DO NOTHING",(role,))
                for permission in permissions: db.execute("INSERT INTO role_permissions(role_name,permission) VALUES (%s,%s) ON CONFLICT DO NOTHING",(role,permission))
            bootstrap_users = [(MASTER, secret_value("PULSE_MASTER_PASSWORD"), "master")]
            admin_email = os.getenv("PULSE_ADMIN_EMAIL", "").strip().lower()
            if admin_email: bootstrap_users.append((admin_email, secret_value("PULSE_ADMIN_PASSWORD"), "admin"))
            reset_passwords = os.getenv("PULSE_RESET_BOOTSTRAP_PASSWORDS", "").lower() in {"1", "true", "yes"}
            for email,password,role in bootstrap_users:
                if not password: continue
                if len(password) < 12: raise RuntimeError(f"{role} bootstrap password must be at least 12 characters")
                user = db.execute("SELECT id FROM users WHERE lower(email)=lower(%s)",(email,)).fetchone()
                if user:
                    uid = user["id"]
                    if reset_passwords: db.execute("UPDATE users SET password_hash=%s WHERE id=%s",(password_hash(password),uid))
                else:
                    uid=db.execute("INSERT INTO users(email,password_hash) VALUES (%s,%s) RETURNING id",(email,password_hash(password))).fetchone()["id"]
                db.execute("INSERT INTO user_roles VALUES (%s,%s) ON CONFLICT DO NOTHING",(uid,role))
                db.execute("INSERT INTO portfolios VALUES (%s,100000) ON CONFLICT DO NOTHING",(uid,))
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    from .mcp_server import get_mcp_session_manager
    async with get_mcp_session_manager().run():
        yield

app.router.lifespan_context = lifespan

class LazyMCPApp:
    def __init__(self):
        self._app = None
    async def __call__(self, scope, receive, send):
        if self._app is None:
            from .mcp_server import get_mcp_asgi_app
            self._app = get_mcp_asgi_app()
        await self._app(scope, receive, send)

_lazy_mcp = LazyMCPApp()
app.add_route("/mcp", _lazy_mcp, methods=["GET", "POST", "DELETE", "OPTIONS"])
app.mount("/mcp", _lazy_mcp)

app.mount("/",StaticFiles(directory=Path(__file__).parent.parent/"web",html=True),name="web")
