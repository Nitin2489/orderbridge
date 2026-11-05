
# OrderBridge - Project Instructions

## 0. Authoritative Requirements Source

`docs/assignment-brief.md` contains the complete original IsoCode Labs assignment brief and client problem statement, verbatim. It is the authoritative source of client requirements.

- If this file and the brief disagree, the brief wins.
- Sections 1, 2 and 7 below summarise the brief for quick reference. Re-read the brief before scoping features, writing the TDD or writing deployment.md.
- Items in this file marked **[Project decision]** are engineering choices made for this build. They are not client requirements.
- The brief's rule "Use only the information in the problem statement. Label every assumption in your TDD" applies. Items marked **[Assumption]** must appear as labelled assumptions in the TDD.

## 1. Project Overview

OrderBridge is a B2B FMCG distributor-retailer ordering and order operations platform.

This is an IsoCode Labs take-home assignment. The goal is to build a realistic, polished, production-quality application, not a college project or a superficial prototype.

The client is "Company A", Jabalpur: a distributor for 4 packaged food and home care brands, serving 1,800 kirana shops. The owner-director signs the deal.

Business context (from the brief):
- 70 staff, including 18 salesmen.
- Revenue ₹85–100 crore a year.
- The brief's problem sizing assumes 600 orders a day, 300 days and ₹5,000 per order (₹90 crore a year). Estimated annual cost of the current process: ₹52.5 lakh (see the brief for the breakdown).
- Today, salesmen write orders in notebooks, or retailers send WhatsApp texts, photos of handwritten lists and Hindi voice notes.
- Next morning an office team of 6 types them into Tally.
- Salesmen don't know stock or schemes at the counter, so lines are cut later.
- Users: retailers (shop owners on phones), salesmen, and the office order desk.
- Client budget and time: ₹7 lakh, 6 weeks.
- Client success targets: 50% of orders placed in the app or captured by AI in 4 months; orders with errors from 5% to under 1%; order entry team from 6 to 3.

Product promise:
Every retailer order, whether manually placed or interpreted from an informal message, becomes a validated, reviewable and accurately priced order.

## 2. Assignment Requirements

### 2.1 Mandatory core build (brief: "Core build")

This slice must work end to end, in about 6 to 8 screens:

1. Retailer catalogue with prices and schemes.
2. Cart and order.
3. AI capture from a text order.
4. Order desk with Tally export.

- Roles: retailer and order desk.
- AI: order capture from messages.
- Hard part: the scheme engine (free goods such as buy 10 get 1, and slab discounts).
- Mock payments, messages and outside systems, and seed from the sample data.

Implementation of the core flow **[Project decision]**: AI drafts are reviewed and corrected by a human, the order desk reviews and confirms orders, and Tally export is a Tally-compatible CSV.

### 2.2 Mandatory submission and platform requirements (brief: Part A "Must have")

- Main flows work on realistic sample data. No dead buttons.
- Frontend, backend, a database and sign-in, with roles.
- The AI part actually works, including what happens when it is wrong or unsure.
- Polished UI: consistent design, a layout that works on a phone, and loading, empty and error states.
- Tests for the most important logic.
- Basic security: no secrets in the repository, validated inputs, access control.

### 2.3 AI behaviour (brief: "Ai or automation")

From the brief:
- AI maps items to SKUs using the retailer's past orders.
- Each line shows the original words, a confidence level and **two alternative SKUs**.
- Unclear units (case or piece) are asked, not guessed.
- No order is placed until a person confirms it.
- Desk corrections become aliases for that retailer.
- Photo and Hindi voice note inputs are optional depth. The core needs text only.

**[Project decision]** guardrails that implement the brief:
- Extract product names, quantities and units from informal messages, and validate the structured output.
- Never silently guess, substitute or discard products.
- AI never calculates prices or order totals. The backend is authoritative for prices, schemes, stock, credit and totals.

