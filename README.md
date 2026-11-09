# OrderBridge

**Every retailer order, typed in the app or sent as an informal message,
becomes a validated, reviewable and correctly priced order.**

OrderBridge is an ordering and order-operations platform for an FMCG
distributor and the kirana shops it supplies. Retailers order from a
catalogue that shows their own prices, stock and current schemes. Informal
order messages are turned into reviewable lines instead of being retyped.
The order desk confirms orders against live stock and credit and exports
them for Tally.

- **Live app:** <https://orderbridge-ten.vercel.app>
- **Backend API:** <https://orderbridge-vaia.onrender.com> (health check:
  `/health`)
- **Source:** <https://github.com/Nitin2489/orderbridge>

All accounts and data in the live app are synthetic. Sign-in uses one-time
codes and no SMS provider is connected, so demo access is available on
request. The backend runs on a sleeping hosting plan: the first request
after a quiet period can take up to about a minute.

---

## The problem

A distributor serving around 1,800 shops receives orders as notebook
entries, WhatsApp texts, photos and voice notes. Every morning an office
team retypes them into Tally. Salesmen cannot see stock or schemes at the
counter, so lines are cut later, and some orders contain a wrong item or
quantity.

OrderBridge covers the core path that removes the retyping and the
guesswork: **catalogue → cart → order → desk confirmation → Tally export**,
plus **message → reviewed lines → order**.

---

## What it does

### For retailers (mobile first)
- **Catalogue** with the retailer's own case price, the exact unit price,
  stock status and the current scheme explained with a worked example. Every
  price says "excl. GST".
- **Cart** with an exact estimated subtotal and quick submission. The cart
  survives a page reload on the same device, and submission is idempotent,
  so a retried request never creates a second order.
- **Order history** with progress, details, the desk's reason for a
  rejection and **repeat order**.
- **Message capture:** paste an order message and review it line by line
  before it becomes an order.

### For the order desk (desktop friendly)
- **At-a-glance summary** and an oldest-first **order queue**.
- **Order review at current prices:** stored and current figures side by
  side, with any changes highlighted, and a link back to the original
  message for captured orders.
- **Confirmation with stock and credit checks.** Stock and the retailer's
  balance change together in one transaction. An order over the credit limit
  goes **on hold** instead of being confirmed. Orders can be rejected with a
  reason.
- **Capture review** for any retailer, a warning before the same message is
  captured twice, and saving a corrected phrase as that retailer's alias.
- **Tally export:** a generic sales-register CSV for Tally's import mapping,
  with every batch stored and downloads checked against their SHA-256. An
  **experimental TallyPrime XML** voucher download is available once the
  ledger names are configured. Neither is a verified native Tally
  integration; GST stays in Tally.

### Scheme engine
- **Free goods** (buy 10 cases, get 1 free) and **slab discounts** (3% off 5
  or more cases).
- Calculated only on the backend, in decimal arithmetic rounded to the paisa,
  and covered by tests. The browser never decides a price that is stored or
  charged.

---

## Message capture

```
bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti
```

| Original words | Suggested product | Match |
|---|---|---|
| 5 peti biscuit chhota | Glucose biscuit 75 g | possible ("chhota" is not a size) |
| 2 peti washing powder 1 kilo | Detergent powder 1 kg | strong |
| chai patti 250 wala 1 peti | Tea leaf 250 g | strong, **out of stock** |

By default the message is read by **deterministic rules**: the same message
always gives the same result, and every suggestion can be explained. An
optional **Gemini** reader can propose the lines instead. Its output is
validated against the original message, quantities, units and products are
still decided by OrderBridge's own code, and any failure falls back to the
rules. Only the message text and product names are sent to it.

How it handles uncertainty:
- Each line keeps its **original words** and shows a **heuristic match
  score** with its reasons and **two alternative products**. The score ranks
  candidates; it is not a probability.
- **The unit is always asked.** "peti" is never assumed to mean cases.
- **Nothing is dropped silently.** An out-of-stock line stays visible until a
  person changes it or excludes it with a reason.
- **No order without a person.** Creating the order produces a normal pending
  order that the desk still confirms.
- **Desk corrections can become aliases** for that retailer, and an existing
  alias is never overwritten silently.
- **Prices are never calculated by the reader.** Prices, schemes, stock,
  credit and totals always come from the backend.

---

## Engineering highlights

- **Correct under concurrency:** row locks in a fixed order, re-validation at
  confirmation, optimistic versions on captures, and idempotency keys on
  every create, submit and export.
- **Snapshots:** confirmed order lines keep the product, price and scheme
  they were confirmed at, plus an append-only audit trail.
- **Security:**
  - OTP sign-in: hashed single-use codes, expiry, attempt limits, lockout and
    rate limits.
  - HttpOnly session cookies, trusted-origin (CSRF) checks, `no-store` on
    API responses and security headers on every page.
  - Role-based access on every route, so a retailer sees only their own
    prices, credit and orders.
  - Validated inputs and outputs, and production settings that refuse unsafe
    values.
- **States everywhere:** loading skeletons, empty states and recoverable
  errors, with no automatic retries of anything that changes data.
- **Tests:**
  - Backend: 1,167 pytest tests on a real PostgreSQL database,
    including confirmation, credit holds, concurrency, capture and export.
  - Frontend: 618 Vitest and Testing Library tests.
  - End to end: 6 Playwright tests against the running stack, covering
    sign-in, role isolation, sign-out, message capture review and (opt-in,
    on a disposable database) the full order path from message to Tally
    export.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS v4, TanStack Query, zod |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 18 (Render, deployed); PostgreSQL 17 in Docker for local development |
| Testing | pytest, Vitest, Testing Library, Playwright |
| Hosting | Vercel (frontend), Render web service (backend), Render PostgreSQL (Singapore) |

The browser talks only to the frontend's origin. Next.js forwards `/api/*` to
the FastAPI backend, so the session cookie is first-party.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/development.md`](docs/development.md) | Local setup, running the tests, how it is deployed, environment variables, known limitations |
| [`frontend/README.md`](frontend/README.md) | Frontend structure, API calls, sign-in and sessions |
| [`frontend/e2e/README.md`](frontend/e2e/README.md) | Running the end-to-end tests |

## Limits and next steps

**Known limits:**
- No SMS provider.
- The interface is in English only (capture understands Hinglish and some
  Devanagari).
- Capture is text only.
- The Tally XML has not been imported into a live Tally company.
- Per-IP rate limits are shared behind the hosting proxy.

**Not built yet:**
- Photo and Hindi voice capture.
- Salesman offline mode.
- Van loading lists.
- An inactive-retailer dashboard.
- Credit-hold release.

**Out of scope:** delivery tracking, payments and GST e-invoicing.

## License

MIT. See [`LICENSE`](LICENSE).
