import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi } from "@/test/api-mock";
import { setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { MainNav } from "./main-nav";
import { isNavItemActive, type NavItem } from "./navigation";

async function renderDeskNavigation(pathname: string) {
  setPathname(pathname);
  mockApi({ "GET /auth/me": json(200, deskUser) });
  const { queryClient } = renderWithSession(<MainNav />);
  await waitFor(() => expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser));
}

describe("order desk navigation", () => {
  it("links to the order queue and Tally export", async () => {
    await renderDeskNavigation("/desk");

    expect(screen.getByRole("link", { name: "Order queue" })).toHaveAttribute("href", "/desk");
    expect(screen.getByRole("link", { name: "Tally export" })).toHaveAttribute(
      "href",
      "/desk/exports",
    );
  });

  it.each([
    ["/desk", "Order queue"],
    ["/desk/orders/44444444-4444-4444-8444-444444444444", "Order queue"],
    ["/desk/exports", "Tally export"],
    ["/desk/exports/55555555-5555-4555-8555-555555555555", "Tally export"],
  ])("on %s, marks only %s as the current page", async (pathname, current) => {
    await renderDeskNavigation(pathname);

    for (const name of ["Order queue", "Tally export"]) {
      const link = screen.getByRole("link", { name });
      if (name === current) {
        expect(link).toHaveAttribute("aria-current", "page");
      } else {
        expect(link).not.toHaveAttribute("aria-current");
      }
    }
  });
});

describe("isNavItemActive", () => {
  const Icon = (() => null) as unknown as NavItem["icon"];
  const queue: NavItem = {
    href: "/desk",
    label: "Order queue",
    icon: Icon,
    activePrefixes: ["/desk/orders"],
  };

  it("with activePrefixes, matches href exactly and the prefixes' sub-routes", () => {
    expect(isNavItemActive("/desk", queue)).toBe(true);
    expect(isNavItemActive("/desk/orders", queue)).toBe(true);
    expect(isNavItemActive("/desk/orders/abc", queue)).toBe(true);
    expect(isNavItemActive("/desk/exports", queue)).toBe(false);
    expect(isNavItemActive("/desk/ordersx", queue)).toBe(false);
  });

  it("without them, keeps the existing prefix and exact behaviour", () => {
    const orders: NavItem = { href: "/retailer/orders", label: "Orders", icon: Icon };
    const catalogue: NavItem = { href: "/retailer", label: "Catalogue", icon: Icon, exact: true };

    expect(isNavItemActive("/retailer/orders/abc", orders)).toBe(true);
    expect(isNavItemActive("/retailer/cart", catalogue)).toBe(false);
    expect(isNavItemActive("/retailer", catalogue)).toBe(true);
  });
});