### 2.4 Constraints and design (brief: "Constraints", "Design")

- Retailers use low-end phones on slow 3G: first load under 3 seconds, large tap targets.
- Hindi and English.
- Prices must show clearly whether GST is included.
- A retailer's prices and credit are visible only to them, their salesman and the desk.
- Good, minimal design: clean layouts, white space, a restrained palette and clear type, with nothing on screen that lacks a purpose. Design counts as much as features.
- Catalogue: fast and uncluttered, with large images, clear prices and a quick cart.

### 2.5 Optional depth (brief: "The rest of MUST HAVE is optional depth")

- Installable web app; OTP login.
- Stock status, repeat order and credit limit.
- Salesman mode for orders during visits, working offline.
- AI capture from a photo of a list or a Hindi voice note.
- Order desk credit holds and van loading lists.
- Dashboard of retailers with no order in 14 days.

The in-progress (uncommitted) `order_service.py` validates stock and credit at confirmation **[Project decision]**. Stock and credit appear in the client's Must have list but are not named in the core slice.

### 2.6 Out of scope (brief: "Out of scope")

- Delivery tracking.
- Payments in the app.
- GST e-invoicing (stays in Tally).

Order status tracking within OrderBridge (for example draft, pending review, confirmed) is an internal workflow **[Project decision]**. It must not grow into delivery tracking.

### 2.7 Submission deliverables (brief: "How to submit", "Rules")

- One .zip (up to 25 MB) with two files:
  1. Technical Design Document (PDF or Word): GitHub URL at the top (plus the live app link if deployed), the architecture and why, the data model, main flows and how the AI works, UI and design decisions for the key screens, and what was cut and what comes next.
  2. `deployment.md`: how to run it locally step by step, how it is deployed, the environment variables it needs (names only, never values), and demo login details.
- Use only the information in the problem statement, and label every assumption in the TDD.
- Keep the commit history; the reviewers read it.
- No secrets or real personal data in the repository.
- Keep the repository public until the review is finished.
- Scoring: Quality of work 50%, Problem solving 30%, Communication 20%.

## 3. Engineering Quality Requirements

Build a working product, not a broad collection of incomplete screens.

Requirements:
- Maintainable modular architecture.
- Validated API inputs and outputs.
- Authentication and role-based authorization.
- Database transactions for critical business operations.
- Correct inventory and credit handling.
- Decimal arithmetic for all financial calculations.
- Structured and validated AI output.
- Appropriate error handling.
- Loading, empty and error states in the UI.
- Responsive mobile-first retailer experience.
- Desktop-friendly order desk.
- Automated tests for important business logic.
- No secrets committed to Git.
- Realistic sample data.
- Meaningful Git commit history.

Do not claim a feature works unless it has actually been implemented and tested.

## 4. Technology Stack

Frontend:
- Next.js
- TypeScript
- Tailwind CSS
- shadcn/ui and Radix UI
- TanStack Query where appropriate

Backend:
- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy 2.x
- Alembic

Database:
- PostgreSQL 17

AI:
- Gemini or OpenAI through a provider-independent service interface.
- Structured JSON output validated using Pydantic.

Testing:
- Pytest
- Playwright
- Vitest and React Testing Library where appropriate.

Infrastructure:
- Docker Compose for local development.
- Vercel for frontend deployment.
- Render or another suitable host for the FastAPI backend.
- Managed PostgreSQL.
- GitHub for source control.

Do not introduce unnecessary technologies or infrastructure.

## 5. Architecture Rules

Keep business logic separate from API routes and database access.

Expected backend responsibilities:

- API layer: HTTP requests, authentication, validation and responses.
- Service layer: business operations and orchestration.
- Scheme engine: deterministic promotion calculations.
- Pricing service: retailer-specific pricing and applicable schemes.
- AI service: message interpretation and product suggestions.
- Database models: persistent entities and relationships.

Important business rules:

