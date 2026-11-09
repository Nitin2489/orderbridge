# OrderBridge: development and deployment

- **Live app:** <https://orderbridge-ten.vercel.app>
- **Backend API:** <https://orderbridge-vaia.onrender.com> (health check:
  `/health`)

OrderBridge has two parts: a FastAPI backend with PostgreSQL (`backend/`) and
a Next.js frontend (`frontend/`). The browser talks only to the frontend's own
origin; Next.js forwards `/api/*` to the backend, so the session cookie is
first-party (HttpOnly, Secure in production) and the browser needs no CORS.

## 1. Run it locally

### Prerequisites

- Docker (runs PostgreSQL 17 for local development, from
  `docker-compose.yml`; the deployed database is PostgreSQL 18, see
  section 3)
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20.9+ and npm

### Steps

1. **Configuration.** From the repository root:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`: set `POSTGRES_PASSWORD` and the matching password in
   `DATABASE_URL` and `TEST_DATABASE_URL`, and set `AUTH_SECRET_KEY` to a
   random value of at least 32 characters, for example the output of
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Keep
   `APP_ENV=development`, `AUTH_DEBUG_OTP=true` and
   `AUTH_SESSION_COOKIE_SECURE=false` for local HTTP. Never commit `.env`.

2. **Database.**

   ```bash
   docker compose up -d db
   ```

3. **Backend** (in `backend/`):

   ```bash
   uv sync
   uv run alembic upgrade head      # create the schema
   uv run python seed.py            # 3 sample products, 3 retailers, the desk user
   uv run python seed_demo.py       # optional: 4 demonstration orders (see below)
   uv run uvicorn app.main:app --reload --port 8000
   ```

4. **Frontend** (in `frontend/`, a second terminal):

   ```bash
   npm ci
   npm run dev                      # http://localhost:3000
   ```

5. **Sign in** at <http://localhost:3000> with the mobile number of one of
   the synthetic accounts created by `seed.py`. In development the one-time
   code is printed in the backend terminal and, with
   `AUTH_DEV_OTP_OUTBOX_PATH` set, appended to `.dev-otp-outbox.jsonl` in
   the repository root.

### Demonstration orders (optional)

`seed_demo.py` places four orders through the real services so the desk queue,
credit holds and Tally export are not empty: a pending order for Gupta Kirana
Store (free goods and the 3% slab discount), a confirmed order for Patel
Provision Store (waiting for the Tally export), a Patel order that the desk's
confirmation puts on credit hold, and a rejected order. It is idempotent.
Sharma General Store is left without orders, so a sample message is matched
without order history.

### Message capture with Gemini (optional)

Capture uses deterministic rules by default (`CAPTURE_INTERPRETER=rules`).
To try the Gemini reader, set `CAPTURE_INTERPRETER=gemini` and
`GEMINI_API_KEY` (a Google AI Studio key) in `.env`, and restart the
backend. Gemini only proposes lines: every phrase is checked against the
original message, quantities and units are read by the rules, products are
matched by OrderBridge's own matcher, and any failure (timeout, error,
invalid output) falls back to the rules. Only the message text and the
products' codes and names are sent; no retailer, phone number, price or
order history.

### Tally XML (optional)

The order desk's export page always offers the Tally CSV. The TallyPrime XML
voucher download additionally needs the exact names of ledgers that already
exist in your Tally company: `TALLY_SALES_LEDGER_NAME`,
`TALLY_DISCOUNT_LEDGER_NAME` (only for batches with a discount) and
`TALLY_VOUCHER_TYPE_NAME` (default `Sales`). Without them the page says what
is missing. The XML has not been verified against a live Tally company.

## 2. Run the tests

**Backend** (needs a separate test database whose name ends in `_test`):

```bash
docker exec -it orderbridge-db psql -U <POSTGRES_USER> -d postgres -c "CREATE DATABASE orderbridge_test"
cd backend
DATABASE_URL='<your TEST_DATABASE_URL>' uv run alembic upgrade head
uv run pytest          # 1,167 tests
uv run ruff check
uv run ruff format --check
```

Tests never fall back to `DATABASE_URL`, and they refuse to run against a
database that looks like the development one. Gemini is never called by
the tests; its responses are stubbed.

**Frontend** (in `frontend/`):

```bash
npm test               # Vitest + Testing Library, 618 tests
npm run typecheck
npm run lint
BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build
```

**End to end** (Playwright, against the running backend and frontend):

```bash
npx playwright install chromium     # once
E2E_OTP_OUTBOX=../.dev-otp-outbox.jsonl npm run test:e2e
```

Five tests cover signing in with real one-time codes read from the local
outbox, a wrong code, session reload, role isolation, sign-out and
protected pages, and creating and discarding a message capture. They do not
create or confirm orders.

A sixth test, `e2e/order-flow.spec.ts`, runs the whole order path in a real
browser: capture, review, order creation, desk confirmation and Tally CSV
export. It changes data, so it is skipped unless `E2E_ORDER_FLOW=1`, and it
must only run against a disposable database that has just been migrated and
seeded with `seed.py` alone. See `frontend/e2e/README.md` for details and
the per-number code request limit.

## 3. How it is deployed

| Part | Host | Address |
|---|---|---|
| Frontend | Vercel | <https://orderbridge-ten.vercel.app> |
| Backend | Render web service | <https://orderbridge-vaia.onrender.com> |
| Database | Render PostgreSQL 18, Singapore region | Private; reached only by the backend |

Local development uses PostgreSQL 17 in Docker. The same Alembic migrations
create the schema on both versions.

To reproduce it, create the three parts in this order: database, backend,
frontend. Then come back to the backend to set the frontend's address.

### Step 1: Database (Render PostgreSQL)

1. In Render, create a **PostgreSQL** instance: version 18, region
   Singapore.
2. Render shows two connection URLs, as `postgres://…` or
   `postgresql://…`. The backend uses the psycopg 3 driver, so for both change
   only the start to `postgresql+psycopg://…` (same user, password, host and
   database):
   - the **Internal Database URL** becomes the backend's `DATABASE_URL`
     (step 2);
   - the **External Database URL** is used once from your own computer to
     create the schema and load the seed data (step 4).

   Both contain the database password: keep them out of the repository and
   out of `.env.example`.

