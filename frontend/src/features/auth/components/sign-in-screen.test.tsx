import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import {
  deferred,
  deskUser,
  json,
  mockApi,
  networkFailure,
  NOT_AUTHENTICATED,
  retailerUser,
  sequence,
} from "@/test/api-mock";
import { mockRouter, setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { SignInScreen } from "./sign-in-screen";

const GENERIC_ACCEPTED = {
  message:
    "If this number belongs to an active OrderBridge account, a one-time code has been issued. It expires in a few minutes.",
};
const INVALID_CODE = json(401, {
  detail: "The code is invalid or has expired. Request a new code.",
});
const TOO_MANY = { detail: "Too many requests. Please wait and try again." };

function renderSignIn() {
  setPathname("/sign-in");
  return renderWithSession(<SignInScreen />);
}

async function submitPhone(user: ReturnType<typeof userEvent.setup>, phone = "90000 00001") {
  await user.type(await screen.findByLabelText("Mobile number"), phone);
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

afterEach(() => {
  vi.useRealTimers();
});

describe("sign-in: slow session check", () => {
  it("explains a possible server wake-up while the check is slow, then shows the form", async () => {
    const session = deferred(NOT_AUTHENTICATED);
    mockApi({ "GET /auth/me": session.handler });
    renderSignIn();

    expect(screen.getByText("Checking your session…")).toBeInTheDocument();
    expect(screen.queryByText(/server may be waking up/)).not.toBeInTheDocument();
    expect(
      await screen.findByText(/server may be waking up/, {}, { timeout: 4_000 }),
    ).toBeInTheDocument();

    session.resolve();

    expect(await screen.findByLabelText("Mobile number")).toBeInTheDocument();
    expect(screen.queryByText(/server may be waking up/)).not.toBeInTheDocument();
  }, 10_000);
});

describe("sign-in: mobile number step", () => {
  it("validates the number before calling the backend", async () => {
    const backend = mockApi({ "GET /auth/me": NOT_AUTHENTICATED });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user, "12345");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a valid 10-digit Indian mobile number.",
    );
    expect(screen.getByLabelText("Mobile number")).toHaveAttribute("aria-invalid", "true");
    expect(backend.calls("POST /auth/request-otp")).toHaveLength(0);
  });

  it("sends the normalised number and moves to the code step", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    expect(await screen.findByRole("heading", { name: "Enter your code" })).toBeInTheDocument();
    expect(backend.calls("POST /auth/request-otp")[0]?.body).toEqual({
      phone_number: "+919000000001",
    });
    // The wording never says whether the number has an account.
    expect(screen.getByText(/is registered with OrderBridge/)).toHaveTextContent(
      "+91 90000 00001",
    );
    expect(screen.getByRole("button", { name: /Resend code in 1:00/ })).toBeDisabled();
    expect(screen.getByLabelText("Digit 1 of 6")).toHaveFocus();
  });

  it("never renders a code or other unexpected fields sent in the response", async () => {
    // The backend never sends a code; if anything code-like ever arrived,
    // the response schema drops it and nothing displays it.
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, {
        ...GENERIC_ACCEPTED,
        debug_otp: "482913",
        code: "482913",
        otp: "482913",
      }),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    await screen.findByRole("heading", { name: "Enter your code" });
    expect(document.body).not.toHaveTextContent("482913");
    expect(screen.queryByText("Development code")).not.toBeInTheDocument();
    for (const box of screen.getAllByRole("textbox")) {
      expect(box).toHaveValue("");
    }
  });

  it("shows a listed demo account's code in a labelled note, without filling it in", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, { ...GENERIC_ACCEPTED, demo_code: "482913" }),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    await screen.findByRole("heading", { name: "Enter your code" });
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Demo account. Your code is 482913");
    expect(note).toHaveTextContent("no SMS provider");
    for (const box of screen.getAllByRole("textbox")) {
      expect(box).toHaveValue("");
    }
  });

  it("ignores a malformed demo code", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, { ...GENERIC_ACCEPTED, demo_code: "48<b>13" }),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    await screen.findByRole("heading", { name: "Enter your code" });
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("reports a connection failure and stays on the number step", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": networkFailure(),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to reach OrderBridge. Check your connection and try again.",
    );
    expect(screen.getByLabelText("Mobile number")).toBeInTheDocument();
  });

  it("sends one request when Continue is pressed repeatedly", async () => {
    const slow = deferred(json(200, GENERIC_ACCEPTED));
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": slow.handler,
    });
    const user = userEvent.setup();
    renderSignIn();

    await user.type(await screen.findByLabelText("Mobile number"), "9000000001");
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("button", { name: /Sending code/ }));

    expect(backend.calls("POST /auth/request-otp")).toHaveLength(1);
    act(() => slow.resolve());
    expect(await screen.findByRole("heading", { name: "Enter your code" })).toBeInTheDocument();
  });
});