- Use Python Decimal, never float, for money.
- AI interprets orders; deterministic backend code calculates prices.
- Stock and retailer credit must be updated atomically.
- A failed order confirmation must not partially update inventory or credit.
- Validate stock again at confirmation time.
- Prevent duplicate confirmation from deducting stock twice.
- Preserve price, scheme and product snapshots on confirmed order items.
- Never silently remove an unavailable product.
- Never trust frontend totals.

## 6. Current Database Model

Existing models include:

- User
- Retailer
- Product
- RetailerProductPrice
- Scheme
- Order
- OrderItem
- OrderCapture

Existing enums include:
- UserRole
- ProductStatus
- SchemeType
- OrderStatus
- OrderSource
- CaptureStatus

Inspect the actual model definitions before changing schemas.

Use Alembic migrations for schema changes. Never modify an existing migration that has already been applied to the local database unless there is a deliberate migration recovery plan.

## 7. Current Sample Data

### 7.1 Sample data from the brief (authoritative)

| SKU Code | SKU Name | Dealer Price ₹ | Stock Cases | Scheme |
|---|---|---|---|---|
| SKU-BS-075 | Glucose biscuit 75 g, case of 96 | 8.10 | 340 | Buy 10 cases get 1 free |
| SKU-DT-1KG | Detergent powder 1 kg, case of 20 | 101.50 | 85 | 3% off 5 or more cases |
| SKU-TE-250 | Tea leaf 250 g, case of 40 | 118.00 | 0 | None |

Raw order message (the reference AI capture test case):

```text
bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti
```

Notes on the message:
- **[Assumption]** "peti" means a case. The brief does not define it.
- Tea (SKU-TE-250) has 0 stock in the brief's data, so that line must be surfaced as unavailable, never silently dropped.
- **[Assumption]** "biscuit chhota" ("small biscuit") names no pack size, so treat it as a low-confidence match with alternatives.

### 7.2 Current seed data (`backend/seed.py`)

**Known discrepancy (open):** `seed.py` uses SKU codes `GB-075`, `DW-1000` and `TE-250`, not the brief's `SKU-BS-075`, `SKU-DT-1KG` and `SKU-TE-250`. Resolve this in favour of the brief in a separate, reviewed change. The seed and any dependent tests must be updated together.

Three demo retailers **[Assumption: invented sample retailers; the brief names none]**:
- Sharma General Store
- Gupta Kirana Store
- Patel Provision Store

Sample products:

Glucose biscuits 75g:
- Case size: 96 packs
- Pack price: ₹8.10
- Case price: ₹777.60
- Stock: 340 cases
- Scheme: Buy 10 cases, get 1 free

Detergent 1kg:
- Case size: 20 packs
- Pack price: ₹101.50
- Case price: ₹2,030
- Stock: 85 cases
- Scheme: 3% discount on 5 or more cases

Tea 250g:
- Case size: 40 packs
- Pack price: ₹118
- Case price: ₹4,720
- Stock: 0 cases
- No scheme

Explicit assumptions **[Assumption]** (to be labelled in the TDD):
- The brief's column is "Dealer Price ₹" and does not say per pack or per case. Given values such as ₹8.10 for a case of 96, the dealer price is interpreted as a per-pack price.
- Pack prices have been multiplied by units per case to derive case prices.
- Product stock is measured in sellable cases, matching the brief's "Stock Cases" column.
- Free cases also consume stock.
- The brief does not say whether dealer prices include GST. The UI must still show clearly whether GST is included, as the brief requires, so the GST treatment must be stated as an assumption.

## 8. Existing Repository State

Project root:
~/projects/orderbridge

Backend:
backend/

Documentation:
docs/assignment-brief.md (original assignment brief; authoritative requirements)

Python virtual environment:
backend/.venv

Local database:
PostgreSQL through Docker Compose.

Backend dependencies are managed using uv.

