import { expect, test } from "@playwright/test";

import { OUTBOX_ENV, outboxPathFromEnv, readOtp } from "./otp-outbox";

/**
 * Message capture against the real stack, as a retailer capturing its own
 * message. It creates a capture and discards it: no order is created, so
 * the development database keeps its orders unchanged. Order creation from
 * a capture is covered by the backend integration tests.
 *
 * Uses Gupta Kirana Store (9000000002), which no other end-to-end test
 * signs in as, to stay within the per-number code request limit.
 */

const RETAILER = "9000000002";
const DEMO =
  "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti";

// Traces, screenshots and videos would record the typed one-time code.
test.use({ trace: "off", screenshot: "off", video: "off" });

test.describe("message capture", () => {
  test.skip(
    outboxPathFromEnv() === null,
    `${OUTBOX_ENV} is not set: sign-in reads codes from the backend's development OTP outbox.`,
  );

  test("a retailer captures a message, reviews it and discards it", async ({ page }) => {
    await page.goto("/sign-in");
    await page.getByLabel("Mobile number").fill(RETAILER);
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Enter your code" })).toBeVisible();
    await page.getByLabel("Digit 1 of 6").fill(await readOtp(RETAILER));
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(page).toHaveURL(/\/retailer$/);

    await page.goto("/retailer/capture/new");
    await page.getByLabel("Message").fill(DEMO);
    await page.getByRole("button", { name: "Read message" }).click();

    await expect(page.getByRole("heading", { name: "Review message" })).toBeVisible();
    await expect(page.getByText(/not an AI model/)).toBeVisible();
    const lines = page.getByRole("article");
    await expect(lines).toHaveCount(3);
    await expect(lines.nth(0)).toContainText("“5 peti biscuit chhota”");
    // The out-of-stock tea stays visible and says so.
    await expect(lines.nth(2).getByText("Out of stock").first()).toBeVisible();
    // "peti" is never confirmed by itself: including needs a confirmed unit.
    await expect(lines.nth(0).getByRole("button", { name: "Include line" })).toBeDisabled();

    await page.getByRole("button", { name: "Discard message…" }).click();
    await page.getByLabel("Reason for discarding").fill("End-to-end test message");
    await page.getByRole("button", { name: "Discard message", exact: true }).click();
    await expect(page.getByText("This message was discarded")).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).first().click();
    await expect(page).toHaveURL(/\/sign-in$/);
  });
});
