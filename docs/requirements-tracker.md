# Requirements tracker

Status of every client requirement from `docs/assignment-brief.md` (the
authoritative source). Updated as work lands. "Implemented" means the code
and its automated tests exist in the repository; whether a given test run
passed is recorded in the commit history and review notes, not here.

Statuses: **Implemented**, **In progress**, **Partial**, **Pending**,
**Out of scope** (excluded by the brief).

## Core build (brief: "Core build")

| Requirement | Status | Where / notes |
|---|---|---|
| Retailer catalogue with prices and schemes | Implemented | `backend/app/services/catalogue_service.py`, `frontend/src/features/catalogue/` |
| Cart and order | Implemented | `backend/app/services/retailer_order_service.py`, `frontend/src/features/cart/` |
| Scheme engine (free goods, slab discounts) | Implemented | `backend/app/services/scheme_engine.py`, `pricing_service.py` |
| AI capture from a text order | **In progress (Phase A)** | Rule-based interpretation, desk review, submission to a pending order. See "AI order capture" below. |
| Order desk review, confirmation and rejection | Implemented | `backend/app/services/order_service.py`, `desk_order_service.py`, `frontend/src/features/desk/` |
| Tally export (Tally-oriented CSV) | Implemented | `backend/app/services/tally_export_service.py`, `frontend/src/features/exports/`. Generic CSV; ledger, GST and voucher mapping are done in Tally. |
| Roles: retailer and order desk, sign-in | Implemented | OTP sign-in, server-side sessions, role checks on every route |

## AI order capture (brief: "Ai or automation")

| Requirement | Status | Notes |
|---|---|---|
| Text messages | **In progress (Phase A)** | Deterministic **rule-based** interpreter (`rules@1`, `backend/app/services/capture_interpreter.py`) and deterministic **rule-based** matcher (`backend/app/services/capture_matcher.py`) are implemented with unit tests. Neither is an AI model. Backend capture service and `/api/v1/captures` API (create, list, read, review, discard, submit to a PENDING_REVIEW `ai_text` order) implemented with service and API tests (A4/A5); screens not built yet. |
| Language-model interpretation (Gemini/OpenAI) | **Pending (Phase B)** | Only behind the replaceable interpreter interface, and only if time and credentials permit. Not started. |
| Photos of handwritten lists | **Pending** | Not started. Needs upload limits, OCR and transcript review. |
| Hindi voice notes | **Pending** | Not started. Needs audio upload and Hindi speech-to-text. |
| Retailer self-capture (retailer pastes their own message) | Partial (backend only) | The captures API lets a retailer create, review and submit its own captures; no screens yet. Only the order desk may save aliases. |
| Map items to SKUs using the retailer's past orders | Partial (Phase A3 matcher) | Implemented in the deterministic matcher: products in the retailer's 20 most recent non-rejected orders get +10. No orders are seeded, so the demo starts without history. Not yet wired into a capture workflow. |
| Each line shows the original words | In progress (Phase A) | Exact substrings with offsets into the unmodified message. |
| Each line shows a confidence level | Partial (Phase A3 matcher) | The matcher computes a deterministic heuristic match score (0-100) with a band and reasons. Not a calibrated probability. Not yet stored or shown on screen. |
| Each line shows two alternative SKUs | Partial (Phase A3 matcher) | The matcher returns up to two alternatives with scores and bands, flagged when weak. With three products, they are the other two. Not yet shown on screen. |
| Unclear units (case or piece) are asked, not guessed | In progress (Phase A) | Every unit, including "peti", must be confirmed by a person. |
| No order is placed until a person confirms it | In progress (Phase A) | Capture review creates a PENDING_REVIEW order; the existing desk confirmation is the only step that deducts stock or uses credit. |
| Desk corrections become aliases for that retailer | In progress (Phase A) | Only for corrected lines, only on explicit opt-in, never overwriting an existing alias without an explicit replacement. |

## Platform and submission (brief: Part A "Must have")

| Requirement | Status | Notes |
|---|---|---|
| Frontend, backend, database, sign-in with roles | Implemented | |
| Tests for the most important logic | Implemented (ongoing) | pytest, Vitest, Playwright |
| No secrets in the repository; validated inputs; access control | Implemented | |
| Loading, empty and error states; phone layout | Implemented for built screens | |
| Hindi and English | **Pending** | Interface text is English only. Capture understands Hinglish and some Devanagari words. |
| First load under 3 seconds on slow 3G | **Pending (not measured)** | |
| Prices show clearly whether GST is included | Implemented | "Amounts exclude GST" (labelled assumption) |
| Retailer prices and credit visible only to them and the desk | Implemented | Salesman role not built |

## Optional depth (brief: "The rest of MUST HAVE is optional depth")

| Requirement | Status |
|---|---|
| OTP login | Implemented |
| Installable web app | Pending |
| Stock status | Implemented (catalogue) |
| Repeat order | Implemented |
| Credit limit | Implemented (credit hold at confirmation); hold release pending |
| Salesman mode, working offline | Pending |
| Van loading lists | Pending |
| Dashboard of retailers with no order in 14 days | Pending |

## Out of scope (brief: "Out of scope")

Delivery tracking, payments in the app, GST e-invoicing (stays in Tally).

## Labelled assumptions for the TDD (retailer screens)

- **[Assumption]** No product photographs were supplied, so the catalogue
  shows a simple illustration per category (biscuits, home care,
  beverages), drawn inline. They are decorative; the product's name, size
  and SKU are always shown as text.
- **[Assumption]** The price per unit inside a case (for example Rs 8.10 =
  Rs 777.60 / 96) is shown only when it divides exactly into paise; it is
  never rounded.
- **[Assumption]** The cart shows an estimated subtotal at catalogue prices,
  computed exactly in paise, before schemes. The order's real totals are
  calculated by the backend when it is submitted and confirmed by the order
  desk.
- **[Assumption]** Order progress shows only OrderBridge's own statuses
  (placed, order desk review, confirmed, sent for billing); delivery is out
  of scope and not shown.

## Labelled assumptions for the TDD (AI order capture)

- **[Assumption]** "peti" usually means a case, but it is **never** applied
  automatically: a person confirms the unit for every line (optionally once
  for every line using the same word in a message).
- **[Assumption]** Orders are placed in whole cases only, as the catalogue,
  prices, stock and schemes are all per case. Pieces or packets must be
  converted to cases by the reviewer.
- **[Assumption]** The product words used for matching are a hand-written
  list, not learned data: the approved synonyms "biscuit", "washing powder"
  (detergent) and "chai patti" (tea), plus the catalogue's own name words
  "detergent" and "tea".
- **[Assumption]** Match score contract: alias 95; product word 60; stated
  pack size matches +25, contradicts -30; recent-order history +10;
  clamped to 0-100. A product is proposed only at 30 or more with no tie
  for the top score.
- **[Assumption]** A pack size stated without a unit ("250 wala") is
  compared with the number in the product's own unit (250 g).
- **[Assumption]** "chhota" ("small") does not identify a pack size.
- **[Assumption]** Greeting words such as "bhaiya" are ignored for matching
  but kept in the stored message.
- **[Assumption]** The match score is a deterministic heuristic for ranking
  and explanation, not a probability.
- **[Assumption]** Aliases store the product phrase only (for example
  "biscuit chhota"), per retailer.
