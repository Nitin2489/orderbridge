# OrderBridge

Ordering and order operations for an FMCG distributor: retailers order from
a catalogue with their own prices and schemes, messages such as WhatsApp
orders are captured and reviewed line by line, and the order desk confirms
orders (with stock and credit checks) and exports them to Tally.

Built for the IsoCode Labs assignment (client brief: `docs/assignment-brief.md`).

## What works

- **Retailers:** OTP sign-in, catalogue with their case and unit prices,
  stock and schemes, a cart with an exact estimated subtotal, order
  submission, order history and progress, repeat orders, message capture.
- **Order desk:** queue with at-a-glance counts, order review at current
  prices, confirmation with stock deduction and credit holds, rejection,
  message capture review with retailer aliases, Tally CSV export with
  checksum-verified downloads.
- **Scheme engine:** buy 10 get 1 free and slab discounts, priced only by the
  backend with decimal arithmetic.
- **Message capture:** deterministic rule-based interpretation and matching
  (not an AI model). Every line keeps its original words, shows a heuristic
  match score and two alternatives, asks for the unit, and needs a person's
  decision before a pending order is created.

The status of every requirement is in `docs/requirements-tracker.md`.

## Repository

| Path | What |
|---|---|
| `backend/` | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17; services, API, tests |
| `frontend/` | Next.js 16, React 19, TypeScript, Tailwind CSS v4, TanStack Query; Vitest and Playwright tests |
| `docs/` | Client brief, requirements tracker, technical design |
| `deployment.md` | Local setup, tests, deployment, environment variables, demo logins |

## Quick start

See `deployment.md` for the full steps. In short:

```bash
cp .env.example .env                 # then set the secrets it asks for
docker compose up -d db
cd backend && uv sync && uv run alembic upgrade head && uv run python seed.py
uv run uvicorn app.main:app --reload --port 8000
cd ../frontend && npm ci && npm run dev
```

Sign in at http://localhost:3000 as retailer 9000000001 or the order desk
9000000010; in development the code is printed in the backend terminal. On
the review deployment, which has no SMS provider, the codes of the four demo
accounts are shown only after **Reviewer access** is unlocked with a private
key (`AUTH_DEMO_ACCESS_KEY`); see `deployment.md`.
