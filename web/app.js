const { useEffect, useRef, useState } = React;
const { createRoot } = ReactDOM;

const h = React.createElement;
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

function Auth({ onLogin }) {
  const [registering, setRegistering] = useState(false), [email, setEmail] = useState(""), [password, setPassword] = useState(""), [message, setMessage] = useState("");
  async function submit(event) {
    event.preventDefault();
    const response = await fetch(registering ? "/api/auth/register" : "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
    const data = await response.json();
    if (!response.ok) return setMessage(data.detail || "Request failed.");
    if (registering) return setMessage("Customer account created. Sign in now.");
    localStorage.setItem("pulse-token", data.access_token); onLogin(data.user);
  }
  return h("main", { className: "auth-page" }, h("section", { className: "auth-card" }, h("div", { className: "brand" }, h("span", { className: "mark" }, "P"), "pulse markets"), h("h1", null, registering ? "Create customer account" : "Sign in"), h("form", { onSubmit: submit }, h("label", null, "Email", h("input", { type: "email", required: true, value: email, onChange: e => setEmail(e.target.value) })), h("label", null, "Password", h("input", { type: "password", required: true, minLength: 8, value: password, onChange: e => setPassword(e.target.value) })), message && h("p", { className: "form-message" }, message), h("button", { className: "auth-button", type: "submit" }, registering ? "Register" : "Sign in")), h("button", { className: "text-button", onClick: () => { setRegistering(!registering); setMessage(""); } }, registering ? "Already registered? Sign in" : "New customer? Register")));
}

function RoleManager({ admin, headers, reload }) {
  const [roleName, setRoleName] = useState(""), [editing, setEditing] = useState(null), [selected, setSelected] = useState([]), [message, setMessage] = useState("");
  const selectRef = useRef(null);
  const apiOptions = [...new Map(admin.private_apis.map(api => [api.permission, api])).values()];
  useEffect(() => {
    const $ = window.jQuery;
    if (!$ || !$.fn.selectize || !selectRef.current) return;
    const control = $(selectRef.current).selectize({
      plugins: ["remove_button"],
      placeholder: "Search private API permissions...",
      searchField: ["text", "value"],
      create: false,
      onChange: values => setSelected(values),
    })[0].selectize;
    return () => control.destroy();
  }, []);
  function setPermissions(values) {
    setSelected(values);
    const control = selectRef.current?.selectize;
    if (control) control.setValue(values, true);
  }
  async function save(event) {
    event.preventDefault();
    const url = editing ? `/api/roles/${editing}/permissions` : "/api/roles";
    const method = editing ? "PUT" : "POST";
    const payload = editing ? { permissions: selected } : { name: roleName, permissions: selected };
    const response = await fetch(url, { method, headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    setMessage(response.ok ? `Saved ${data.role}.` : data.detail || "Unable to save role.");
    if (response.ok) { setRoleName(""); setEditing(null); setPermissions([]); reload(); }
  }
  async function remove(role) {
    if (!confirm(`Delete the ${role} role?`)) return;
    const response = await fetch(`/api/roles/${role}`, { method: "DELETE", headers });
    setMessage(response.ok ? `Deleted ${role}.` : (await response.json()).detail || "Unable to delete role.");
    if (response.ok) reload();
  }
  return h("div", { className: "card border-0 shadow-sm mt-4 text-dark" }, h("div", { className: "card-body p-4" }, h("h3", { className: "h5" }, editing ? `Edit API access: ${editing}` : "Create role with API access"), h("div", { className: "alert alert-primary py-2 small" }, "Use the search box below to find a private API permission, then click it to add it to this role."), h("form", { onSubmit: save, className: "row g-3" }, h("div", { className: "col-md-4" }, h("label", { className: "form-label" }, "Role name"), h("input", { className: "form-control", required: !editing, disabled: !!editing, value: roleName, placeholder: "support", pattern: "[a-z][a-z0-9_-]{1,31}", onChange: e => setRoleName(e.target.value) })), h("div", { className: "col-md-8" }, h("label", { className: "form-label" }, "Search and add private API permissions"), h("select", { ref: selectRef, multiple: true, className: "form-select", placeholder: "Search private API permissions..." }, apiOptions.map(api => h("option", { key: api.permission, value: api.permission }, `${api.permission} — ${api.method} ${api.path}`))), h("small", { className: "text-secondary" }, `${admin.private_apis.length} private endpoints discovered from the database`)), h("div", { className: "col-12 d-flex gap-2" }, h("button", { type: "submit", className: "btn btn-dark" }, editing ? "Save permissions" : "Create role"), editing && h("button", { type: "button", className: "btn btn-outline-secondary", onClick: () => { setEditing(null); setRoleName(""); setPermissions([]); } }, "Cancel"))), message && h("p", { className: "small mt-3 mb-0" }, message), h("div", { className: "table-responsive mt-4" }, h("table", { className: "table table-sm align-middle mb-0" }, h("thead", null, h("tr", null, h("th", null, "Role"), h("th", null, "Private API access"), h("th", null, "Actions"))), h("tbody", null, Object.entries(admin.roles).map(([role, access]) => h("tr", { key: role }, h("td", null, h("strong", null, role)), h("td", null, access.join(", ") || "No access"), h("td", { className: "d-flex gap-2" }, role !== "master" && h("button", { className: "btn btn-sm btn-outline-primary", onClick: () => { setEditing(role); setRoleName(role); setPermissions(access); } }, "Edit"), !["master", "admin", "analyst", "viewer", "customer"].includes(role) && h("button", { className: "btn btn-sm btn-outline-danger", onClick: () => remove(role) }, "Delete")))))))));
}

function UserRoleManager({ admin, headers, reload }) {
  const [userId, setUserId] = useState(""), [roleName, setRoleName] = useState(""), [message, setMessage] = useState("");
  const userSelectRef = useRef(null);
  useEffect(() => {
    const $ = window.jQuery;
    if (!$ || !$.fn.selectize || !userSelectRef.current) return;
    const control = $(userSelectRef.current).selectize({
      placeholder: "Search users by email...",
      searchField: ["text", "value"],
      create: false,
      onChange: value => setUserId(value),
    })[0].selectize;
    return () => control.destroy();
  }, []);
  async function attach(event) {
    event.preventDefault();
    const response = await fetch(`/api/users/${userId}/roles/${roleName}`, { method: "POST", headers });
    const data = await response.json();
    setMessage(response.ok ? `Attached ${roleName} to ${data.email}.` : data.detail || "Unable to attach role.");
    if (response.ok) reload();
  }
  async function detach(user, role) {
    const response = await fetch(`/api/users/${user.id}/roles/${role}`, { method: "DELETE", headers });
    const data = await response.json();
    setMessage(response.ok ? `Removed ${role} from ${data.email}.` : data.detail || "Unable to remove role.");
    if (response.ok) reload();
  }
  return h("div", { className: "card border-0 shadow-sm mt-4 text-dark" }, h("div", { className: "card-body p-4" }, h("h3", { className: "h5" }, "Assign roles to users"), h("p", { className: "text-secondary small" }, "Search users by email; assignments are saved in SQLite."), h("form", { className: "row g-3 align-items-end", onSubmit: attach }, h("div", { className: "col-md-5" }, h("label", { className: "form-label" }, "Search user"), h("select", { ref: userSelectRef, className: "form-select", required: true }, h("option", { value: "" }, ""), admin.users.map(user => h("option", { key: user.id, value: user.id }, user.email)))), h("div", { className: "col-md-5" }, h("label", { className: "form-label" }, "Role to add"), h("select", { className: "form-select", required: true, value: roleName, onChange: e => setRoleName(e.target.value) }, h("option", { value: "" }, "Choose a role"), Object.keys(admin.roles).map(role => h("option", { key: role, value: role }, role)))), h("div", { className: "col-md-2" }, h("button", { className: "btn btn-success w-100", type: "submit" }, "Attach"))), message && h("p", { className: "small mt-3 mb-0" }, message), h("div", { className: "table-responsive mt-4" }, h("table", { className: "table table-sm align-middle mb-0" }, h("thead", null, h("tr", null, h("th", null, "User"), h("th", null, "Assigned roles"))), h("tbody", null, admin.users.map(user => h("tr", { key: user.id }, h("td", null, user.email), h("td", null, user.roles.length ? user.roles.map(role => h("span", { className: "badge text-bg-secondary me-1", key: role }, role, " ", h("button", { className: "badge-action", title: `Remove ${role}`, onClick: () => detach(user, role) }, "×"))) : "No roles"))))))));
}

function UserManagement({ admin, headers, reload }) {
  const [selectedId, setSelectedId] = useState(""), [roles, setRoles] = useState([]), [email, setEmail] = useState(""), [password, setPassword] = useState(""), [message, setMessage] = useState("");
  const tableElementRef = useRef(null), dataTableRef = useRef(null);
  const selectedUser = admin.users.find(user => String(user.id) === selectedId);
  const master = admin.current_user.email === "master@pulse.local";
  function chooseUser(id) { const user = admin.users.find(item => String(item.id) === id); setSelectedId(id); setRoles(user?.roles || []); setEmail(user?.email || ""); setPassword(""); }
  useEffect(() => {
    const $ = window.jQuery;
    if (!$ || !$.fn.DataTable || !tableElementRef.current) return;
    const table = $(tableElementRef.current).DataTable({
      processing: true, serverSide: true, pageLength: 10,
      ajax: { url: "/api/admin/users", headers, dataSrc: "data" },
      columns: [
        { data: "email" },
        { data: "roles_display" },
        { data: null, orderable: false, searchable: false, render: user => user.email === "master@pulse.local" ? '<span class="text-secondary small">Protected</span>' : '<select class="form-select form-select-sm user-action"><option value="" selected>Actions</option><option value="edit">Edit</option><option value="remove">Remove</option></select>' },
      ],
    });
    dataTableRef.current = table;
    $(tableElementRef.current).on("change", ".user-action", event => { const action = event.target.value; event.target.value = ""; const user = table.row($(event.target).closest("tr")).data(); if (action === "edit") chooseUser(String(user.id)); if (action === "remove") remove(user); });
    return () => { $(tableElementRef.current).off("change", ".user-action"); table.destroy(); dataTableRef.current = null; };
  }, []);
  async function save() {
    if (!selectedUser) return;
    const payload = master ? { email, password: password || undefined, roles } : { roles };
    const response = await fetch(`/api/users/${selectedUser.id}`, { method: "PUT", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    setMessage(response.ok ? `Updated ${data.email}.` : data.detail || "Unable to edit user.");
    if (response.ok) { reload(); dataTableRef.current?.ajax.reload(null, false); setSelectedId(""); setRoles([]); setEmail(""); setPassword(""); }
  }
  async function remove(user) {
    if (!confirm(`Delete ${user.email}? This cannot be undone.`)) return;
    const response = await fetch(`/api/users/${user.id}`, { method: "DELETE", headers });
    if (response.ok) { setMessage(`Deleted ${user.email}.`); reload(); dataTableRef.current?.ajax.reload(null, false); return; }
    setMessage((await response.json()).detail || "Unable to delete user.");
  }
  return h("div", { className: "card border-0 shadow-sm mt-4 text-dark" }, h("div", { className: "card-body p-4" },
    h("h3", { className: "h5" }, "User management"),
    h("p", { className: "text-secondary small" }, "Admins and the master can edit or delete users. master@pulse.local is permanently protected."),
    message && h("p", { className: "small" }, message),
    h("div", { className: "table-responsive mt-4" }, h("table", { ref: tableElementRef, className: "table table-sm align-middle w-100" }, h("thead", null, h("tr", null, h("th", null, "Email"), h("th", null, "Roles"), h("th", null, "Actions"))))),
    selectedUser && h(React.Fragment, null, h("div", { className: "modal fade show", style: { display: "block" }, role: "dialog", "aria-modal": true }, h("div", { className: "modal-dialog modal-dialog-centered" }, h("div", { className: "modal-content" }, h("div", { className: "modal-header" }, h("h5", { className: "modal-title" }, "Edit user"), h("button", { type: "button", className: "btn-close", "aria-label": "Close", onClick: () => { setSelectedId(""); setRoles([]); setEmail(""); setPassword(""); } })), h("div", { className: "modal-body" }, h("p", { className: "text-secondary" }, master ? "Update account details and access." : "Admins can update account access."), h("label", { className: "form-label" }, "Email"), h("input", { className: "form-control", type: "email", value: email, disabled: !master, onChange: e => setEmail(e.target.value) }), master && h(React.Fragment, null, h("label", { className: "form-label mt-3" }, "New password (optional)"), h("input", { className: "form-control", type: "password", minLength: 8, value: password, placeholder: "Leave blank to keep current password", onChange: e => setPassword(e.target.value) })), h("fieldset", { className: "mt-3" }, h("legend", { className: "form-label" }, "Roles"), Object.keys(admin.roles).map(role => h("label", { className: "form-check", key: role }, h("input", { className: "form-check-input", type: "checkbox", checked: roles.includes(role), onChange: e => setRoles(e.target.checked ? [...roles, role] : roles.filter(item => item !== role)) }), h("span", { className: "form-check-label" }, role))))), h("div", { className: "modal-footer" }, h("button", { className: "btn btn-outline-secondary", onClick: () => { setSelectedId(""); setRoles([]); setEmail(""); setPassword(""); } }, "Cancel"), h("button", { className: "btn btn-primary", onClick: save }, "Save changes"))))), h("div", { className: "modal-backdrop fade show user-modal-blur" }))
  ));
}

function Trading({ quotes, headers, onLogout }) {
  const [portfolio, setPortfolio] = useState(null), [symbol, setSymbol] = useState("AAPL"), [quantity, setQuantity] = useState(1), [message, setMessage] = useState("");
  const loadPortfolio = async () => { const response = await fetch("/api/portfolio", { headers }); if (response.status === 401) return onLogout(); if (response.ok) setPortfolio(await response.json()); };
  useEffect(() => { loadPortfolio(); }, []);
  async function submit(side) {
    setMessage("");
    const response = await fetch("/api/orders", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ side, symbol, quantity: Number(quantity) }) });
    const data = await response.json();
    if (!response.ok) return setMessage(data.detail || "Order could not be placed.");
    setPortfolio(data.portfolio); setMessage(`${side === "buy" ? "Bought" : "Sold"} ${data.order.quantity} ${data.order.symbol} at ${money.format(data.order.price)}.`);
  }
  const orderForm = h("div", { className: "content trade-card" },
    h("h2", null, "Place a paper trade"),
    h("p", { className: "trade-note" }, "Trade against the current displayed price. Your starting balance is $100,000."),
    h("div", { className: "order-fields" },
      h("label", null, "Stock", h("select", { value: symbol, onChange: e => setSymbol(e.target.value) }, quotes.map(quote => h("option", { key: quote.symbol, value: quote.symbol }, `${quote.symbol} — ${quote.name}`)))),
      h("label", null, "Shares", h("input", { type: "number", min: 1, step: 1, value: quantity, onChange: e => setQuantity(e.target.value) })),
      h("div", { className: "order-actions" }, h("button", { onClick: () => submit("buy") }, "Buy"), h("button", { className: "sell-button", onClick: () => submit("sell") }, "Sell"))
    ),
    message && h("p", { className: "form-message" }, message)
  );
  const portfolioContent = !portfolio ? h("p", { className: "trade-note" }, "Loading portfolio…") : h(React.Fragment, null,
    h("div", { className: "portfolio-totals" },
      h("div", null, h("small", null, "TOTAL VALUE"), h("strong", null, money.format(portfolio.total_value))),
      h("div", null, h("small", null, "CASH"), h("strong", null, money.format(portfolio.cash))),
      h("div", null, h("small", null, "HOLDINGS"), h("strong", null, money.format(portfolio.holdings_value)))
    ),
    portfolio.holdings.length ? h("div", { className: "table" }, portfolio.holdings.map(holding => h("article", { className: "row", key: holding.symbol },
      h("div", { className: "company" }, h("b", null, holding.symbol), h("small", null, `${holding.shares} shares · avg ${money.format(holding.average_cost)}`)),
      h("strong", { className: "price" }, money.format(holding.market_value)),
      h("span", null, money.format(holding.current_price))
    ))) : h("p", { className: "trade-note" }, "No holdings yet.")
  );
  return h("section", { className: "trade-layout" }, orderForm, h("div", { className: "content portfolio-card" }, h("h2", null, "Your portfolio"), portfolioContent));
}

function History({ headers, onLogout }) {
  const [transactions, setTransactions] = useState([]);
  useEffect(() => { (async () => { const response = await fetch("/api/transactions", { headers }); if (response.status === 401) return onLogout(); if (response.ok) setTransactions((await response.json()).transactions); })(); }, []);
  return h("section", { className: "content" }, h("h2", null, "Transaction history"), transactions.length ? h("div", { className: "table" }, transactions.map(transaction => h("article", { className: "row", key: transaction.id }, h("div", { className: "company" }, h("b", null, `${transaction.side.toUpperCase()} ${transaction.symbol}`), h("small", null, new Date(transaction.executed_at).toLocaleString())), h("strong", { className: "price" }, `${transaction.quantity} shares`), h("span", { className: transaction.side === "buy" ? "negative" : "positive" }, `${transaction.side === "buy" ? "-" : "+"}${money.format(transaction.total)}`)))) : h("p", { className: "trade-note" }, "No trades yet."));
}

function Assistant({ headers, onLogout }) {
  const welcome = { role: "assistant", text: "I use local gpt-oss with your saved portfolio context. Ask for analysis, or request a paper-trade proposal. Nothing executes without your confirmation." };
  const [conversations, setConversations] = useState([]), [selectedId, setSelectedId] = useState(null);
  const [messages, setMessages] = useState([welcome]);
  const [question, setQuestion] = useState(""), [loading, setLoading] = useState(false);
  const [proposal, setProposal] = useState(null), [proposalBusy, setProposalBusy] = useState(false), [proposalNotice, setProposalNotice] = useState("");
  async function loadConversations(selectLatest = false) {
    const response = await fetch("/api/assistant/conversations", { headers }); if (response.status === 401) return onLogout(); if (!response.ok) return;
    const data = await response.json(); setConversations(data.conversations);
    if (selectLatest && data.conversations.length) loadConversation(data.conversations[0].id);
  }
  async function loadConversation(id) {
    const response = await fetch(`/api/assistant/history?conversation_id=${id}`, { headers }); if (response.status === 401) return onLogout(); if (!response.ok) return;
    const data = await response.json(); setSelectedId(id); setMessages(data.messages.map(message => ({ role: message.role, text: message.content })));
  }
  useEffect(() => { loadConversations(true); }, []);
  function newConversation() { setSelectedId(null); setMessages([welcome]); setQuestion(""); setProposal(null); setProposalNotice(""); }
  async function ask(event) {
    event.preventDefault(); const text = question.trim(); if (!text || loading) return;
    setQuestion(""); setLoading(true); setMessages(current => [...current, { role: "user", text }]);
    const response = await fetch("/api/assistant/chat", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ message: text, conversation_id: selectedId }) });
    if (response.status === 401) return onLogout();
    const data = await response.json(); if (response.ok) { setSelectedId(data.conversation_id); setMessages(current => [...current, { role: "assistant", text: data.answer, performance: data.performance, transactions: data.transactions }]); loadConversations(); } else setMessages(current => [...current, { role: "assistant", text: data.detail || "I could not read your portfolio." }]); setLoading(false);
  }
  async function propose() {
    const message = question.trim() || "Analyze my portfolio and propose one conservative paper trade."; if (proposalBusy) return;
    setProposalBusy(true); setProposalNotice("");
    const response = await fetch("/api/assistant/proposals", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    if (response.status === 401) return onLogout(); const data = await response.json();
    if (response.ok) { setProposal(data.proposal); setQuestion(""); } else setProposalNotice(data.detail || "I could not create a proposal."); setProposalBusy(false);
  }
  async function decide(action) {
    if (!proposal || proposalBusy) return; setProposalBusy(true); setProposalNotice("");
    const response = await fetch(action === "confirm" ? "/api/assistant/proposals/confirm" : `/api/assistant/proposals/${proposal.id}/cancel`, { method: action === "confirm" ? "POST" : "POST", headers: { ...headers, "Content-Type": "application/json" }, body: action === "confirm" ? JSON.stringify({ proposal_id: proposal.id }) : undefined });
    if (response.status === 401) return onLogout(); const data = response.status === 204 ? {} : await response.json();
    if (response.ok) { setProposal(null); setProposalNotice(action === "confirm" ? `Paper order filled: ${data.order.side.toUpperCase()} ${data.order.quantity} ${data.order.symbol} at ${money.format(data.order.price)}.` : "Proposal cancelled. No order was placed."); } else setProposalNotice(data.detail || "Proposal could not be updated."); setProposalBusy(false);
  }
  const signedMoney = value => `${value >= 0 ? "+" : "-"}${money.format(Math.abs(value))}`;
  function PerformanceTable({ data }) {
    const metrics = [["Initial funding", money.format(data.initial_funding)], ["Cash", money.format(data.cash)], ["Holdings value", money.format(data.holdings_value)], ["Total account value", money.format(data.total_value)], ["Overall P/L", `${signedMoney(data.overall_pl)} (${data.overall_percent >= 0 ? "+" : ""}${data.overall_percent}%)`], ["Realized P/L", signedMoney(data.realized_pl)], ["Unrealized P/L", signedMoney(data.unrealized_pl)]];
    const metricRows = metrics.map(([label, value]) => h("tr", { key: label }, h("th", null, label), h("td", null, value)));
    const holdingRows = data.holdings.map(holding => h("tr", { key: holding.symbol }, h("td", null, holding.symbol), h("td", null, holding.shares), h("td", null, money.format(holding.average_cost)), h("td", null, money.format(holding.market_value)), h("td", null, signedMoney(holding.unrealized_pl))));
    return h("section", { className: "assistant-performance" },
      h("h3", null, "Portfolio performance"),
      h("table", null, h("tbody", null, metricRows)),
      h("h4", null, "Holdings"),
      h("table", null,
        h("thead", null, h("tr", null, ["Symbol", "Shares", "Avg cost", "Market value", "Unrealized P/L"].map(label => h("th", { key: label }, label)))),
        h("tbody", null, holdingRows)
      )
    );
  }
  function TransactionTable({ data }) { return h("section", { className: "assistant-performance" }, h("h3", null, data.title), h("table", null, h("thead", null, h("tr", null, ["Time", "Symbol", "Qty", "Price", "Total"].map(label => h("th", { key: label }, label)))), h("tbody", null, data.rows.map(row => h("tr", { key: row.id }, h("td", null, new Date(row.executed_at).toLocaleString("en-IN")), h("td", null, row.symbol), h("td", null, row.quantity), h("td", null, money.format(row.price)), h("td", null, money.format(row.total))))))); }
  const proposalCard = proposal && h("section", { className: "trade-proposal" }, h("small", null, "PENDING PAPER-TRADE PROPOSAL"), h("h3", null, `${proposal.side.toUpperCase()} ${proposal.quantity} ${proposal.symbol}`), h("p", null, proposal.rationale), h("p", { className: "trade-note" }, `Estimated ${money.format(proposal.estimated_total)} at ${money.format(proposal.estimated_price)}. Expires ${new Date(proposal.expires_at).toLocaleTimeString()}.`), h("div", { className: "proposal-actions" }, h("button", { onClick: () => decide("confirm"), disabled: proposalBusy }, "Confirm paper order"), h("button", { className: "proposal-cancel", onClick: () => decide("cancel"), disabled: proposalBusy }, "Cancel")));
  return h("section", { className: "assistant-layout" }, h("aside", { className: "assistant-sidebar" }, h("div", { className: "assistant-sidebar-head" }, h("span", null, "YOUR CHATS"), h("button", { onClick: newConversation }, "+ New")), h("nav", null, conversations.length ? conversations.map(conversation => h("button", { key: conversation.id, className: `assistant-chat-link ${selectedId === conversation.id ? "active" : ""}`, onClick: () => loadConversation(conversation.id) }, h("b", null, conversation.title), h("small", null, conversation.preview || `${conversation.message_count} messages`))) : h("p", { className: "assistant-empty" }, "Your saved chats will appear here."))), h("div", { className: "content assistant-card" }, h("div", { className: "assistant-heading" }, h("div", null, h("h2", null, selectedId ? (conversations.find(chat => chat.id === selectedId)?.title || "Conversation") : "New conversation"), h("p", { className: "trade-note" }, "Local analysis can propose one paper trade. Only your confirmation executes it.")), h("span", { className: "assistant-status" }, "LOCAL GPT-OSS")), h("div", { className: "assistant-messages", "aria-live": "polite" }, messages.map((message, index) => h("article", { className: `assistant-message ${message.role}`, key: index }, h("small", null, message.role === "user" ? "YOU" : "PULSE ASSISTANT"), h("p", null, message.text), message.performance && h(PerformanceTable, { data: message.performance }), message.transactions && h(TransactionTable, { data: message.transactions }))), loading && h("article", { className: "assistant-message assistant" }, h("small", null, "PULSE ASSISTANT"), h("p", null, "Reviewing your saved data…"))), proposalCard, proposalNotice && h("p", { className: "proposal-notice" }, proposalNotice), h("form", { className: "assistant-form", onSubmit: ask }, h("input", { value: question, placeholder: "Ask a question or describe the analysis you want", onChange: event => setQuestion(event.target.value) }), h("button", { type: "submit", disabled: loading || proposalBusy }, "Ask"), h("button", { type: "button", onClick: propose, disabled: loading || proposalBusy }, proposalBusy ? "Working…" : "Analyze & propose"))));
}

