# Feature modules

Each domain feature gets its own folder here, for example `auth/`, `catalogue/`,
`orders/`, `order-desk/` and `exports/`. A feature folder holds what is specific
to that feature:

- `api.ts`: typed calls through `@/lib/api` and the TanStack Query hooks built on them.
- `schemas.ts`: Zod schemas for its forms and API payloads.
- `components/`: the feature's UI.

Shared, domain-free building blocks belong in `@/components/ui`, and
application-wide layout in `@/components/layout`. Routes in `src/app` stay thin
and compose feature components.

Existing modules:

- `auth/`: OTP sign-in, the session provider (`useSession`), role guards
  (`RoleGate`) and sign-out.
- `catalogue/`: the retailer catalogue (`GET /retailer/catalogue` and
  `/categories`), with search, category filter, stock and scheme display.
- `cart/`: the retailer cart and order submission (`POST /retailer/orders`):
  a cart scoped to the signed-in retailer (product ids and quantities kept
  in this device's storage, never prices), idempotent submission
  and the order confirmation.
- `orders/`: retailer order history (`GET /retailer/orders`), order details
  (`GET /retailer/orders/{id}`) and repeat ordering
  (`POST /retailer/orders/{id}/repeat`), with the shared order response
  schemas and retailer-scoped repeat idempotency keys.
- `desk/`: the order desk. The queue (`GET /desk/orders`, with status,
  search, date and retailer filters) and order review
  (`GET /desk/orders/{id}`, `/preview`, `POST .../confirm` and `/reject`).
  Confirmation sends the exact preview fingerprint the desk approved;
  ambiguous or uncertain results re-read the order instead of retrying.
- `captures/`: message capture for both roles (`/api/v1/captures`, and
  `GET /desk/retailers` for the desk's retailer picker). The message is kept
  exactly as received; each line shows its original words, a heuristic match
  score with reasons (never a probability), the suggested product and up to
  two alternatives with stock; the unit (cases or pieces) is always asked;
  aliases are desk-only. Every change names the capture version.
- `exports/`: Tally CSV export for the order desk (`GET /desk/exports/eligible`,
  `POST /desk/exports`, `GET /desk/exports`, `GET /desk/exports/{id}` and
  `/csv`). An export takes every confirmed order waiting at that moment; the
  request carries only a `client_request_id`, reused for a manual retry
  after an uncertain outcome and never retried automatically. CSV downloads
  go through `apiDownload` (same-origin, session cookie) and are saved only
  when their SHA-256 matches the batch's recorded checksum, or explicitly
  unverified where Web Crypto is unavailable. The CSV is generic: ledgers,
  GST and vouchers are mapped in Tally.

Tests sit next to the code as `*.test.ts(x)`.
