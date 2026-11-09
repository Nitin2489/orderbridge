import { expect, test, type Page } from "@playwright/test";

import { OUTBOX_ENV, outboxPathFromEnv, readOtp } from "./otp-outbox";

/**
 * The full order path in a real browser: a retailer captures a message,
 * reviews it and creates an order; the order desk confirms it and exports
 * it to the Tally CSV.
 *
 * It CHANGES DATA: it creates, confirms and exports an order (stock and the
 * retailer's balance change, and every confirmed order waiting is
 * exported). So it is opt-in (E2E_ORDER_FLOW=1) and must only run against a
 * disposable, freshly seeded local database (`seed.py` only, so this is the
 * only order), never the development or production database.
 *
 * Expected figures come from the brief's sample data: biscuit ₹777.60 a case
 * with buy 10 get 1 free; detergent ₹2,030.00 a case with 3% off 5 or more;
 * tea out of stock. Sharma General Store: limit ₹50,000, outstanding
 * ₹12,000.
 */

const RETAILER = "9000000001"; // Sharma General Store
const ORDER_DESK = "9000000010";
const MESSAGE =
  "bhaiya 10 peti biscuit, 5 peti washing powder 1 kilo, chai patti 250 wala 1 peti";

test.use({ trace: "off", screenshot: "off", video: "off" });

async function signIn(page: Page, phone: string, home: RegExp) {
  await page.goto("/sign-in");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Enter your code" })).toBeVisible();
  await page.getByLabel("Digit 1 of 6").fill(await readOtp(phone));
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL(home);
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page).toHaveURL(/\/sign-in$/);
}