describe("sign-in: code step", () => {
  it("signs a retailer in and opens the retailer workspace", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
      "POST /auth/verify-otp": json(200, retailerUser),
    });
    const user = userEvent.setup();
    const { queryClient } = renderSignIn();

    await submitPhone(user);
    await screen.findByLabelText("Digit 1 of 6");
    await user.keyboard("123456");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/retailer"));
    expect(backend.calls("POST /auth/verify-otp")[0]?.body).toEqual({
      phone_number: "+919000000001",
      code: "123456",
    });
    // The session comes from the backend's response; nothing is stored in
    // the browser by the frontend.
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser);
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
    expect(document.cookie).toBe("");
  });

  it("opens the order desk workspace for an order desk user", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
      "POST /auth/verify-otp": json(200, deskUser),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user, "9000000010");
    await screen.findByLabelText("Digit 1 of 6");
    await user.paste("654321");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/desk"));
  });

  it("explains a rejected code, clears the boxes and does not sign in", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
      "POST /auth/verify-otp": INVALID_CODE,
    });
    const user = userEvent.setup();
    const { queryClient } = renderSignIn();

    await submitPhone(user);
    await screen.findByLabelText("Digit 1 of 6");
    await user.keyboard("000000");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That code is incorrect or has expired.",
    );
    for (const box of screen.getAllByRole("textbox")) {
      expect(box).toHaveValue("");
      // A rejected code is marked invalid (unlike a rate limit).
      expect(box).toHaveAttribute("aria-invalid", "true");
    }
    expect(screen.getByLabelText("Digit 1 of 6")).toHaveFocus();
    expect(mockRouter.replace).not.toHaveBeenCalled();
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull();
  });

  it("asks for all six digits before verifying", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);
    await screen.findByLabelText("Digit 1 of 6");
    await user.keyboard("123");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter all 6 digits of the code.");
    expect(backend.calls("POST /auth/verify-otp")).toHaveLength(0);
  });

  it("sends one verification when Verify is pressed repeatedly", async () => {
    const slow = deferred(json(200, retailerUser));
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
      "POST /auth/verify-otp": slow.handler,
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);
    await screen.findByLabelText("Digit 1 of 6");
    await user.keyboard("123456");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));
    await user.click(screen.getByRole("button", { name: /Verifying/ }));
    await user.keyboard("{Enter}");

    expect(screen.getByRole("button", { name: /Verifying/ })).toBeDisabled();
    expect(backend.calls("POST /auth/verify-otp")).toHaveLength(1);
    act(() => slow.resolve());
    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/retailer"));
  });

  it("enables resend after the cooldown and requests a new code", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderSignIn();

    await submitPhone(user);
    expect(await screen.findByRole("button", { name: /Resend code in/ })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    const resend = screen.getByRole("button", { name: "Resend code" });
    expect(resend).toBeEnabled();

    await user.click(resend);

    expect(await screen.findByText("A new code has been requested.")).toBeInTheDocument();
    expect(backend.calls("POST /auth/request-otp")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Resend code in 1:00/ })).toBeDisabled();
  });

  it("goes back to the number step with the number kept", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);
    await user.click(await screen.findByRole("button", { name: "Change number" }));

    expect(await screen.findByLabelText("Mobile number")).toHaveValue("9000000001");
  });
});

