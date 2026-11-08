# OrderBridge frontend

The web application for OrderBridge: retailer ordering and order-desk operations
for an FMCG distributor. It talks to the FastAPI backend in `../backend` through
a same-origin API proxy.

**Status:** application shell with role-based workspaces and OTP sign-in
(`/sign-in`). Retailers: catalogue (`/retailer`), cart and order submission
(`/retailer/cart`), order history, details and repeat ordering
(`/retailer/orders`) and message capture (`/retailer/capture`). Order desk:
queue with at-a-glance counts and order review (confirm with credit holds,
or reject) at `/desk`, message capture review at `/desk/captures`, and the
Tally CSV export at `/desk/exports`.

Message capture uses deterministic rule-based interpretation and matching
(not an AI model): a person decides every line and its unit before an order
is created, and the order desk confirms it as usual.

The Tally export is a generic, line-level CSV sales register, not a verified
native Tally import: ledgers, GST and voucher types must be mapped in Tally.
Downloads are checked against the SHA-256 the backend recorded for the
batch and are not saved on a mismatch; a browser without Web Crypto (for
example over plain HTTP on a non-localhost address) can only save them
unverified, and the page says so.

## Stack

- Next.js (App Router), React and TypeScript in strict mode
- Tailwind CSS v4 with shadcn/ui components on Radix UI
- TanStack Query for server state
- React Hook Form and Zod for forms and validation (used as forms are added)
- ESLint (flat config with `eslint-config-next`)
- Vitest and React Testing Library (unit and component tests), Playwright
  (end-to-end tests against the running backend)
- npm

## Prerequisites

- Node.js 20.9 or newer (developed with Node.js 22)
- npm 10 or newer
- The backend running locally (see the backend instructions); by default the
  proxy expects it at `http://127.0.0.1:8000`

## Local setup

```bash
cd frontend
npm install                   # creates package-lock.json; commit it
cp .env.example .env.local    # then adjust BACKEND_ORIGIN if needed
npm run dev                   # http://localhost:3000
```

The backend must trust the frontend's origin and, for local HTTP, allow a
non-Secure cookie. In the repository root `.env`:

```
FRONTEND_ORIGINS=["http://localhost:3000"]
AUTH_SESSION_COOKIE_SECURE=false
```

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Development server |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run lint` | ESLint |
| `npm run typecheck` | TypeScript check without emitting files |
| `npm test` | Unit and component tests (Vitest, jsdom; API responses stubbed) |
| `npm run test:e2e` | End-to-end tests against the running stack (see `e2e/README.md`) |

## Environment variables

| Name | Where | Purpose |
|---|---|---|
| `BACKEND_ORIGIN` | Server only, read at build time | Origin of the FastAPI backend the `/api` proxy forwards to. Optional in development (defaults to `http://127.0.0.1:8000`); **required** for production builds, which fail without it. |

No secrets are needed by the frontend, and nothing is exposed to the browser
through `NEXT_PUBLIC_` variables. See `.env.example`.

## How API calls work

```
Browser ──/api/v1/...──▶ Next.js (same origin) ──rewrite──▶ FastAPI /api/v1/...
```

- The browser only calls relative `/api/...` paths. `next.config.ts` rewrites
  them to `${BACKEND_ORIGIN}/api/...` on the server.
- The backend's HttpOnly session cookie is therefore a first-party cookie for
  the frontend's own origin (SameSite=lax), and no CORS is involved.
- The browser's `Origin` header is passed through, so the backend's CSRF check
  sees the frontend origin. The backend's `FRONTEND_ORIGINS` must list it.
- Use `api` / `apiRequest` from `@/lib/api` (in client components, typically
  inside TanStack Query hooks). They send credentials, send and parse JSON, and
  throw `ApiError` with the HTTP status and parsed body for any non-2xx
  response (status `0` when the server cannot be reached).

## Sign-in and sessions

