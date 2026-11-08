# OrderBridge: Technical Design Document

- **GitHub:** _<repository URL, to be added before submission>_
- **Live app:** _<frontend URL, to be added after deployment>_
- **Demo logins:** retailers 9000000001, 9000000002, 9000000003; order desk 9000000010 (see `deployment.md`)

## 1. The problem and what was built

Company A (Jabalpur) distributes four brands to 1,800 kirana shops. Orders
arrive as notebook entries, WhatsApp texts, photos and Hindi voice notes, and
six people retype them into Tally the next morning. Salesmen do not know stock
or schemes at the counter, so lines are cut later. The brief sizes the cost at
₹52.5 lakh a year (returns from wrong lines, entry staff, lost sales).

The brief's core slice was built end to end and deployed as one product:

| Core requirement | What exists |
|---|---|
| Retailer catalogue with prices and schemes | Catalogue with the retailer's case price, the exact unit price, stock, schemes in plain words |
| Cart and order | Cart with exact estimated subtotal, idempotent submission, order history, progress, repeat order |
| AI capture from a text order | Message capture with deterministic rule-based interpretation and matching, human review of every line |
| Order desk with Tally export | Queue, review at current prices, confirmation with stock and credit, holds, rejection, Tally CSV export |
| Scheme engine (the hard part) | Free goods and slab discounts in one pure, tested module used by every price calculation |

Beyond the core: OTP sign-in with rate limits and lockout, credit holds,
stock status, repeat orders, retailer self-capture, desk aliases, an
at-a-glance desk summary, Light/Dark/System themes, and checksum-verified
CSV downloads.

## 2. Architecture and why

```
Browser (phone or desktop)
   │  same origin: /, /api/*
   ▼
Next.js 16 (Vercel) ── rewrites /api/* ──► FastAPI (Render) ──► PostgreSQL 17
   React 19, TanStack Query,                  services + scheme engine,
   zod-validated responses                    SQLAlchemy 2, Alembic
```

- **A separate API and web app.** The business rules (pricing, stock, credit,
  idempotency, capture review) live in one Python service with a database,
  where they can be tested with real transactions. The web app only displays
  and submits; it never computes a price that is stored or charged.
- **Same-origin proxy.** The browser calls `/api/*` on the frontend's origin
  and Next.js forwards it. The session cookie is therefore first-party,
  HttpOnly and Secure, and no CORS is exposed to the browser.
- **Services own transactions.** Routes validate input (Pydantic, unknown
  fields refused), call one service, and commit once; on any error they roll
  back explicitly. Services never commit, so one request is one transaction.
- **PostgreSQL for correctness.** Row locks (`SELECT … FOR UPDATE`) in a fixed
  lock order for confirmation, advisory locks for idempotent creation, check
  constraints for every state rule, and a sequence for order numbers. Money is
  `NUMERIC` in the database and `Decimal` in Python, never floating point.
- **Why not more.** No queue, cache or microservices: 600 orders a day fit
  comfortably in one service and one database, and fewer moving parts suit a
  six-week, ₹7 lakh budget. **[Assumption]** Volume stays near the brief's
  600 orders a day during the first phase.

## 3. Data model

| Table | Purpose and key rules |
|---|---|
| `users` | Phone, name, role (`retailer` or `order_desk`), active flag. Roles are set on the server only. |
| `retailers` | Business, credit limit, outstanding balance (both ≥ 0), linked user |
| `products` | SKU, name, case configuration (`units_per_case`), stock in cases (≥ 0), status |
| `retailer_product_prices` | One active case price per retailer and product |
| `schemes` | Free goods or slab discount with dates; the database enforces a valid configuration for each type |
| `orders` | Number (sequence, `OB-YYYY-NNNNNN` in the Indian business year), status, source (`retailer_app` or `ai_text`), totals, idempotency key and request fingerprint (unique per retailer), export batch |
| `order_items` | A snapshot of product name, SKU, unit, case price, free quantity, discount and line total at submission (re-priced at confirmation) |
| `order_events` | Append-only audit: submitted, confirmed, placed on hold, rejected, exported, with actor and details |
| `order_captures` | A message exactly as received, the interpreter's output, status, version (optimistic concurrency), creator, idempotency key, submission or discard record, linked order |
| `order_capture_lines` | Exact offsets into the message, product phrase, suggestions, ranked candidates, and the reviewer's decisions (unit, product, include or exclude with reason, alias choice) |
| `retailer_product_aliases` | A retailer's own phrase for a product; one active per phrase; replacements keep the old row (deactivated) |
| `export_batches` | Each Tally export: the exact CSV, its SHA-256, the creator and idempotency key |
| `otp_challenges`, `user_sessions`, `rate_limit_buckets` | Hashed codes with attempts and expiry, hashed session tokens, fixed-window counters keyed by HMAC |