function Dashboard({ user, onLogout, page, onNavigate }) {
  const [quotes, setQuotes] = useState([]), [admin, setAdmin] = useState(null), [managerPanel, setManagerPanel] = useState("roles");
  const manager = user.roles.some(role => role === "master" || role === "admin"), master = user.roles.includes("master");
  const activePage = manager && page === "management" ? "management" : ["trade", "history", "assistant"].includes(page) ? page : "home";
  const headers = { Authorization: `Bearer ${localStorage.getItem("pulse-token")}` };
  async function refreshQuotes() { const response = await fetch("/api/quotes", { headers }); if (response.status === 401) return onLogout(); if (response.ok) setQuotes((await response.json()).quotes); }
  async function reload() { const response = await fetch("/api/admin/overview", { headers }); if (response.status === 401) return onLogout(); if (response.ok) setAdmin(await response.json()); }
  useEffect(() => { refreshQuotes(); if (manager) reload(); const timer = setInterval(refreshQuotes, 5000); return () => clearInterval(timer); }, []);
  const managementView = activePage === "management" && admin && h("section", { className: "admin-panel" }, h("div", { className: "admin-grid" }, h("div", null, h("span", null, "USERS"), h("strong", null, admin.users.length)), h("div", null, h("span", null, "ROLES"), h("strong", null, Object.keys(admin.roles).length)), h("div", null, h("span", null, "ACCESS"), h("strong", null, master ? "MASTER" : "ADMIN"))), h("div", { className: "row g-4 mt-1" }, h("aside", { className: "col-md-3" }, h("nav", { className: "nav flex-column nav-pills manager-side-nav" }, h("button", { className: `nav-link text-start ${managerPanel === "roles" ? "active" : ""}`, onClick: () => setManagerPanel("roles") }, "Role permissions"), h("button", { className: `nav-link text-start ${managerPanel === "users" ? "active" : ""}`, onClick: () => setManagerPanel("users") }, "User role assignment"), h("button", { className: `nav-link text-start ${managerPanel === "manage-users" ? "active" : ""}`, onClick: () => setManagerPanel("manage-users") }, "User management"))), h("div", { className: "col-md-9" }, managerPanel === "roles" ? h(RoleManager, { admin, headers, reload }) : managerPanel === "users" ? h(UserRoleManager, { admin, headers, reload }) : h(UserManagement, { admin, headers, reload }))));
  const homeView = activePage === "home" && h("section", { className: "content" }, h("h2", null, "Private live watchlist"), h("div", { className: "table" }, quotes.map(quote => h("article", { className: "row", key: quote.symbol }, h("div", { className: "company" }, h("b", null, quote.symbol), h("small", null, quote.name)), h("strong", { className: "price" }, money.format(quote.price)), h("span", { className: quote.change >= 0 ? "positive" : "negative" }, `${quote.change >= 0 ? "+" : ""}${quote.change_percent}%`)))));
  const tradeView = activePage === "trade" && h(Trading, { quotes, headers, onLogout });
  const historyView = activePage === "history" && h(History, { headers, onLogout });
  const assistantView = activePage === "assistant" && h(Assistant, { headers, onLogout });
  return h("div", { className: "shell" }, h("header", { className: "site-header" }, h("div", { className: "brand" }, h("span", { className: "mark" }, "P"), "pulse markets"), h("nav", { className: "top-nav" }, h("button", { className: activePage === "home" ? "nav-active" : "", onClick: () => onNavigate("home") }, "Market dashboard"), h("button", { className: activePage === "trade" ? "nav-active" : "", onClick: () => onNavigate("trade") }, "Trade"), h("button", { className: activePage === "history" ? "nav-active" : "", onClick: () => onNavigate("history") }, "History"), h("button", { className: activePage === "assistant" ? "nav-active" : "", onClick: () => onNavigate("assistant") }, "Assistant"), manager && h("button", { className: activePage === "management" ? "nav-active" : "", onClick: () => onNavigate("management") }, "Management")), h("div", { className: "account" }, h("span", null, user.email), h("button", { className: "text-button", onClick: onLogout }, "Sign out"))), h("section", { className: "hero compact" }, h("p", { className: "eyebrow" }, activePage === "management" ? `${master ? "MASTER" : "ADMIN"} CONTROL CENTER` : activePage === "assistant" ? "READ-ONLY INSIGHTS" : activePage === "trade" ? "PAPER TRADING" : activePage === "history" ? "ACTIVITY" : "MARKET DASHBOARD"), h("h1", { style: ["management", "history", "assistant"].includes(activePage) ? { fontSize: "clamp(36px, 5vw, 56px)", letterSpacing: "-2.5px" } : null }, activePage === "management" ? "Administration" : activePage === "assistant" ? "Portfolio assistant" : activePage === "trade" ? "Buy and sell with confidence." : activePage === "history" ? "Transaction history" : "Your market, in motion."), ["management", "history", "assistant"].includes(activePage) && h("p", { style: { color: "#71807a", margin: "14px 0 0", fontSize: "16px" } }, activePage === "management" ? "Manage users, roles, and private API access." : activePage === "assistant" ? "Ask questions about your saved portfolio and activity." : "Review your completed paper trades.")), managementView, homeView, tradeView, historyView, assistantView);
}

function App() { const hashPage = () => location.hash === "#management" ? "management" : location.hash === "#trade" ? "trade" : location.hash === "#history" ? "history" : location.hash === "#assistant" ? "assistant" : "home"; const [user, setUser] = useState(null), [page, setPage] = useState(hashPage); function navigate(next) { location.hash = next === "management" ? "management" : next === "trade" ? "trade" : next === "history" ? "history" : next === "assistant" ? "assistant" : ""; setPage(next); } function login(user) { location.hash = ""; setPage("home"); setUser(user); } return user ? h(Dashboard, { user, page, onNavigate: navigate, onLogout: () => { localStorage.removeItem("pulse-token"); setUser(null); } }) : h(Auth, { onLogin: login }); }
createRoot(document.getElementById("root")).render(h(App));
