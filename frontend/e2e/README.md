# End-to-end tests

Playwright tests in this folder drive the real application: the Next.js
frontend, the `/api` proxy and the FastAPI backend with its seeded demo
accounts. Nothing is mocked, so they are the evidence that sign-in,
session cookies and role checks actually work together.

## Prerequisites

1. PostgreSQL running, migrations applied and demo data seeded (see the
   backend instructions).
2. Backend running on `http://127.0.0.1:8000` with, in the repository root
   `.env`:
   - `APP_ENV=development` and `AUTH_DEBUG_OTP=true`
   - `AUTH_DEV_OTP_OUTBOX_PATH=.dev-otp-outbox.jsonl` (the development OTP
     outbox; relative to the repository root, gitignored)
   - `FRONTEND_ORIGINS=["http://localhost:3000"]`
   - `AUTH_SESSION_COOKIE_SECURE=false` (local HTTP)

   Restart the backend after changing `.env`.
3. Frontend running: `npm run dev`.
4. Browsers installed once: `npx playwright install chromium`.

## Run

```bash
E2E_OTP_OUTBOX=../.dev-otp-outbox.jsonl npm run test:e2e
```

`E2E_OTP_OUTBOX` must name the same file as the backend's
`AUTH_DEV_OTP_OUTBOX_PATH`: an absolute path, or one relative to this
`frontend` directory (Playwright runs from there; the backend resolves its
relative path from the repository root, hence `../`).

## How the tests get one-time codes

Codes are never returned over HTTP or shown in the page. The backend appends
each issued code, in development only, to the outbox file (one JSON line:
number, code, UTC time, file mode 0600). `e2e/otp-outbox.ts` reads it in the
Playwright test process (never in the browser) after the page reaches the
code step, and takes the newest unexpired code for that number. Codes are
typed into the page but never printed.

Without `E2E_OTP_OUTBOX`, the sign-in tests are skipped with that reason;
the signed-out redirect test still runs.

Unit tests for the reader (`e2e/otp-outbox.test.ts`) run with `npm test`.

The order desk test also opens `/desk/exports` read-only (the waiting
orders and export history load through the proxy). It never creates an
export, because that would change seeded orders to Exported. It reuses the
desk sign-in rather than signing in again, to stay within the code request
limit below.

## Full order path (opt-in, changes data)

`order-flow.spec.ts` drives the whole path in a real browser: a retailer
captures a message and creates an order, then the order desk confirms it
and exports it to the Tally CSV. It creates, confirms and exports an order,
so it is skipped unless `E2E_ORDER_FLOW=1`. Run it only against a
disposable database that has just been migrated and seeded with `seed.py`
alone (no `seed_demo.py`), with the backend and frontend pointed at it,
never against the development or production database:

```bash
E2E_ORDER_FLOW=1 E2E_OTP_OUTBOX=../.dev-otp-outbox.jsonl npx playwright test e2e/order-flow.spec.ts
```

`E2E_BASE_URL` overrides the frontend URL (default `http://localhost:3000`).
Use `localhost`, not `127.0.0.1`: the browser's `Origin` must match
`FRONTEND_ORIGINS` exactly, or the backend rejects sign-in with 403.

## Limits to keep in mind when rerunning

- **Resend cooldown, 60 seconds per number.** A rerun within a minute issues
  no new code; the previous, still-valid code is read from the outbox
  instead, so reruns work.
- **Rate limit: 5 code requests per number per hour** (and 30 per client IP
  per 15 minutes, 15 verifications per number per hour). Each run requests
  one code per demo account, so more than about five runs in an hour get
  HTTP 429 at the code step. Wait for the window to pass, or raise
  `RATE_LIMIT_OTP_REQUEST_PHONE_LIMIT` in your local `.env`.
- **Cumulative lockout: 10 failed codes per number per 24 hours.** The
  wrong-code test enters one wrong code and then signs in with the right
  one, which clears the failures, so repeated runs do not lock the account.

Playwright traces kept for failed tests (`trace: "retain-on-failure"`)
record typed input, including the one-time code. They stay on your machine
under `test-results/`; the codes expire after 5 minutes.
