import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, noContent, NOT_AUTHENTICATED } from "@/test/api-mock";
import { setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { SignInScreen } from "./sign-in-screen";

// A test fixture, not a deployment value.
const KEY = "Qm7vT2xLp9RkW4nZc8HsJ3dYf6GbN1aE5uVo0iKt";
const OFFERED = json(200, { enabled: true, active: false });
const ACTIVE = json(200, { enabled: true, active: true });

function renderSignIn() {
  setPathname("/sign-in");
  return renderWithSession(<SignInScreen />);
}

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Reviewer access" }));
  return screen.getByLabelText("Reviewer access key");
}

describe("sign-in: reviewer access", () => {
  it("is not shown when the deployment does not offer it", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "GET /auth/demo-access": json(200, { enabled: false, active: false }),
    });
    renderSignIn();

    await screen.findByLabelText("Mobile number");
    await waitFor(() => expect(backend.calls("GET /auth/demo-access")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Reviewer access" })).not.toBeInTheDocument();
  });

  it("is not shown, and sign-in still works, when the status check fails", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "GET /auth/demo-access": json(500, { detail: "Internal Server Error" }),
    });
    renderSignIn();

    await screen.findByLabelText("Mobile number");
    await waitFor(() => expect(backend.calls("GET /auth/demo-access")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Reviewer access" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts collapsed, with a masked key field that is not autofilled", async () => {
    mockApi({ "GET /auth/me": NOT_AUTHENTICATED, "GET /auth/demo-access": OFFERED });
    const user = userEvent.setup();
    renderSignIn();

    const toggle = await screen.findByRole("button", { name: "Reviewer access" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Reviewer access key")).not.toBeInTheDocument();

    const field = await openPanel(user);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("autocomplete", "off");
  });

  it("asks for a key before calling the backend", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "GET /auth/demo-access": OFFERED,
    });
    const user = userEvent.setup();
    renderSignIn();

    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "Unlock reviewer access" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the reviewer access key.");
    expect(backend.calls("POST /auth/demo-access")).toHaveLength(0);
  });

  it("sends the key once, clears it and shows that access is on", async () => {
    const backend = mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "GET /auth/demo-access": OFFERED,
      "POST /auth/demo-access": noContent(),
    });
    const user = userEvent.setup();
    renderSignIn();

    const field = await openPanel(user);
    await user.type(field, KEY);
    await user.click(screen.getByRole("button", { name: "Unlock reviewer access" }));

    expect(await screen.findByText("Reviewer access is on for this browser.")).toBeVisible();
    expect(backend.calls("POST /auth/demo-access")).toHaveLength(1);
    expect(backend.calls("POST /auth/demo-access")[0]?.body).toEqual({ access_key: KEY });
    // The key is gone from the page, and never put in a URL.
    expect(screen.queryByLabelText("Reviewer access key")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(KEY);
    for (const request of backend.requests) {
      expect(request.query.toString()).not.toContain(KEY);
    }
  });

  it("reports a wrong key and clears the field", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "GET /auth/demo-access": OFFERED,
      "POST /auth/demo-access": json(401, { detail: "The reviewer access key is not valid." }),
    });
    const user = userEvent.setup();
    renderSignIn();

    const field = await openPanel(user);
    await user.type(field, "not-the-key");
    await user.click(screen.getByRole("button", { name: "Unlock reviewer access" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That reviewer access key is not valid.",
    );
    expect(screen.getByLabelText("Reviewer access key")).toHaveValue("");
    expect(screen.getByLabelText("Reviewer access key")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByText("Reviewer access is on for this browser.")).not.toBeInTheDocument();
  });

  it("reports the rate limit", async () => {
    mockApi({
      "GET /auth/me": NOT_AUTHENTICATED,
      "GET /auth/demo-access": OFFERED,
      "POST /auth/demo-access": json(
        429,
        { detail: "Too many requests. Please wait and try again." },
        { "Retry-After": "600" },
      ),
    });
    const user = userEvent.setup();
    renderSignIn();

    await user.type(await openPanel(user), "anything");
    await user.click(screen.getByRole("button", { name: "Unlock reviewer access" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
  });

  it("shows that access is on without asking again", async () => {
    mockApi({ "GET /auth/me": NOT_AUTHENTICATED, "GET /auth/demo-access": ACTIVE });
    renderSignIn();

    expect(await screen.findByText("Reviewer access is on for this browser.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reviewer access" })).not.toBeInTheDocument();
  });
});