Order statuses: `pending_review` → `confirmed` or `on_hold` (credit) or
`rejected`; `confirmed` → `exported`. Capture statuses: `needs_review` →
`ready` → `submitted` (a pending order exists), or `discarded`.

## 4. Main flows

**Retailer order.** The retailer browses the catalogue (prices from their own
price list, stock, current scheme with a worked example). The cart shows each
line's amount and an exact estimated subtotal, labelled as an estimate before
schemes. Submitting sends only product ids, quantities, notes and a
client-generated request id. The backend prices every line with the scheme
engine, refuses the order if any line has a problem (and lists every
problem), and creates a `pending_review` order. Repeating a request with the
same id returns the same order.

**Order desk confirmation.** The desk sees the queue (oldest first) and opens
an order. A preview re-prices it at current prices and stock and returns a
fingerprint of exactly those figures. Confirming sends that fingerprint; the
backend locks the order, its lines, the retailer, products, prices and
schemes in a fixed order, re-checks everything and confirms only if the
figures are unchanged. If they changed, nothing is confirmed and the new
figures are shown. If the total exceeds the retailer's available credit, the
order goes on hold instead. Stock and the retailer's balance change only
here, inside a savepoint, so a failure leaves nothing half-done.

**Tally export.** The desk exports every confirmed order waiting. The backend
locks those orders, builds the CSV (one row per line, Indian business dates,
two-decimal money, formula-like text neutralised), stores it with its SHA-256,
marks the orders exported and records an event for each, all in one
transaction. Downloads are verified in the browser against the stored
checksum before being saved. **[Assumption]** The CSV is a generic sales
register for Tally's import mapping, not a verified native Tally import;
ledgers, GST and vouchers are mapped in Tally.

**Message capture.** See section 5.

## 5. How the "AI" part works, and what happens when it is wrong

The brief asks for AI that reads messages and maps items to SKUs. This build
uses **deterministic rule-based interpretation and matching, not a trained or
language model**. That choice was deliberate: the results are explainable,
repeatable and fully tested, and the system behaves correctly when it is
unsure, which the brief values most. A language-model interpreter can be added
behind the same interface later (section 8).

