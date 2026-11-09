import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { SessionControls } from "@/features/auth/components/session-controls";
import {
  deferred,
  deskUser,
  json,
  mockApi,
  noContent,
  retailerUser,
} from "@/test/api-mock";
import { mockRouter, setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { MobileNav } from "./mobile-nav";

/**
 * The header's sign-out controls (desktop header and phone menu). The shell
 * lives in the root layout and stays mounted across sign-out and sign-in,
 * so these tests also cover a second session in the same mounted header.
 */

const FAILED = json(500, { detail: "Internal Server Error" });

function spinnerIn(element: HTMLElement): Element | null {
  return element.querySelector(".animate-spin");
}

/** A request that never finishes, unrelated to signing out. */
function UnrelatedPendingRequest() {
  useQuery({ queryKey: ["unrelated"], queryFn: () => new Promise<never>(() => {}) });
  return null;
}

async function renderHeader(logout: Parameters<typeof mockApi>[0][string]) {
  setPathname("/retailer");
  const backend = mockApi({
    "GET /auth/me": json(200, retailerUser),
    "POST /auth/logout": logout,
  });
  const result = renderWithSession(
    <>
      <SessionControls />
      <UnrelatedPendingRequest />
    </>,
  );
  const button = await screen.findByRole("button", { name: "Sign out" });
  return { ...result, backend, button, user: userEvent.setup() };
}

describe("header sign-out", () => {
  it("is enabled with the sign-out icon while signed in, despite other pending requests", async () => {
    const { button, queryClient } = await renderHeader(noContent());

    expect(queryClient.isFetching({ queryKey: ["unrelated"] })).toBe(1);
    expect(button).toBeEnabled();
    expect(spinnerIn(button)).toBeNull();
    // At least 44 x 44 px.
    expect(button).toHaveClass("h-11", "min-w-11");
  });

  it("shows a spinner and is disabled only while signing out, then goes to sign-in", async () => {
    const logout = deferred(noContent());
    const { backend, button, queryClient, user } = await renderHeader(logout.handler);

    await user.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(spinnerIn(button)).not.toBeNull();
    expect(mockRouter.replace).not.toHaveBeenCalled();

    logout.resolve();

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/sign-in"));
    expect(backend.calls("POST /auth/logout")).toHaveLength(1);
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("restores the button and shows an error when signing out fails", async () => {
    const { button, queryClient, user } = await renderHeader(FAILED);

    await user.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign out. Try again.");
    expect(button).toBeEnabled();
    expect(spinnerIn(button)).toBeNull();
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser);
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it("is enabled again for the next session in the same mounted header", async () => {
    const { button, queryClient, user } = await renderHeader(noContent());

    await user.click(button);
    await waitFor(() => expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull());

    // Signing in again without reloading the page.
    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, deskUser));

    const next = await screen.findByRole("button", { name: "Sign out" });
    expect(next).toBeEnabled();
    expect(spinnerIn(next)).toBeNull();
  });

  it("does not carry a sign-out error over to the next session", async () => {
    const { button, queryClient, user } = await renderHeader(FAILED);

    await user.click(button);
    await screen.findByRole("alert");

    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, deskUser));

    await screen.findByText("Order Desk");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });
});

describe("phone menu sign-out", () => {
  async function renderMenu(logout: Parameters<typeof mockApi>[0][string]) {
    setPathname("/retailer");
    mockApi({ "GET /auth/me": json(200, retailerUser), "POST /auth/logout": logout });
    const result = renderWithSession(<MobileNav />);
    await waitFor(() =>
      expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
    );
    const user = userEvent.setup();
    const openMenu = async () => {
      await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
      return screen.findByRole("dialog");
    };
    return { ...result, user, openMenu };
  }

  it("is enabled, pends while signing out, then closes the menu", async () => {
    const logout = deferred(noContent());
    const { user, openMenu } = await renderMenu(logout.handler);

    const menu = await openMenu();
    const button = within(menu).getByRole("button", { name: "Sign out" });
    expect(button).toBeEnabled();
    expect(spinnerIn(button)).toBeNull();

    await user.click(button);
    const pending = await within(menu).findByRole("button", { name: "Signing out…" });
    expect(pending).toBeDisabled();
    expect(spinnerIn(pending)).not.toBeNull();

    logout.resolve();

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/sign-in"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("restores the button and shows an error when signing out fails", async () => {
    const { user, openMenu } = await renderMenu(FAILED);

    const menu = await openMenu();
    await user.click(within(menu).getByRole("button", { name: "Sign out" }));

    expect(await within(menu).findByRole("alert")).toHaveTextContent(
      "Could not sign out. Try again.",
    );
    expect(within(menu).getByRole("button", { name: "Sign out" })).toBeEnabled();
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it("is enabled again for the next session", async () => {
    const { user, queryClient, openMenu } = await renderMenu(noContent());

    await user.click(within(await openMenu()).getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull());

    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, deskUser));

    const button = within(await openMenu()).getByRole("button", { name: "Sign out" });
    expect(button).toBeEnabled();
    expect(spinnerIn(button)).toBeNull();
  });
});