Existing backend modules:
- app/core/config.py
- app/core/database.py
- app/models/base.py
- app/models/catalog.py
- app/models/enums.py
- app/models/sales.py
- app/services/scheme_engine.py
- app/services/pricing_service.py

Existing migration:
1aa9327febc3_create_initial_orderbridge_schema.py

Existing seed script:
backend/seed.py

Existing tests:
- tests/test_database.py
- tests/test_pricing_service.py
- tests/test_scheme_engine.py

Previously verified:
- PostgreSQL connection works.
- Initial Alembic migration is applied.
- Seed data is populated.
- Scheme engine works.
- Pricing service works.
- Eight existing tests passed before the current order confirmation changes.

Git:
- Branch: main
- Initial commit: d46bef8
- Commit message: chore: initialize OrderBridge backend and database foundation

There are currently uncommitted changes in:
- backend/app/models/sales.py
- backend/app/services/pricing_service.py
- backend/tests/test_pricing_service.py
- backend/app/services/order_service.py

IMPORTANT:
Preserve these changes. Do not reset, restore, discard or overwrite them without explicit review and user approval.

## 9. Development Workflow

The developer is learning backend engineering and database concepts.

Explain important architectural decisions and code changes clearly.

For every feature:

1. Understand the business problem.
2. Explain the technical problem.
3. Propose the design.
4. Inspect existing code before modifying it.
5. Implement in small, reviewable steps.
6. Run relevant lint checks and tests.
7. Explain what the tests prove.
8. Review the Git diff.
9. Commit the completed milestone with a descriptive message.

Do not replace entire files unnecessarily.

Do not generate the entire application in one task.

Do not make unrelated changes.

Before implementing a feature, inspect relevant existing models, services, routes and tests.

Never assume code exists without checking.

## 10. Git and Safety Rules

Never run destructive commands such as:
- git reset --hard
- git clean -fd
- git checkout -- .
- git restore on uncommitted work

Never discard user changes.

Never commit:
- .env
- API keys
- passwords
- credentials
- private certificates

Inspect git status and git diff before commits.

Keep commits focused on meaningful milestones.

Do not force-push or rewrite existing Git history.

## 11. Security

- Never print or expose secret values.
- Use environment variables for credentials.
- Validate all external input.
- Enforce authorization on the backend.
- Protect retailer-specific data.
- Use secure session handling.
- Avoid SQL injection and unsafe dynamic queries.
- Validate uploaded files if file upload functionality is added.
- Do not trust AI-generated data without validation and human review.

## 12. Scope Management

Deadline target: Friday, 6 November 2026.
Final submission deadline: 9 November 2026, 11:59 PM IST.

The brief's time allowance is "7 days from when this step opens". The dates above are the developer's working dates for that window.

Brief guidance: "Build a small part really well instead of the whole scope badly."

Prioritize the mandatory core (section 2.1) and the submission requirements (section 2.2).

Optional depth (section 2.5), such as photo/OCR capture, voice capture, salesman offline mode, OTP login, van loading lists and the 14-day dashboard, must not delay the working core.

Never build out-of-scope items (section 2.6).

Do not sacrifice correctness, testing, security or documentation to add superficial features.

## 13. How Claude Should Work

Act as a senior full-stack engineer and technical reviewer.

Before making substantial changes:
- Inspect the repository.
- Explain the proposed implementation.
- Identify risks and dependencies.
- Ask for approval when a change is destructive, architectural or security-sensitive.

When coding:
- Follow existing project conventions.
- Prefer simple, maintainable solutions.
- Write tests.
- Avoid unnecessary abstractions.
- Keep dependencies minimal.
- Explain non-obvious code.

After implementation:
- Run tests and lint checks.
- Report exactly what changed.
- Report tests actually executed and their results.
- Identify remaining limitations honestly.
- Do not claim deployment or integration succeeded without verification.

The developer must understand and approve major engineering decisions.