### Step 2: Backend (Render web service)

Create a **Web Service** from the GitHub repository, in the same region as
the database, with:

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Runtime | Python |
| Build command | `uv sync --frozen` |
| Pre-deploy command | Empty (migrations are run by hand, step 4) |
| Start command | `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-proxy-headers` |
| Health check path | `/health` |

Environment variables, set in Render's environment settings and never in the
repository (all names in section 4):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` (API docs are then disabled) |
| `DATABASE_URL` | the `postgresql+psycopg://…` URL from step 1 |
| `AUTH_SECRET_KEY` | a new random value of at least 32 characters |
| `AUTH_SESSION_COOKIE_SECURE` | `true` |
| `AUTH_DEBUG_OTP` | `false` |
| `FRONTEND_ORIGINS` | `["https://orderbridge-ten.vercel.app"]` (set after step 3) |
| `CAPTURE_INTERPRETER` | `rules`, the default (or `gemini` with `GEMINI_API_KEY`) |
| `AUTH_DEMO_OTP_NUMBERS`, `AUTH_DEMO_ACCESS_KEY` | Optional demo access; leave unset unless needed (see `.env.example`) |

Leave `AUTH_DEV_OTP_OUTBOX_PATH` and `TRUSTED_PROXY_CIDRS` unset. The backend
refuses to start with unsafe production settings (for example a placeholder
secret, an http origin or debug codes switched on).

Generate each secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

No pre-deploy command is configured, so a deploy never changes the database.
When a later change adds a migration, run it by hand (step 4) before
deploying the code that needs it.

### Step 3: Frontend (Vercel)

Import the GitHub repository into Vercel with:

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Framework preset | Next.js (build and output settings left at their defaults) |
| Environment variable | `BACKEND_ORIGIN` = `https://orderbridge-vaia.onrender.com` |

`BACKEND_ORIGIN` is read when the app is built, so redeploy the frontend
after changing it. A production build without it fails on purpose.

Then set `FRONTEND_ORIGINS` on the backend (step 2) to the Vercel address and
let Render redeploy. Without it, sign-in is refused by the backend's origin
check.

### Step 4: Migrate and seed, by hand from your computer

Point the backend's tools at the deployed database through its **External
Database URL**. In `backend/`:

```bash
read -rs DEPLOY_DATABASE_URL     # paste the postgresql+psycopg://… external URL; input is hidden
export DEPLOY_DATABASE_URL

DATABASE_URL="$DEPLOY_DATABASE_URL" uv run alembic upgrade head   # create or update the schema
DATABASE_URL="$DEPLOY_DATABASE_URL" uv run alembic current        # must print the head revision
DATABASE_URL="$DEPLOY_DATABASE_URL" uv run python seed.py         # products, retailers, desk user
DATABASE_URL="$DEPLOY_DATABASE_URL" uv run python seed_demo.py    # the four demonstration orders

unset DEPLOY_DATABASE_URL
```

`DATABASE_URL` given on the command line takes precedence over the one in
your local `.env`, so this never changes the local database's settings and
the deployed URL is not saved in any file.