test.describe("order flow: capture → desk confirmation → Tally export", () => {
  test.skip(
    process.env.E2E_ORDER_FLOW !== "1",
    "Changes data: set E2E_ORDER_FLOW=1 only against a disposable seeded database.",
  );
  test.skip(outboxPathFromEnv() === null, `${OUTBOX_ENV} is not set.`);

  test("retailer capture becomes a confirmed, exported order", async ({ page }) => {
    test.setTimeout(180_000);

    // 1-2. Retailer signs in and opens message capture.
    await signIn(page, RETAILER, /\/retailer$/);
    await page.goto("/retailer/capture/new");

    // 3. Submit the informal message.
    await page.getByLabel("Message").fill(MESSAGE);
    await page.getByRole("button", { name: "Read message" }).click();
    await expect(page.getByRole("heading", { name: "Review message" })).toBeVisible();

    // 4. The original message is kept exactly.
    await expect(page.getByText("“bhaiya” (greeting)")).toBeVisible();
    const lines = page.getByRole("article");
    await expect(lines).toHaveCount(3);
    const [biscuit, detergent, tea] = [lines.nth(0), lines.nth(1), lines.nth(2)];
    await expect(biscuit).toContainText("“10 peti biscuit”");
    await expect(detergent).toContainText("“5 peti washing powder 1 kilo”");
    await expect(tea).toContainText("“chai patti 250 wala 1 peti”");

    // 5. Suggestions, match scores and two weak alternatives per line.
    await expect(biscuit).toContainText("Glucose Biscuit 75g");
    await expect(biscuit.getByText("60/100 · Possible match")).toBeVisible();
    await biscuit.getByRole("button", { name: "Show alternatives (2)" }).click();
    await expect(biscuit.getByText("Weak match", { exact: true })).toHaveCount(2);
    await expect(detergent).toContainText("Detergent Powder 1kg");
    await expect(detergent.getByText("85/100 · Strong match")).toBeVisible();
    await detergent.getByRole("button", { name: "Show alternatives (2)" }).click();
    await expect(detergent.getByText("Weak match", { exact: true })).toHaveCount(2);
    await expect(tea).toContainText("Tea 250g");
    await expect(tea.getByText("Out of stock").first()).toBeVisible();
    // No unit is confirmed yet, so nothing can be included.
    await expect(biscuit.getByRole("button", { name: "Include line" })).toBeDisabled();

    // 6. Confirm units (cases) and include biscuit and detergent.
    for (const line of [biscuit, detergent]) {
      await line.getByRole("radio", { name: "Cases", exact: true }).check();
      await line.getByRole("button", { name: "Confirm quantity" }).click();
      await expect(line.getByRole("button", { name: "Include line" })).toBeEnabled();
      await line.getByRole("button", { name: "Include line" }).click();
      await expect(line.getByText("Included")).toBeVisible();
    }
    await expect(biscuit.getByText("10 cases", { exact: true })).toBeVisible();
    await expect(detergent.getByText("5 cases", { exact: true })).toBeVisible();

    // 7. Exclude the out-of-stock tea with a reason.
    await tea.getByRole("button", { name: "Exclude…" }).click();
    await tea.getByLabel("Reason for excluding this line").fill("Out of stock at the distributor");
    await tea.getByRole("button", { name: "Exclude line" }).click();
    await expect(tea.getByText("Excluded")).toBeVisible();

    // 8. Create the order (pending review, not confirmed).
    await page.getByRole("button", { name: "Create order for review" }).click();
    await expect(page).toHaveURL(/\/retailer\/orders\/[0-9a-f-]+$/);
    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toContainText(/OB-\d{4}-\d{6}/);
    const orderNumber = ((await heading.textContent()) ?? "").match(/OB-\d{4}-\d{6}/)![0];
    await signOut(page);

    // 9-10. The order desk sees it waiting in its queue.
    await signIn(page, ORDER_DESK, /\/desk$/);
    const queueLink = page.getByRole("link", { name: orderNumber });
    await expect(queueLink).toBeVisible();

    // 11. Pricing, schemes, stock and credit on the review screen.
    await queueLink.click();
    await expect(page.getByRole("heading", { name: `Order ${orderNumber}` })).toBeVisible();
    const pricing = page.getByRole("region", { name: "Current pricing" });
    await expect(pricing).toContainText("₹7,776.00"); // 10 × 777.60, 1 free case
    await expect(pricing).toContainText("₹304.50"); // 3% off 5 detergent cases
    await expect(pricing).toContainText("₹17,621.50"); // grand total, excl. GST
    await expect(pricing.getByText("Stock required").first()).toBeVisible();
    await expect(pricing).toContainText("₹38,000.00"); // available credit before
    await expect(page.getByText("Sharma General Store: credit now")).toBeVisible();

    // 12. Confirm.
    await page.getByRole("button", { name: "Confirm order" }).click();
    await page.getByRole("button", { name: "Yes, confirm order" }).click();
    await expect(page.getByText("Order confirmed")).toBeVisible();

    // 13. Credit updated (stock is checked in the database afterwards).
    await page.reload();
    const credit = page.getByRole("region", { name: "Sharma General Store: credit now" });
    await expect(credit).toContainText("₹20,378.50"); // 38,000.00 − 17,621.50

    // 14. Create the Tally CSV export.
    await page.goto("/desk/exports");
    await page.getByRole("button", { name: "Export to CSV" }).click();
    await page.getByRole("button", { name: "Yes, export orders" }).click();
    const created = page.getByText(/Export created: EX-\d{4}-\d{6}/);
    await expect(created).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "Export created" })).toContainText(
      "1 order exported",
    );
    await page.getByRole("link", { name: "View batch" }).click();

    // 15. The batch holds this order, and its CSV has the right lines.
    await expect(page.getByRole("link", { name: orderNumber })).toBeVisible();
    const batchId = page.url().split("/").pop()!;
    const csv = await page.request.get(`/api/v1/desk/exports/${batchId}/csv`);
    expect(csv.status()).toBe(200);
    const rows = (await csv.text()).trim().split("\r\n");
    expect(rows).toHaveLength(3); // header and two lines; tea was excluded
    const body = rows.slice(1).join("\n");
    expect(body).toContain(`${orderNumber},`);
    expect(body).toContain("SKU-BS-075,Glucose Biscuit 75g,10,1,case,777.60,7776.00,0.00,7776.00");
    expect(body).toContain(
      "SKU-DT-1KG,Detergent Powder 1kg,5,0,case,2030.00,10150.00,304.50,9845.50",
    );
    expect(body).toContain("17926.00,304.50,17621.50,exclusive (GST not calculated)");
    expect(body).not.toContain("SKU-TE-250");

    await signOut(page);
  });
});
