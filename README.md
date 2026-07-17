# PayEnvelope

**Every Salary Already Has a Job.**

A deterministic, rule-based salary-budgeting Progressive Web App. Payslip
parsing and envelope allocation run on regular expressions, arithmetic, and
configurable rules — **no machine learning or AI anywhere in this codebase.**

## What's in this project

```
payenvelope/
  frontend/     A real, installable, offline-capable PWA (vanilla JS + IndexedDB)
  backend/      A real, tested FastAPI service with the same parsing/allocation logic in Python
  netlify.toml  Netlify deployment config for the frontend
  render.yaml   Render deployment config for the backend + Postgres
```

**Ready to deploy:** see [`DEPLOYMENT.md`](./DEPLOYMENT.md) for step-by-step
instructions to put the frontend on Netlify and the backend on Render.

Both halves run the *same* deterministic engine — the frontend's `js/parser.js`
and `js/envelope-engine.js` are line-for-line ports of the backend's
`app/parser.py` and `app/envelope_engine.py`, verified against each other with
matching test cases (17 passing pytest cases + Node parity checks).

### Frontend (`frontend/`) — try it now

Fully working offline. To run it:

```bash
cd frontend
python3 -m http.server 8080
# open http://localhost:8080 — install it from the browser's install prompt
```

What it does, for real: register a local account, paste a payslip (two
realistic Nigerian samples are worth trying), watch it parse into gross/net
with a validation badge, confirm to auto-create and fund envelopes, log
transactions against them, lock/merge/split/transfer between envelopes, and
pull up cash-flow and budget-performance reports — all persisted in IndexedDB
so it survives a refresh and works with the network off. Verified with an
automated Playwright walkthrough (register → parse → allocate → transact →
report) and an offline-reload test, both passing with zero console errors of
its own (the only network error you'll see is Google Fonts being blocked in
this sandbox — it resolves normally on the open internet).

### Backend (`backend/`) — the same logic, server-side

A real FastAPI service (not scaffolding) with JWT auth, SQLAlchemy models,
and REST endpoints for payslips/envelopes/transactions/reports. Swagger docs
at `/docs`. See `backend/README.md` for setup and what's a genuine
integration point (OAuth, payment providers, push) versus fully implemented.

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Design

Dark glassmorphism fintech aesthetic per the brief's exact palette
(`#6366F1` primary, `#8B5CF6` secondary, `#080C14` background, etc.),
Plus Jakarta Sans for numbers/headings, Inter for body text. The signature
visual motif — every budgeting bucket rendered as a small paper envelope
with a folded flap (`.envelope-card` in `frontend/css/styles.css`) — ties
the UI directly back to the product's name and metaphor rather than using
generic cards.

## What was deliberately scoped out (and why)

This was built as a working PWA + a working backend, not a deployed
multi-service platform — some brief items need infrastructure this sandbox
can't provision (a live Postgres/Redis cluster, real Paystack/Firebase/OAuth
credentials, CI runners). Those are documented as clear integration points
in `backend/README.md` rather than faked. Everything else — the parsing
engine, the envelope engine, the UI, persistence, and offline support — is
real, runs, and is tested.
