# Small LLM intent routing

## Purpose

Use a small local LLM only to understand a customer's wording and select a safe response type. It does not calculate money, query the database, or place orders.

This improves on fragile keyword rules. For example, it should distinguish:

- "Show all trades I sold" -> sell history
- "Should I sell AAPL?" -> analysis question

## Request flow

```text
Customer question
        |
        v
Small local LLM: classify intent as validated JSON
        |
        +-- portfolio_performance -> FastAPI calculates P/L and renders tables
        +-- sell_history          -> FastAPI reads sell rows and renders a table
        +-- transaction_history   -> FastAPI reads transaction rows and renders a table
        +-- general_analysis      -> main local LLM writes an answer from read-only context
```

The data tables always come from FastAPI and PostgreSQL. The small LLM only chooses the route.

## Required JSON response

```json
{"intent":"sell_history"}
```

Allowed values:

- `portfolio_performance`
- `sell_history`
- `transaction_history`
- `general_analysis`

If the response is invalid, unknown, empty, or times out, use `general_analysis` or ask the customer to clarify. Never execute a trade from an intent result.

## Suggested classifier instruction

```text
Classify the customer's request into exactly one intent. Return JSON only:
portfolio_performance, sell_history, transaction_history, or general_analysis.

sell_history means the customer wants completed past sell transactions.
transaction_history means the customer wants past trades generally.
portfolio_performance means the customer wants value, profit, loss, return, or holdings performance.
general_analysis covers advice, explanations, questions about whether to buy or sell, and anything else.
```

## Safety rules

- Validate the JSON with a strict server-side schema.
- Use the authenticated customer's data only.
- Do not trust the model with calculations or permissions.
- Keep paper-order creation behind the existing explicit proposal and confirmation flow.
- Log only the selected intent and normal application audit data; do not store secrets.