**Interpretation** (`backend/app/services/capture_interpreter.py`). The
message is split into lines at commas, newlines, `aur`/`and`/`और` and the
Devanagari danda. Greetings at the edges ("bhaiya") are listed as ignored,
never silently dropped. In each line the rules separate the quantity from the
pack size ("2 peti washing powder **1 kilo**", "chai patti **250 wala** 1
peti"), read Devanagari digits and number words ("do", "पाँच"), and note the
unit word as a hint only. Every extracted span is validated against the
original text by its offsets.

**Matching** (`backend/app/services/capture_matcher.py`). Each active product
gets a heuristic score from fixed rules: an active alias of this retailer 95;
otherwise a recognised product word 60, a stated pack size that matches +25 or
contradicts −30; +10 if the product is in the retailer's 20 most recent
non-rejected orders; clamped to 0–100. A product is proposed only at 30 or
more and only if it is not tied with another; otherwise the line asks the
reviewer to choose. Every product stays a ranked candidate.

For the brief's sample message the result is:

| Line (original words) | Proposal | Score |
|---|---|---|
| "5 peti biscuit chhota" | Glucose biscuit 75 g | 60, possible ("chhota" is not a size) |
| "2 peti washing powder 1 kilo" | Detergent powder 1 kg | 85, strong |
| "chai patti 250 wala 1 peti" | Tea leaf 250 g | 85, strong, **out of stock** |

**When it is wrong or unsure.**
- Each line shows the original words, the score with its reasons, and two
  alternatives (marked when weak), with current stock.
- The unit is always asked: cases or pieces, nothing pre-selected. "peti" is
  never turned into cases on its own; the reviewer may confirm "peti means
  cases" for all such lines in one explicit step. Pieces convert to cases only
  when they make whole cases of the chosen product.
- A line cannot be included without a product and a confirmed unit; it can
  only be excluded with a reason. The out-of-stock tea stays visible and is
  refused at submission until it is changed or excluded with a reason.
- Nothing is ordered until a person creates the order: that creates a normal
  `pending_review` order (source `ai_text`) through the same pricing and
  validation as the app, and the desk still confirms it.
- **Desk corrections become aliases:** when the desk includes a product other
  than the proposal, it may opt in to remember the phrase for that retailer.
  An existing alias for another product is replaced only on explicit request,
  and the old one is kept in the history.
- Every change names the capture version it saw; a stale change is refused
  and the screen reloads. Submission, order creation, linking and aliases are
  one transaction.

**Assumptions.**
- **[Assumption]** "peti" usually means a case, but it is never applied
  without a person's confirmation.
- **[Assumption]** Product words are a hand-written list: "biscuit",
  "washing powder" (detergent) and "chai patti" (tea), plus the catalogue
  words "detergent" and "tea".
- **[Assumption]** "chhota" ("small") does not identify a pack size.
- **[Assumption]** The match score is a heuristic for ranking and explanation,
  not a probability; it is never shown as a percentage or confidence.

## 6. Pricing, schemes and money

- **[Assumption]** The brief's "Dealer Price ₹" (₹8.10, ₹101.50, ₹118.00) is
  per pack; case prices are pack price × units per case (₹777.60, ₹2,030.00,
  ₹4,720.00). Stock is counted in cases. Free cases consume stock.
- **[Assumption]** Prices exclude GST, which is calculated at invoicing in
  Tally; every screen says "excl. GST".
- **Scheme engine** (`backend/app/services/scheme_engine.py`): free goods give
  `(quantity // minimum) × free` free cases ("buy 10 get 1": 12 cases → 1
  free); slab discounts apply the percentage to the line at or above the
  minimum (5 detergent cases: ₹10,150.00 − 3% = ₹9,845.50). Rounding is
  half-up to paise, in `Decimal`.
- **[Assumption]** At most one scheme applies to a product at a time; two
  active schemes are a configuration error that blocks ordering the product
  until corrected, rather than a guess.
- The frontend never computes a stored amount. The cart's estimate and the
  catalogue's unit price use exact integer paise and are labelled as
  estimates.

## 7. Security

- OTP sign-in: codes are random, stored hashed (HMAC), single use, expire in
  5 minutes, limited attempts, a resend cooldown, cumulative lockout (10
  failures a day), and per-IP and per-number rate limits (429 with
  Retry-After). Sessions are random tokens stored hashed, in an HttpOnly,
  SameSite cookie, Secure in production.
- **[Assumption]** No SMS provider is available for the review. Because the
  four demo numbers are published, their codes are protected by a separate
  private reviewer access key (`AUTH_DEMO_ACCESS_KEY`), held only in the
  host's secret settings and given to the evaluator privately:
  - The evaluator enters the key once under "Reviewer access" on the sign-in
    page. It is compared in constant time, attempts are rate-limited per IP,
    and the request must come from the trusted origin.
  - Success sets an HttpOnly, SameSite=Lax cookie (Secure in production)
    holding a signed token that expires after 8 hours. It contains neither
    secret, grants no session or role, and stops working when the key is
    rotated.
  - With that cookie, the code step shows the freshly issued code of a demo
    account. Roles still come only from the account; every OTP rule above
    still applies.
  - Without it, requests for the demo numbers neither issue, invalidate nor
    check a code, and are counted in a separate rate-limit bucket, so
    outsiders cannot take over the accounts, expire the evaluator's code or
    lock the accounts.
  - The backend refuses to start if the list names any number other than the
    four synthetic accounts, or names any without a strong key. Production
    needs an SMS provider and an empty list.
- **Client IP behind the proxies (known limitation).** Requests pass through
  Vercel and Render's own proxy. `TRUSTED_PROXY_CIDRS` is left empty on
  purpose: the proxies' handling of `X-Forwarded-For` is unverified, and
  Vercel's outgoing addresses are not published, so trusting them would let a
  direct caller forge a client address. Forwarded headers are therefore
  ignored and cannot be spoofed, but all visitors share the per-IP rate
  limits of the proxy address. A noisy client can delay others (for example
  the reviewer access unlock, 10 attempts per 15 minutes) but never gain
  access: per-number limits, lockout and the key still apply.
- Authorisation on every route from the session only: retailers see only
  their own prices, orders and captures (another retailer's is a 404); desk
  routes require the desk role. State-changing requests require a trusted
  Origin (CSRF protection). Request bodies are limited to 64 KB.
- Inputs are validated with Pydantic (unknown fields refused) and responses
  with zod. Settings refuse unsafe production values (placeholder secrets,
  http origins, debug codes). No secrets are in the repository.

## 8. UI and design decisions

The brief values "clean layouts, white space, a restrained palette and clear
type, with nothing on screen that lacks a purpose", and shopkeepers who order
between customers on low-end phones.

- **One accent colour** (deep teal) on neutral surfaces; status colours only
  for meaning (in stock, held, rejected). Inter type, tabular figures for
  money. Light, Dark and System themes, Light by default.
- **Mobile first:** 44–48 px tap targets, a bottom navigation dock for
  retailers, stacked figures on phones and columns on desktops. The order desk
  is desktop-friendly: figures scan across, at-a-glance tiles lead into work.
- **Catalogue:** a large illustration per category, the case price large and
  the unit price under it, stock as a badge and count, the scheme in plain
  words with the backend's worked example. **[Assumption]** No product
  photographs were supplied, so category illustrations are drawn inline.
- **Capture review:** the message as received with each line highlighted, then
  one card per line: original words, suggested product and alternatives with
  score bars and reasons, the unit question, and include or exclude. Decided
  lines collapse to a summary. The screen states plainly that it is
  rule-based and that nothing is ordered until the order is created.
- **States everywhere:** skeletons while loading, contextual empty states,
  recoverable errors with a retry, rate-limit countdowns, and no automatic
  retries of anything that changes data.
- **Accessibility:** semantic regions and lists, labelled controls, visible
  focus, `aria-live` for results, reduced-motion support.

## 9. Testing

| Suite | Count | Covers |
|---|---|---|
| Backend (pytest, real PostgreSQL) | about 990 | Scheme engine, pricing, ordering, confirmation and holds, concurrency (stock, credit, double submission), exports, auth and rate limits, capture interpretation, matching, review, submission and rollback |
| Frontend (Vitest + Testing Library) | about 560 | Every screen's states, money helpers, request bodies, idempotent retries, session isolation, capture review rules |
| End to end (Playwright) | 5 | Real sign-in with codes, role isolation, and capture review against the running stack |

Performance, measured on the production build at mobile size with CPU slowed
4× and the cache disabled (first visit to the sign-in page, 301 KB
transferred):

| Network | First contentful paint | Fully loaded |
|---|---|---|
| Regular 3G (750 kbps, 100 ms) | 0.7 s | 3.8 s |
| Slow 3G (400 kbps, 400 ms) | 1.5 s | 7.2 s |

Content appears well under 3 seconds on both; the full load (mostly the
framework runtime and the font) exceeds 3 seconds on slow 3G. Trimming it is
listed in section 10.

## 10. What was cut, and what comes next

**Cut deliberately** ("build a small part really well"):
- Photo and Hindi voice capture, and a language-model interpreter.
- Salesman offline mode, van loading lists, the 14-day inactive retailer
  dashboard, credit-hold release.
- Hindi interface text (capture understands Hinglish and some Devanagari, but
  screens are English only).
- An installable web app manifest and offline caching.
- Out of scope in the brief: delivery tracking, payments, GST e-invoicing.

**Next, in order of value:**
1. A language-model interpreter behind the existing interface, returning the
   same validated extraction; the deterministic matcher keeps the final say.
2. Hindi interface text, then photo capture (OCR into the same review) and
   Hindi voice notes (speech to text into the same review).
3. Salesman mode with offline ordering and sync.
4. SMS provider integration; credit-hold release; van loading lists; the
   inactive retailer dashboard.
5. Authenticated proxy forwarding, so per-IP limits apply per real client:
   the Next.js server passes Vercel's client IP with a shared secret that
   the backend checks in constant time.
6. Performance: smaller first load (fewer client components on the sign-in
   page, a system font on slow networks) to bring the full load under 3
   seconds on slow 3G.
7. Product import for the full catalogue (CSV upload validated per row).

## 11. Labelled assumptions (summary)

All assumptions above are marked **[Assumption]**. In addition:
- **[Assumption]** The three demo retailers and the order desk user are
  synthetic; the brief names none.
- **[Assumption]** The demonstration orders (`seed_demo.py`) are placed
  through the real services for illustration; Sharma General Store has none,
  so the sample message is matched without order history.
- **[Assumption]** Order numbers and dates use the Indian business calendar
  (Asia/Kolkata).
