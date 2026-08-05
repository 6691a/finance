---
name: kis-api-reference
description: Use when the user asks about the 한국투자증권, 한투, or KIS Open API, including authentication, tokens, TR_ID values, domestic or US stock quotes, orders, balances, realtime WebSocket trades or order books, candle or period queries, endpoints, headers, parameters, or response fields.
---

# KIS (한국투자증권) API Reference

KIS API details vary by market, exchange, environment, and endpoint. Verify the
answer against the `main` branch of the official
[`koreainvestment/open-trading-api`](https://github.com/koreainvestment/open-trading-api)
repository before responding.

## Find the authoritative example

Treat `examples_llm/` as the primary reference. Each API normally has two files:

- `<name>.py`: request URL, TR_ID, headers, and parameters
- `chk_<name>.py`: invocation and expected response fields

Use the folder matching the asset class:

| Topic | Folder |
|---|---|
| Token, WebSocket `approval_key`, hashkey | `examples_llm/auth/` |
| Korean stocks | `examples_llm/domestic_stock/` |
| US and other overseas stocks | `examples_llm/overseas_stock/` |
| Korean bonds | `examples_llm/domestic_bond/` |
| Korean futures and options | `examples_llm/domestic_futureoption/` |
| Overseas futures and options | `examples_llm/overseas_futureoption/` |
| ELW, ETF, and ETN | `examples_llm/elw/`, `examples_llm/etfetn/` |

Filename hints include `asking_price` for order books, `ccnl` for trades,
`dailyprice` or `inquire_daily` for daily prices, and `order` or `balance` for
orders and balances. For Korean stocks, distinguish `_krx`, `_nxt`, and
`_total`. Realtime WebSocket examples are located in the corresponding asset
folder.

If filename search is insufficient, consult `MCP/data.csv` for the API index and
`docs/convention.md` for naming conventions.

## Extract before answering

Verify and report:

- the exact example file and link
- REST URL path or WebSocket TR_ID
- required headers, including `tr_id`, `custtype`, `tr_cont`, or `approval_key`
- query or body parameter names and coded values
- response field names and realtime message layout
- differences between production and paper trading

Do not infer these values from memory.

## Fit the answer to this project

This repository uses learning mode. Explain the design and verified mapping,
but do not implement function bodies, client logic, classes, or algorithms
unless the user explicitly requests implementation.

Point to the appropriate location under `app/kis/`, such as `korea/`,
`overseas/`, `websocket/`, or `schemas/`. Recommend an `Enum` for coded value
sets and a Pydantic `BaseModel` for grouped parameters.

## Avoid common errors

- Do not confuse KRX, NXT, and integrated (`total`) APIs.
- Do not use a REST access token where WebSocket `approval_key` is required.
- Do not omit `tr_cont` when the verified example requires continuation.
- Confirm whether the user is using production or paper trading before choosing
  environment-specific TR_ID values or base URLs.
