# IsoCode Labs Assignment Brief (Original)

> **Source of truth.** This file reproduces the original IsoCode Labs assignment brief and the chosen client problem statement verbatim. Only Markdown formatting (headings, lists, tables, code blocks) has been added. Wording, figures and data are unchanged, including the original spelling and capitalisation (e.g. "Ai or automation").
>
> Part A and Part B are the original text. Part C is an editor's index added for this repository; it introduces no new requirements and quotes or points to the original wording only.

---

## Part A — Assignment Brief (original text)

### Brief

#### Your task

A client has sent us the brief you pick below. Build the product, end to end, with a great UI and UX.

| | |
|---|---|
| You submit | One ZIP file with two files: your Technical Design Document (TDD) and a deployment.md |
| Time | 7 days from when this step opens |
| Work | Alone |

Build a small part really well instead of the whole scope badly. The "CORE BUILD" paragraph in your problem statement says which slice must work end to end.

#### Use AI, and use it well

AI tools are highly recommended. Use them to draft faster, test ideas, find gaps and polish your work, so that what you hand in is a more finished, specific outcome for this client and not a generic one. The judgement, the numbers and the final choices must still be yours. You will explain them in the interview.

#### What to do

Pick one problem statement from the ones shown to you. Then build it.

#### Must have

- The main flows in the brief work, on realistic sample data you create. No dead buttons
- Frontend, backend, a database and sign-in. Use roles if the brief has more than one kind of user
- The AI or automation part actually works, including what happens when it is wrong or unsure
- A polished UI: a consistent design, a layout that works on a phone, and loading, empty and error states
- Tests for the most important logic, and basic security: no secrets in the repository, validated inputs, access control

#### How to submit

Put these two files in one .zip (up to 25 MB) and upload it.

**1. Technical Design Document (PDF or Word)**

- The GitHub URL of your project, at the top. Also add the link to the live app if you deployed it
- The architecture and why you chose it
- The data model, the main flows and how the AI part works
- UI and design decisions: the key screens and why they look the way they do
- What you cut, and what you would do next

**2. deployment.md**

- How to run it locally, step by step
- How it is deployed, and the environment variables it needs (names only, never the values)
- Demo login details for the reviewer

#### Rules

- Use only the information in the problem statement. Label every assumption in your TDD
- Keep the commit history. We read it
- Do not put secrets or real personal data in the repository
- Keep the repository public until we have finished reviewing
- You can resubmit until the deadline; we review your latest version

#### What great looks like

- A product a client could try today
- Clean, readable code with a clear structure
- A UI that feels designed, not default
- Honest notes about the limits
- Extra depth, well-judged features beyond the core and careful edge cases count in your favour

#### Common mistakes

- A beautiful front end with nothing behind it
- Everything built, nothing working
- No sample data, so the app looks empty
- The AI part faked, or not handling mistakes
- A TDD with no GitHub URL

#### How you'll be scored

| Area | Weight |
|---|---|
| Quality of work | 50% |
| Problem solving | 30% |
| Communication | 20% |

---

## Part B — Problem Statement (original text)

### Client

Company A, Jabalpur. Distributor for 4 packaged food and home care brands, serving 1,800 kirana shops. 70 staff including 18 salesmen, revenue ₹85–100 crore a year.

### Today

Salesmen write orders in notebooks, or retailers send WhatsApp texts, photos of handwritten lists and Hindi voice notes. Next morning an office team of 6 types them into Tally. Salesmen don't know stock or schemes at the counter, so lines are cut later.

### The problem

Assumptions: 600 orders a day, 300 days, ₹5,000 per order, so ₹90 crore a year. 5% of orders have a wrong item or quantity needing a return or redelivery: 1,80,000 × 5% = 9,000 × ₹350 = ₹31.5 lakh. Order entry staff: 6 × ₹2.6 lakh = ₹15.6 lakh. Stock-outs found after ordering lose 1% of sales at 6% margin: ₹90 lakh × 6% = ₹5.4 lakh. Total ₹52.5 lakh a year.

### Users

Retailers (shop owners on phones), salesmen, and the office order desk. The owner-director signs the deal.

### The ask

My shopkeepers already order on WhatsApp, each in their own way, and my office retypes it all. I want a simple app where a retailer sees stock, his price and the current scheme, and orders in a minute. Salesmen should use it in the shop, even without signal. Messages and voice notes should become orders my desk only has to check.

