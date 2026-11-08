# OrderBridge: running and deploying

- **GitHub:** _to be added when the repository is published_
- **Live app:** _to be added when deployed_

OrderBridge has two parts: a FastAPI backend with PostgreSQL (`backend/`) and
a Next.js frontend (`frontend/`). The browser talks only to the frontend's own
origin; Next.js forwards `/api/*` to the backend, so the session cookie is
first-party (HttpOnly, Secure in production) and the browser needs no CORS.

## 1. Run it locally

### Prerequisites

- Docker (for PostgreSQL 17)
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
   uv run python seed.py            # the 3 client products, 3 retailers, the desk user
   uv run python seed_demo.py       # optional: 4 demonstration orders (see below)
   uv run uvicorn app.main:app --reload --port 8000
   ```

4. **Frontend** (in `frontend/`, a second terminal):

   ```bash
   npm ci
   npm run dev                      # http://localhost:3000
   ```

5. **Sign in** at <http://localhost:3000> with a demo number (section 4). In
   development the one-time code is printed in the backend terminal and
   appended to `.dev-otp-outbox.jsonl` in the repository root.

### Demonstration orders (optional)

`seed_demo.py` places four orders through the real services so the desk queue,
credit holds and Tally export are not empty: a pending order for Gupta Kirana
Store (free goods and the 3% slab discount), a confirmed order for Patel
Provision Store (waiting for the Tally export), a Patel order that the desk's
confirmation puts on credit hold, and a rejected order. It is idempotent.
Sharma General Store is left without orders, so the brief's sample message is
matched without order history.

## 2. Run the tests

**Backend** (needs a separate test database whose name ends in `_test`):

```bash
docker exec -it orderbridge-db psql -U <POSTGRES_USER> -d postgres -c "CREATE DATABASE orderbridge_test"
cd backend
DATABASE_URL='<your TEST_DATABASE_URL>' uv run alembic upgrade head
uv run pytest          # about 990 tests
uv run ruff check
uv run ruff format --check
```

Tests never fall back to `DATABASE_URL`, and they refuse to run against a
database that looks like the development one.

**Frontend** (in `frontend/`):

```bash
npm test               # Vitest + Testing Library, about 560 tests
npm run typecheck
npm run lint
BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build
```

**End to end** (Playwright, against the running backend and frontend):

```bash
npx playwright install chromium     # once
E2E_OTP_OUTBOX=../.dev-otp-outbox.jsonl npm run test:e2e
```

The end-to-end tests sign in with real one-time codes read from the local
outbox. They create and discard a message capture but never create or confirm
orders. See `frontend/e2e/README.md` for the per-number code request limit.

## 3. How it is deployed

_Planned; this section will be confirmed with the live URLs once deployed._

| Part | Host | Notes |
|---|---|---|
| Database | Managed PostgreSQL 17 (for example Render PostgreSQL) | |
| Backend | Render web service, root `backend/` | Build `uv sync --frozen`; pre-deploy `uv run alembic upgrade head && uv run python seed.py && uv run python seed_demo.py`; start `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-proxy-headers` |
| Frontend | Vercel, root `frontend/` | `BACKEND_ORIGIN` set to the backend's public origin at build time |

Production settings: `APP_ENV=production` (API docs are then disabled),
`AUTH_SESSION_COOKIE_SECURE=true`, `AUTH_DEBUG_OTP=false`, no outbox path,
`FRONTEND_ORIGINS` set to the Vercel origin (https only). The backend refuses
to start with unsafe production settings (for example a placeholder secret or
an http origin).

**Sign-in without SMS.** No SMS provider is integrated. For the review
deployment, set `AUTH_DEMO_OTP_NUMBERS` to the four demo numbers: for those
accounts only, the sign-in page then shows the freshly issued code with a
"Demo account" label. Codes stay random, single use, expiring, rate-limited
and subject to lockout; any other number receives no code. A real deployment
needs an SMS provider and an empty `AUTH_DEMO_OTP_NUMBERS`.

## 4. Demo logins

| Role | Mobile number | Account |
|---|---|---|
| Retailer | 9000000001 | Sharma General Store |
| Retailer | 9000000002 | Gupta Kirana Store |
| Retailer | 9000000003 | Patel Provision Store |
| Order desk | 9000000010 | Order Desk Operator |

All four are synthetic accounts from `seed.py`. Locally the code is in the
backend terminal and the outbox file; on the review deployment it is shown on
the sign-in page for these numbers.

### A short walkthrough

1. **Retailer (9000000001):** browse the catalogue (case and unit prices,
   schemes, the out-of-stock tea), add 10 cases of biscuit and 5 of detergent,
   see the estimated subtotal, and submit the order.
2. **Retailer:** Message capture, paste
   `bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti`,
   and review the three lines: original words, heuristic match scores, two
   alternatives, out-of-stock tea; confirm "peti" as cases, include biscuit
   and detergent, exclude the tea with a reason, and create the order.
3. **Order desk (9000000010):** at-a-glance counts, the order queue, review an
   order at current prices, confirm it (or see a credit hold), reject one with
   a reason, and export confirmed orders to the Tally CSV (downloads are
   checked against their SHA-256).

## 5. Environment variables (names only)

**Backend** (`.env` in the repository root; all documented in `.env.example`):

`APP_ENV`, `APP_NAME`, `APP_VERSION`, `DATABASE_URL`, `TEST_DATABASE_URL`
(tests only), `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (Docker
Compose only), `AUTH_SECRET_KEY`, `AUTH_OTP_TTL_SECONDS`,
`AUTH_OTP_MAX_ATTEMPTS`, `AUTH_OTP_RESEND_COOLDOWN_SECONDS`,
`AUTH_OTP_LOCKOUT_THRESHOLD`, `AUTH_OTP_LOCKOUT_WINDOW_SECONDS`,
`AUTH_SESSION_TTL_SECONDS`, `AUTH_SESSION_COOKIE_NAME`,
`AUTH_SESSION_COOKIE_SECURE`, `AUTH_SESSION_COOKIE_SAMESITE`,
`AUTH_DEBUG_OTP`, `AUTH_DEV_OTP_OUTBOX_PATH`, `AUTH_DEMO_OTP_NUMBERS`,
`FRONTEND_ORIGINS`, `RATE_LIMIT_OTP_REQUEST_IP_LIMIT`,
`RATE_LIMIT_OTP_REQUEST_IP_WINDOW_SECONDS`,
`RATE_LIMIT_OTP_REQUEST_PHONE_LIMIT`,
`RATE_LIMIT_OTP_REQUEST_PHONE_WINDOW_SECONDS`,
`RATE_LIMIT_OTP_VERIFY_IP_LIMIT`, `RATE_LIMIT_OTP_VERIFY_IP_WINDOW_SECONDS`,
`RATE_LIMIT_OTP_VERIFY_PHONE_LIMIT`,
`RATE_LIMIT_OTP_VERIFY_PHONE_WINDOW_SECONDS`,
`RATE_LIMIT_CLEANUP_PROBABILITY`, `TRUSTED_PROXY_CIDRS`,
`MAX_REQUEST_BODY_BYTES`.

**Frontend:** `BACKEND_ORIGIN` (server-side, read at build time).

**End-to-end tests:** `E2E_OTP_OUTBOX`, `E2E_BASE_URL` (optional).

## 6. Known limitations

- **No SMS provider.** Codes reach the backend console and outbox locally, and
  the sign-in page for listed demo accounts on the review deployment.
- **Message capture is rule-based, not an AI model.** A deterministic
  interpreter and matcher with a heuristic score; photo and Hindi voice
  capture and language-model interpretation are not built.
- **Interface in English only.** Messages in Hinglish and some Devanagari
  words are understood by capture, but the screens are not translated.
- **Per-IP rate limits behind proxies.** Requests reach the backend through
  the Vercel proxy, so per-IP limits apply per proxy address unless
  `TRUSTED_PROXY_CIDRS` lists the proxies; per-number limits always apply.
- **Not built (future scope):** salesman offline mode, van loading lists, the
  14-day inactive retailer dashboard, credit-hold release, delivery tracking,
  payments and GST e-invoicing (the last three are out of scope in the brief).
