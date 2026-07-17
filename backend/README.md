# Silo — Backend

A deterministic, rule-based salary-budgeting API. **No machine learning or AI
is used anywhere in this service** — payslip parsing is regular expressions
and arithmetic, and envelope allocation is configurable percentage/fixed/
remainder rules.

## What's implemented vs. what's a stub

This backend is real, runnable, tested code — not scaffolding. What's fully
implemented:

- **Payslip parser** (`app/parser.py`) — regex-based extraction of employee
  name, employer, earnings, statutory deductions, gross/net salary, with
  country-aware label sets for Nigeria (default), Ghana, Kenya, South Africa,
  and a reconciliation check that flags mismatched figures for review. It
  also lifts out any other "Label: Amount" line on the payslip (e.g. Rent,
  Clothing, Health) verbatim, as `line_items` — see below.
- **Envelope engine** (`app/envelope_engine.py`) — percentage/fixed/remainder
  allocation rules, plus create/rename/delete/archive/lock/merge/split/
  transfer operations.
- **Payslip → envelope split auto-generation** (`routers/payslips.py`) —
  parsing a payslip automatically (re)creates the user's active
  `EnvelopeRule` set and matching `Envelope` rows using the *exact category
  names found on the payslip* (Rent, Clothing, Health, ...), each weighted as
  a percentage of net salary. Uploading a new payslip supersedes the
  previous split.
- **Payment API integration** (`app/payments.py`, `routers/payments.py`) —
  `POST /payments/accounts` creates a Paystack dedicated virtual account
  (a real NUBAN account number) for the logged-in user via the Paystack REST
  API. `POST /payments/webhook/paystack` verifies Paystack's HMAC-SHA512
  webhook signature and, on `charge.success`, splits the incoming amount
  across that user's *currently active* envelope split — i.e. whatever was
  generated from their most recently uploaded payslip — crediting each
  envelope and logging a transaction, idempotently (safe against webhook
  redelivery).
- **Admin** (`app/auth.py: get_current_admin`, `routers/admin.py`) — a
  `User.is_admin` flag gates `/admin/*` (user list/search, activate/
  deactivate, promote/demote, platform stats, payment-event audit log).
  "Admin login" is the same `/auth/login` as everyone else; there's no
  parallel auth system. Bootstrap the first admin either by setting
  `ADMIN_BOOTSTRAP_EMAILS` before that account registers, or by running
  `python -m scripts.create_admin <email> --password "..."` against a fresh
  or existing account.
- **REST API** (FastAPI) — auth (register/login/me via JWT), payslip parsing
  + persistence, envelope CRUD + lifecycle actions, transactions with
  filtering, and report endpoints (cash flow, expense categories, envelope
  allocation, budget performance). Auto-generated Swagger docs at `/docs`.
- **Database models** (SQLAlchemy) — users, payslips, envelopes, envelope
  rules, transactions, savings goals, bills, notifications, subscriptions,
  payment accounts, payment events, audit logs.
- **Tests** — pytest cases covering the parser, envelope engine, payment
  split logic, and full register → login → parse-payslip → envelope-split →
  payment-webhook flows end-to-end
  (`tests/test_parser.py`, `tests/test_payslip_line_items.py`,
  `tests/test_envelope_engine.py`, `tests/test_payments.py`,
  `tests/test_integration_flows.py`).

What's a documented integration point, not implemented (these require live
third-party credentials/infrastructure this environment can't provision):

- OAuth (Google/Microsoft/Apple) and magic-link login — `auth.create_access_token`
  is ready to be called from an OAuth callback route once you register a
  provider app.
- Live Paystack credentials — the virtual-account and webhook code is real
  and tested against Paystack's documented API/webhook contract, but needs a
  real `PAYSTACK_SECRET_KEY` (and, in production, a registered webhook URL in
  the Paystack dashboard) to talk to the live API. Without a key configured,
  `POST /payments/accounts` returns a clear `502` rather than failing silently.
- Push notifications (FCM/OneSignal) — `models.Notification` stores in-app
  notifications now; hook a provider SDK into the same creation path to also
  push externally.
- Redis caching — the app runs fine without it (SQLite/Postgres only); add a
  cache layer in front of the report endpoints if you need it at scale.

## Running locally

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for interactive Swagger docs.

By default this uses a local SQLite file (`silo.db`) so it runs with
zero setup. For Postgres, set `DATABASE_URL`:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/silo"
```

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | local SQLite file |
| `JWT_SECRET_KEY` | Signs auth tokens — **set a real secret in production** | dev-only default |
| `PAYSTACK_SECRET_KEY` | Enables `/payments/accounts` and webhook verification | unset — endpoint returns 502 until set |
| `PAYSTACK_PREFERRED_BANK` | Partner bank for dedicated virtual accounts | `wema-bank` |
| `ADMIN_BOOTSTRAP_EMAILS` | Comma-separated emails auto-promoted to admin on registration | unset |

### Creating the first admin account

```bash
cd backend
python -m scripts.create_admin admin@yourcompany.com --password "ChangeMe123!" --name "Admin"
```

Re-running this against an existing email promotes that account to admin
instead of creating a duplicate.

## Running tests

```bash
pytest tests/ -v
```

## Project layout

```
backend/
  app/
    main.py              FastAPI app, CORS, rate limiting, router registration
    parser.py             Deterministic payslip parsing engine (+ line-item extraction)
    envelope_engine.py     Rule-based envelope allocation + lifecycle ops
    payments.py             Paystack integration: virtual accounts, webhook verify, split math
    models.py              SQLAlchemy ORM models
    schemas.py              Pydantic request/response schemas
    database.py            Engine/session config (Postgres or SQLite)
    auth.py                 Password hashing (bcrypt) + JWT issuance/verification + admin gate
    routers/
      auth.py               /auth/register, /auth/login, /auth/me
      payslips.py            /payslips/parse, /payslips/ (also auto-generates the envelope split)
      envelopes.py            /envelopes CRUD + lock/archive/merge/split/transfer + /envelopes/rules
      transactions.py          /transactions with filtering
      reports.py                /reports/cash-flow, expense-categories, etc.
      payments.py                /payments/accounts, /payments/webhook/paystack
      admin.py                    /admin/users, /admin/stats, /admin/payment-events
  scripts/
    create_admin.py         CLI to bootstrap/promote an admin account
  tests/
    test_parser.py
    test_payslip_line_items.py
    test_envelope_engine.py
    test_payments.py
    test_integration_flows.py
  requirements.txt
```

## Security notes

- Passwords are hashed with bcrypt directly (not via passlib's CryptContext,
  which has known incompatibilities with recent bcrypt releases).
- Raw payslip text is never stored — only a SHA-256 hash, matching the
  product's "purge raw document" privacy commitment.
- JWTs expire after 7 days; set `JWT_SECRET_KEY` in production — the default
  in `auth.py` is for local development only.
- Paystack webhooks are verified via HMAC-SHA512 signature before any data is
  trusted (`payments.verify_webhook_signature`); unsigned or badly-signed
  requests are rejected with 401.
- `/admin/*` routes require `is_admin=True` on the authenticated user
  (`auth.get_current_admin`); there is no separate admin credential store.