Seed only once. `seed.py` resets the products' details and stock to the
sample values, so running it again would restore stock that confirmed
orders have already used. `seed_demo.py` is idempotent, but it depends on
`seed.py` having run. Migrations are safe to repeat: `alembic upgrade head`
does nothing when the schema is already at head.

### Step 5: Check it

- `https://orderbridge-vaia.onrender.com/health` returns `healthy`.
- The sign-in page at <https://orderbridge-ten.vercel.app> loads.

**Free-plan behaviour.** On Render's free plan the backend sleeps when idle,
so the first request after a pause can take up to about a minute; reload
once if the first sign-in attempt times out.

## 4. Environment variables (names only)

**Backend** (`.env` in the repository root; all documented in `.env.example`):

`APP_ENV`, `APP_NAME`, `APP_VERSION`, `DATABASE_URL`, `TEST_DATABASE_URL`
(tests only), `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (Docker
Compose only), `AUTH_SECRET_KEY`, `AUTH_OTP_TTL_SECONDS`,
`AUTH_OTP_MAX_ATTEMPTS`, `AUTH_OTP_RESEND_COOLDOWN_SECONDS`,
`AUTH_OTP_LOCKOUT_THRESHOLD`, `AUTH_OTP_LOCKOUT_WINDOW_SECONDS`,
`AUTH_SESSION_TTL_SECONDS`, `AUTH_SESSION_COOKIE_NAME`,
`AUTH_SESSION_COOKIE_SECURE`, `AUTH_SESSION_COOKIE_SAMESITE`,
`AUTH_DEBUG_OTP`, `AUTH_DEV_OTP_OUTBOX_PATH`, `AUTH_DEMO_OTP_NUMBERS`,
`AUTH_DEMO_ACCESS_KEY` (secret), `FRONTEND_ORIGINS`,
`RATE_LIMIT_OTP_REQUEST_IP_LIMIT`,
`RATE_LIMIT_OTP_REQUEST_IP_WINDOW_SECONDS`,
`RATE_LIMIT_OTP_REQUEST_PHONE_LIMIT`,
`RATE_LIMIT_OTP_REQUEST_PHONE_WINDOW_SECONDS`,
`RATE_LIMIT_OTP_VERIFY_IP_LIMIT`, `RATE_LIMIT_OTP_VERIFY_IP_WINDOW_SECONDS`,
`RATE_LIMIT_OTP_VERIFY_PHONE_LIMIT`,
`RATE_LIMIT_OTP_VERIFY_PHONE_WINDOW_SECONDS`,
`RATE_LIMIT_CLEANUP_PROBABILITY`, `TRUSTED_PROXY_CIDRS`,
`MAX_REQUEST_BODY_BYTES`, `CAPTURE_INTERPRETER`, `GEMINI_API_KEY`
(secret), `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`,
`TALLY_SALES_LEDGER_NAME`, `TALLY_DISCOUNT_LEDGER_NAME`,
`TALLY_VOUCHER_TYPE_NAME`.

**Frontend:** `BACKEND_ORIGIN` (server-side, read at build time).

**End-to-end tests:** `E2E_OTP_OUTBOX`, `E2E_BASE_URL` (optional),
`E2E_ORDER_FLOW` (opt-in, data-changing test only).

## 5. Known limitations

- **No SMS provider.** In development, codes reach the backend console and
  the local outbox; a real deployment needs an SMS provider.
- **Capture is text only.** Rules by default; the optional Gemini reader is
  validated and falls back to the rules. Photo and Hindi voice capture are
  not built.
- **Interface in English only.** Messages in Hinglish and some Devanagari
  words are understood by capture, but the screens are not translated.
- **Tally XML is unverified.** It follows Tally's documented import format
  but has not been imported into a live Tally company; the CSV is the
  supported export.
- **Per-IP rate limits share one address behind the proxy.** Requests reach
  the backend through Vercel and then Render's own proxy, and
  `TRUSTED_PROXY_CIDRS` is deliberately left empty: neither proxy's
  forwarding of `X-Forwarded-For` has been verified, and Vercel's outgoing
  addresses are not published, so trusting them would let anyone calling the
  backend directly forge a client address. Forwarded headers are therefore
  ignored and cannot be spoofed, but every visitor shares the per-IP limits
  of the proxy address. It never grants access: per-number limits and
  lockout still apply. The planned fix is authenticated
  proxy forwarding (the Next.js server passes the client IP with a shared
  secret that the backend checks).
- **Not built:** salesman offline mode, van loading lists, an inactive
  retailer dashboard, credit-hold release, delivery tracking, payments and
  GST e-invoicing.
