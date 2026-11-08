# OrderBridge

**Every retailer order, typed in the app or sent as an informal message,
becomes a validated, reviewable and correctly priced order.**

OrderBridge is an ordering and order-operations platform for an FMCG
distributor and the kirana shops it supplies. Retailers order from a
catalogue that shows their own prices, stock and current schemes. Informal
order messages are turned into reviewable lines instead of being retyped.
The order desk confirms orders against live stock and credit and exports
them for Tally.

> **Live demo:** _<live demo URL, to be added after deployment>_
>
> Built for the IsoCode Labs take-home assignment. Client brief:
> [`docs/assignment-brief.md`](docs/assignment-brief.md).

---

## Try it

| Role | Mobile number | Account |
|---|---|---|
| Retailer | 9000000001 | Sharma General Store |
| Retailer | 9000000002 | Gupta Kirana Store |
| Retailer | 9000000003 | Patel Provision Store |
| Order desk | 9000000010 | Order Desk Operator |

All accounts and data are synthetic. Sign-in uses one-time codes. The demo
has no SMS provider, so the codes for these four accounts are shown on the
sign-in page only after **Reviewer access** is unlocked with a private key
supplied with the submission. The key is never stored in this repository.

---

## The problem

A Jabalpur distributor serves 1,800 shops. Orders arrive as notebook
entries, WhatsApp texts, photos and voice notes. Every morning an office team
retypes them into Tally. Salesmen cannot see stock or schemes at the counter,
so lines are cut later, and about 5% of orders contain a wrong item or
quantity.

OrderBridge builds the core path that removes the retyping and the guesswork:
**catalogue → cart → order → desk confirmation → Tally CSV**, plus
**message → reviewed lines → order**.

---

## What it does

### For retailers (mobile first)
- **Catalogue** with the retailer's own case price, the exact unit price,
  stock status and the current scheme explained with a worked example. Every
  price says "excl. GST".
- **Cart** with an exact estimated subtotal and quick submission. Submission
  is idempotent, so a retried request never creates a second order.
- **Order history** with progress, details and **repeat order**.
- **Message capture:** paste an order message and review it line by line
  before it becomes an order.

### For the order desk (desktop friendly)
- **At-a-glance summary** and an oldest-first **order queue**.
- **Order review at current prices:** stored and current figures side by
  side, with any changes highlighted.
- **Confirmation with stock and credit checks.** Stock and the retailer's
  balance change together in one transaction. An order over the credit limit
  goes **on hold** instead of being confirmed. Orders can be rejected with a
  reason.
- **Capture review** for any retailer, and saving a corrected phrase as that
  retailer's alias.
- **Tally-oriented CSV export:** a generic sales-register CSV for Tally's
  import mapping. Each export batch is stored, and downloads are checked
  against their SHA-256. This is **not** a verified native Tally integration;
  ledgers, GST and vouchers are mapped in Tally.

### Scheme engine
- **Free goods** (buy 10 cases, get 1 free) and **slab discounts** (3% off 5
  or more cases).
- Calculated only on the backend, in decimal arithmetic rounded to the paisa,
  and covered by tests. The browser never decides a price that is stored or
  charged.

---

## Message capture: rule-based, by design

Capture uses **deterministic rule-based extraction and matching. It is not
an AI or language model.** The same message always gives the same result,
and every suggestion can be explained.

```
bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti
```

| Original words | Suggested product | Match |
|---|---|---|
| 5 peti biscuit chhota | Glucose biscuit 75 g | possible ("chhota" is not a size) |
| 2 peti washing powder 1 kilo | Detergent powder 1 kg | strong |
| chai patti 250 wala 1 peti | Tea leaf 250 g | strong, **out of stock** |

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
  - HttpOnly session cookies and trusted-origin (CSRF) checks.
  - Role-based access on every route, so a retailer sees only their own
    prices, credit and orders.
  - Validated inputs and outputs, and production settings that refuse unsafe
    values.
- **States everywhere:** loading skeletons, empty states and recoverable
  errors, with no automatic retries of anything that changes data.
- **Tests:**
  - Backend: 1,000+ pytest tests on a real PostgreSQL database.
  - Frontend: 570+ Vitest and Testing Library tests.
  - End to end: Playwright tests against the running stack.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS v4, TanStack Query, zod |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 17 |
| Testing | pytest, Vitest, Testing Library, Playwright |
| Hosting (planned) | Vercel (frontend), Render (backend), managed PostgreSQL |

The browser talks only to the frontend's origin. Next.js forwards `/api/*` to
the FastAPI backend, so the session cookie is first-party.

---

## Documentation

| Document | Contents |
|---|---|
| [`deployment.md`](deployment.md) | Local setup step by step, running the tests, deployment, environment variables, demo logins |
| [`docs/technical-design.md`](docs/technical-design.md) | Architecture, data model, main flows, how capture works, UI decisions, labelled assumptions, what was cut |
| [`docs/requirements-tracker.md`](docs/requirements-tracker.md) | Status of every requirement in the brief |
| [`docs/assignment-brief.md`](docs/assignment-brief.md) | The original client brief |

**Running it locally:** follow [`deployment.md`](deployment.md).

## Limits and next steps

**Known limits:**
- No SMS provider.
- The interface is in English only (capture understands Hinglish and some
  Devanagari).
- Capture is text only.
- Per-IP rate limits are shared behind the hosting proxy.

**Not built:**
- Photo and Hindi voice capture.
- Salesman offline mode.
- Van loading lists.
- The 14-day inactive-retailer dashboard.
- Credit-hold release.

**Out of scope in the brief:** delivery tracking, payments and GST
e-invoicing.

The reasoning and the plan for each are in the technical design document.

## License

MIT. See [`LICENSE`](LICENSE).
