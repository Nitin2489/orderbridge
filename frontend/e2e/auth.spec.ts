import { expect, test, type Page } from "@playwright/test";

import { OUTBOX_ENV, outboxPathFromEnv, readOtp } from "./otp-outbox";

/**
 * Sign-in against the real backend and seeded demo accounts. Nothing is
 * mocked: these tests exercise the actual OTP, cookie and role checks.
 *
 * Codes are never in HTTP responses or the page. They are read here, in the
 * Playwright (Node) process, from the backend's development OTP outbox
 * (E2E_OTP_OUTBOX = the backend's AUTH_DEV_OTP_OUTBOX_PATH). A request
 * inside the backend's 60-second resend cooldown issues no new code; the
 * newest unexpired code in the outbox is then still the valid one, so
 * reruns work. See e2e/README.md, including the 5-per-hour per-number
 * request limit.
 *
 * Codes are never printed: they are only typed into the page.
 */

const RETAILER = "9000000001"; // Sharma General Store
const RETAILER_FOR_WRONG_CODE = "9000000003"; // Patel Provision Store
const ORDER_DESK = "9000000010";

// Traces, screenshots and videos would store the typed one-time code, the
// verify-otp request body and the session cookie, so this file records none
// of them. (Playwright only accepts these options at file scope.)
test.use({ trace: "off", screenshot: "off", video: "off" });

async function requestCode(page: Page, phone: string): Promise<string> {
  await page.goto("/sign-in");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByRole("button", { name: "Continue" }).click();
  // The backend writes the outbox record before it answers, so the code is
  // there once the page reaches the code step.
  await expect(page.getByRole("heading", { name: "Enter your code" })).toBeVisible();

  return readOtp(phone);
}

async function enterCode(page: Page, code: string) {
  await page.getByLabel("Digit 1 of 6").fill(code);
  await page.getByRole("button", { name: "Verify and sign in" }).click();
}

// Needs no code, so it runs whether or not the outbox is configured.
test("protected pages send signed-out visitors to sign-in", async ({ page }) => {
  await page.goto("/retailer");
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.goto("/desk");
  await expect(page).toHaveURL(/\/sign-in$/);
});

test.describe("sign-in with a one-time code", () => {
  test.skip(
    outboxPathFromEnv() === null,
    `${OUTBOX_ENV} is not set: these tests read codes from the backend's development ` +
      "OTP outbox (see e2e/README.md).",
  );

  test("retailer signs in, keeps the session on reload, is kept out of the desk and signs out", async ({
    page,
    context,
  }) => {
    await enterCode(page, await requestCode(page, RETAILER));

    await expect(page).toHaveURL(/\/retailer$/);
    await expect(page.getByRole("heading", { name: "Catalogue" })).toBeVisible();
    await expect(page.locator("#main-content").getByText("Sharma General Store")).toBeVisible();

    // The session is an HttpOnly cookie, invisible to page scripts.
    const cookies = await context.cookies();
    const session = cookies.find((cookie) => cookie.name === "orderbridge_session");
    expect(session?.httpOnly).toBe(true);
    expect(await page.evaluate(() => document.cookie)).not.toContain("orderbridge_session");
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Catalogue" })).toBeVisible();
    await expect(page.locator("#main-content").getByText("Sharma General Store")).toBeVisible();

    await page.goto("/desk");
    await expect(
      page.getByRole("heading", { name: "You do not have access to this page" }),
    ).toBeVisible();

    // The backend refuses the desk API too; the page guard is not the control.
    const desk = await page.request.get("/api/v1/desk/orders");
    expect(desk.status()).toBe(403);
    const exports = await page.request.get("/api/v1/desk/exports/eligible");
    expect(exports.status()).toBe(403);

    await page.goto("/sign-in");
    await expect(page).toHaveURL(/\/retailer$/);

    await page.getByRole("button", { name: "Sign out" }).first().click();
    await expect(page).toHaveURL(/\/sign-in$/);

    const me = await page.request.get("/api/v1/auth/me");
    expect(me.status()).toBe(401);
    await page.goto("/retailer");
    await expect(page).toHaveURL(/\/sign-in$/);
  });

  test("order desk signs in to the desk workspace", async ({ page }) => {
    await enterCode(page, await requestCode(page, ORDER_DESK));

    await expect(page).toHaveURL(/\/desk$/);
    await expect(page.getByRole("heading", { name: "Order queue" })).toBeVisible();

    // Tally export, read-only: the page loads the waiting orders and history
    // through the proxy with the session cookie. Nothing is exported, so the
    // seeded data is unchanged. (Checked in this test to reuse its sign-in:
    // each sign-in uses one of the number's 5 hourly code requests.)
    //
    // The project emulates a phone, so the primary navigation is the mobile
    // menu (a dialog behind "Open navigation menu"); the desktop bar is
    // hidden at this width.
    const openMenu = page.getByRole("button", { name: "Open navigation menu" });
    const menu = page.getByRole("dialog");

    await openMenu.click();
    await menu.getByRole("link", { name: "Tally export" }).click();
    await expect(page).toHaveURL(/\/desk\/exports$/);
    // Following a link closes the menu.
    await expect(menu).toBeHidden();
    await expect(page.getByRole("heading", { name: "Tally export", level: 1 })).toBeVisible();

    // The current-page marker moved from Order queue to Tally export.
    await openMenu.click();
    await expect(menu.getByRole("link", { name: "Tally export" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(menu.getByRole("link", { name: "Order queue" })).not.toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();

    await expect(page.getByRole("region", { name: "Waiting for export" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Export history" })).toBeVisible();
    await expect(page.getByText("could not be loaded")).toHaveCount(0);
    const eligible = await page.request.get("/api/v1/desk/exports/eligible");
    expect(eligible.status()).toBe(200);

    // End the session so no desk session outlives the test.
    await page.getByRole("button", { name: "Sign out" }).first().click();
    await expect(page).toHaveURL(/\/sign-in$/);
    expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
  });

  test("a wrong code is rejected, then the right code signs in", async ({ page }) => {
    const code = await requestCode(page, RETAILER_FOR_WRONG_CODE);
    const wrong = code === "000000" ? "111111" : "000000";

    await enterCode(page, wrong);

    await expect(page.locator("#otp-error")).toContainText(
      "That code is incorrect or has expired",
    );
    await expect(page).toHaveURL(/\/sign-in$/);
    expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);

    // The same challenge still allows more attempts. Signing in successfully
    // also clears this number's recorded failure, so repeated runs never build
    // up to the backend's cumulative lockout (10 failures per day).
    await enterCode(page, code);

    await expect(page).toHaveURL(/\/retailer$/);
    await expect(page.locator("#main-content").getByText("Patel Provision Store")).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).first().click();
    await expect(page).toHaveURL(/\/sign-in$/);
  });
});