describe("sign-in: rate limits (429)", () => {
  it("explains a refused code request and holds Continue for that number", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(429, TOO_MANY, { "Retry-After": "900" }),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Please wait 15 minutes and try again.",
    );
    const held = screen.getByRole("button", { name: "Try again in 15:00" });
    expect(held).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(backend.calls("POST /auth/request-otp")).toHaveLength(1);

    // Another number is not held by this number's window, and the first
    // number's message is no longer shown.
    const input = screen.getByLabelText("Mobile number");
    await user.clear(input);
    await user.type(input, "9000000002");
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
    expect(screen.queryByText(/Too many attempts/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Back to the first number: its message and hold return.
    await user.clear(input);
    await user.type(input, "90000 00001");
    expect(screen.getByRole("alert")).toHaveTextContent("Too many attempts.");
    expect(screen.getByRole("button", { name: /Try again in/ })).toBeDisabled();
  });

  it("uses a general wait message when Retry-After is missing", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(429, TOO_MANY),
    });
    const user = userEvent.setup();
    renderSignIn();

    await submitPhone(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Please wait a few minutes and try again.",
    );
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("keeps the digits after a refused verification and waits out Retry-After", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": json(200, GENERIC_ACCEPTED),
      "POST /auth/verify-otp": sequence(
        json(429, TOO_MANY, { "Retry-After": "30" }),
        json(200, retailerUser),
      ),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderSignIn();

    await submitPhone(user);
    await screen.findByLabelText("Digit 1 of 6");
    await user.keyboard("123456");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Please wait 30 seconds and try again.",
    );
    // A rate limit says nothing about the code: the digits stay, and they
    // are not marked invalid.
    expect(screen.getAllByRole("textbox").map((box) => (box as HTMLInputElement).value)).toEqual(
      ["1", "2", "3", "4", "5", "6"],
    );
    for (const box of screen.getAllByRole("textbox")) {
      expect(box).not.toHaveAttribute("aria-invalid");
    }
    expect(screen.getByRole("button", { name: "Try again in 0:30" })).toBeDisabled();
    expect(backend.calls("POST /auth/verify-otp")).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/retailer"));
    expect(backend.calls("POST /auth/verify-otp")[1]?.body).toEqual({
      phone_number: "+919000000001",
      code: "123456",
    });
  });

  it("extends the resend countdown to a refused resend's Retry-After", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "POST /auth/request-otp": sequence(
        json(200, GENERIC_ACCEPTED),
        json(429, TOO_MANY, { "Retry-After": "600" }),
      ),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderSignIn();

    await submitPhone(user);
    await screen.findByRole("button", { name: /Resend code in/ });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    await user.click(screen.getByRole("button", { name: "Resend code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Please wait 10 minutes and try again.",
    );
    expect(screen.getByRole("button", { name: "Resend code in 10:00" })).toBeDisabled();
  });
});

describe("sign-in: existing session", () => {
  it("sends a signed-in user straight to their workspace", async () => {
    const backend = mockApi({ "GET /auth/me": json(200, deskUser) });
    renderSignIn();

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/desk"));
    expect(screen.queryByLabelText("Mobile number")).not.toBeInTheDocument();
    expect(backend.calls("GET /auth/me")).toHaveLength(1);
  });

  it("denies a signed-in account with an unknown role", async () => {
    mockApi({ "GET /auth/me": json(200, { ...deskUser, role: "salesman" }) });
    renderSignIn();

    expect(
      await screen.findByRole("heading", { name: "You do not have access to this page" }),
    ).toBeInTheDocument();
    expect(mockRouter.replace).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