### Must have

- Installable web app with OTP login, catalogue, retailer prices and schemes, stock status, repeat order and credit limit
- Salesman mode for orders during visits, working offline
- AI order capture from text, a photo of a list, or a Hindi voice note
- Scheme engine for free goods (buy 10 get 1) and slab discounts
- Order desk with credit holds, Tally export and van loading lists
- Dashboard of retailers with no order in 14 days

### Core build

Software Engineering candidates must ship this slice end to end, in about 6 to 8 screens: retailer catalogue with prices and schemes, cart and order, AI capture from a text order, and order desk with Tally export. Roles: retailer and order desk. AI: order capture from messages. Hard part: the scheme engine. Mock payments, messages and outside systems, and seed from the sample data. The rest of MUST HAVE is optional depth.

### Out of scope

- Delivery tracking
- Payments in the app
- GST e-invoicing (stays in Tally)

### Ai or automation

AI reads text, photos and voice notes and maps items to SKUs using the retailer's past orders. When it is wrong: each line shows the original words, a confidence level and two alternative SKUs, and unclear units (case or piece) are asked, not guessed. No order is placed until a person confirms it, and desk corrections become aliases for that retailer.

### Constraints

Retailers use low-end phones on slow 3G: first load under 3 seconds, large tap targets. Hindi and English. Prices must show clearly whether GST is included. A retailer's prices and credit are visible only to them, their salesman and the desk.

### Design

The client values good, minimal design: clean layouts, white space, a restrained palette and clear type, with nothing on screen that lacks a purpose. Design counts as much as features. Shopkeepers order between customers: a fast, uncluttered catalogue with large images, clear prices and a quick cart.

### Budget and time

₹7 lakh, 6 weeks. Success: 50% of orders placed in the app or captured by AI in 4 months; orders with errors from 5% to under 1%; order entry team from 6 to 3.

### Sample data

| SKU Code | SKU Name | Dealer Price ₹ | Stock Cases | Scheme |
|---|---|---|---|---|
| SKU-BS-075 | Glucose biscuit 75 g, case of 96 | 8.10 | 340 | Buy 10 cases get 1 free |
| SKU-DT-1KG | Detergent powder 1 kg, case of 20 | 101.50 | 85 | 3% off 5 or more cases |
| SKU-TE-250 | Tea leaf 250 g, case of 40 | 118.00 | 0 | None |

Raw order message:

```text
bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti
```

---

## Part C — Scope Index (editor's note, not part of the original brief)

This index sorts the brief's own items into the three scope buckets the brief defines. It adds no requirements.

### C1. Mandatory: submission and platform requirements (Part A "Must have", "How to submit", "Rules")

These apply to every submission, whatever problem statement is chosen. Main flows working on realistic sample data with no dead buttons; frontend, backend, database and sign-in with roles; AI that works, including when it is wrong or unsure; polished, phone-friendly UI with loading, empty and error states; tests for the most important logic; no secrets in the repository; validated inputs; access control. Deliverables: one ZIP containing the TDD (PDF or Word, with the GitHub URL at the top) and deployment.md. Every assumption must be labelled in the TDD.

### C2. Mandatory: core build slice (Part B "Core build")

- Retailer catalogue with prices and schemes
- Cart and order
- AI capture from a text order
- Order desk with Tally export
- Roles: retailer and order desk
- Hard part: the scheme engine
- Mock payments, messages and outside systems; seed from the sample data
- About 6 to 8 screens

The "Ai or automation" behaviour (original words, confidence level, two alternative SKUs, unit clarification for case or piece, human confirmation before an order is placed, desk corrections become aliases for that retailer) describes how the AI part must behave, and Part A requires that the AI part "actually works, including what happens when it is wrong or unsure". Its photo and voice inputs are optional depth (see C3). The "Constraints" and "Design" sections apply to whatever is built.

### C3. Optional depth (Part B "Must have" items outside the core: "The rest of MUST HAVE is optional depth")

- Installable web app
- OTP login
- Stock status, repeat order and credit limit
- Salesman mode for orders during visits, working offline
- AI order capture from a photo of a list or a Hindi voice note
- Order desk credit holds and van loading lists
- Dashboard of retailers with no order in 14 days

### C4. Out of scope (Part B "Out of scope")

- Delivery tracking
- Payments in the app
- GST e-invoicing (stays in Tally)
