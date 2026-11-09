import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi } from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import { DeskHome } from "./desk-home";

function page(total: number) {
  return { items: [], total, limit: 50, offset: 0 };
}

async function renderHome() {
  const backend = mockApi({
    "GET /auth/me": json(200, deskUser),
    "GET /desk/orders": (request) =>
      json(200, page(request.query.get("status") === "on_hold" ? 1 : request.query.get("status") === "pending_review" ? 3 : 0))(request),
    "GET /captures": json(200, { items: [], total: 2, limit: 20, offset: 0 }),
    "GET /desk/exports/eligible": json(200, { order_count: 5, net_total: "100.00" }),
  });
  const result = renderWithSession(<DeskHome />);
  await waitFor(() => expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser));
  return { backend, user: userEvent.setup() };
}

describe("DeskSummary", () => {
  it("shows what needs attention, each a way into that work", async () => {
    await renderHome();
    const summary = screen.getByRole("region", { name: "At a glance" });

    const toReview = await within(summary).findByRole("button", { name: /To review/ });
    await waitFor(() => expect(toReview).toHaveTextContent("3"));
    expect(toReview).toHaveAttribute("aria-pressed", "true");
    expect(within(summary).getByRole("button", { name: /On credit hold/ })).toHaveTextContent("1");
    expect(within(summary).getByRole("link", { name: /Messages to review/ })).toHaveAttribute(
      "href",
      "/desk/captures",
    );
    await waitFor(() =>
      expect(within(summary).getByRole("link", { name: /Messages to review/ })).toHaveTextContent("2"),
    );
    const tally = within(summary).getByRole("link", { name: /Waiting for Tally/ });
    expect(tally).toHaveAttribute("href", "/desk/exports");
    await waitFor(() => expect(tally).toHaveTextContent("5"));
  });

  it("asks for the pending orders once: the count shares the queue's request", async () => {
    const { backend } = await renderHome();
    const summary = screen.getByRole("region", { name: "At a glance" });
    await waitFor(() =>
      expect(within(summary).getByRole("button", { name: /To review/ })).toHaveTextContent("3"),
    );
    await screen.findByText(/No orders/i).catch(() => undefined);

    const pending = backend
      .calls("GET /desk/orders")
      .filter((call) => call.query.get("status") === "pending_review");
    expect(pending).toHaveLength(1);
    // Other counts still have their own request.
    expect(
      backend.calls("GET /desk/orders").filter((call) => call.query.get("status") === "on_hold"),
    ).toHaveLength(1);
  });

  it("filters the queue when a status tile is chosen", async () => {
    const { user, backend } = await renderHome();
    const summary = screen.getByRole("region", { name: "At a glance" });

    await user.click(within(summary).getByRole("button", { name: /On credit hold/ }));

    expect(within(summary).getByRole("button", { name: /On credit hold/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      within(screen.getByRole("group", { name: "Filter by status" })).getByRole("button", { name: "On hold" }),
    ).toHaveAttribute("aria-pressed", "true");
    await waitFor(() =>
      expect(
        backend.calls("GET /desk/orders").some(
          (r) => r.query.get("status") === "on_hold" && r.query.get("limit") === "50",
        ),
      ).toBe(true),
    );
  });
});