- `/sign-in` asks for an Indian mobile number (`POST /api/v1/auth/request-otp`),
  then the 6-digit code (`POST /api/v1/auth/verify-otp`). The backend sets an
  HttpOnly session cookie; the frontend never sees or stores a token.
- On every page load the signed-in user is recovered from
  `GET /api/v1/auth/me` (401 means signed out) and cached by TanStack Query.
  Role and retailer come only from that response.
- Retailers land on `/retailer`, the order desk on `/desk`. Signed-out
  visitors to either are sent to `/sign-in`; a user with the other role sees
  an access-denied page; an unknown role is denied everywhere. These guards
  are for the user experience only: the backend authorises every request.
- Sign out calls `POST /api/v1/auth/logout`, then clears all cached data. A
  401 from any other request also ends the client session.
- The request-otp response is identical for registered and unknown numbers,
  and every verification failure is the same 401, so the UI never reveals
  whether a number has an account or why a code failed. The backend's resend
  cooldown is silent, so the client shows its own 60-second countdown.
- Codes never reach the browser: request-otp returns only a message, and
  the frontend neither expects nor displays a code. In local development
  (`APP_ENV=development`, `AUTH_DEBUG_OTP=true`) the backend prints each code
  to its own terminal and, with `AUTH_DEV_OTP_OUTBOX_PATH`, appends it to a
  local outbox file that the end-to-end tests read (see `e2e/README.md`).
- Rate limits: the backend answers HTTP 429 with `Retry-After` (for example
  after 5 code requests for one number in an hour). The client keeps the
  status and `retryAfterSeconds` on `ApiError`, shows how long to wait, and
  holds the affected action until then: Continue for that number, Resend
  (its countdown is extended), or Verify (the typed digits are kept).

Demo accounts (seeded by the backend): retailers `9000000001`, `9000000002`,
`9000000003`; order desk `9000000010`.

## Project structure

```
src/
  app/                 Routes, root layout, error and not-found pages, global CSS
  components/
    ui/                Reusable primitives (shadcn/ui style): Button, Card, Sheet
    layout/            Application shell, header, navigation, brand
  features/            Domain feature modules (see features/README.md)
    auth/              Sign-in, session provider, role guards
    catalogue/         Retailer catalogue: search, category filter, product cards
    cart/              Retailer cart and order submission
    orders/            Retailer order history, details and repeat ordering
    desk/              Order desk queue, order review, confirm and reject
    exports/           Tally CSV export, export history, batch detail, downloads
  lib/
    api/               Typed API client
    utils.ts           Shared helpers (cn)
  providers/           Client-side providers (TanStack Query)
  types/               Shared types (API error shapes)
  test/                Test helpers (fetch stub, router mock, render)
e2e/                   Playwright tests against the running stack
```

Add shadcn/ui components with `npx shadcn@latest add <component>`; they are
written to `src/components/ui` using the tokens in `src/app/globals.css`.

## Design

- Neutral slate surfaces with a single deep teal accent, defined as semantic
  tokens in `src/app/globals.css`, with Light, Dark and System themes
  (Light by default, applied before first paint).
- Mobile first: touch targets of at least 44 px, a slide-in navigation panel
  below the `md` breakpoint, and a horizontal navigation bar above it.
- Accessibility: skip link, visible focus rings, `aria-current` on the active
  navigation item, and Radix primitives for keyboard and focus management.

## Deployment (planned)

The frontend deploys independently, for example on Vercel:

1. Set `BACKEND_ORIGIN` to the backend's public origin (for example the Render
   service URL) in the build environment.
2. On the backend, set `FRONTEND_ORIGINS` to the frontend's public origin and
   keep `AUTH_SESSION_COOKIE_SECURE=true` with `AUTH_SESSION_COOKIE_SAMESITE=lax`.

Because browsers only talk to the frontend's origin, the session cookie stays
first-party and no cross-site cookie settings are required.
