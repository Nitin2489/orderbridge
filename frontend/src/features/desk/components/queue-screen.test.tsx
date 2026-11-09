import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { deskUser, json, mockApi, sequence, type RecordedRequest } from "@/test/api-mock";
import { makeDeskSummary } from "@/test/desk-fixtures";
import { renderWithSession } from "@/test/render";

import { QueueScreen } from "./queue-screen";

const QUEUE = "GET /desk/orders";

const first = makeDeskSummary();
const second = makeDeskSummary({
  id: "55555555-5555-4555-8555-555555555555",
  order_number: "OB-2026-000124",
  retailer_id: "9c3d4e5f-6a7b-4c8d-9e0f-666666666666",
  retailer_business_name: "Gupta Kirana Store",
  status: "confirmed",
  grand_total: "12500.00",
});

function page(items: ReturnType<typeof makeDeskSummary>[], total = items.length, offset = 0) {
  return json(200, { items, total, limit: 50, offset });
}

function renderQueue(handler: Parameters<typeof mockApi>[0][string]) {
  const backend = mockApi({ "GET /auth/me": json(200, deskUser), [QUEUE]: handler });
  const user = userEvent.setup();
  renderWithSession(<QueueScreen />);
  return { backend, user, lastQuery: () => backend.calls(QUEUE).at(-1)?.query };
}

describe("QueueScreen", () => {
  it("opens on the pending queue and lists orders with links", async () => {
    const { lastQuery } = renderQueue(page([first]));

    expect(screen.getByText("Loading orders…")).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: "OB-2026-000123" });

    expect(link).toHaveAttribute("href", `/desk/orders/${first.id}`);
    // A 44 px tap area around the order number; the retailer filter below it
    // stays on top for its own box and is at least 24 px tall.
    expect(link).toHaveClass("tap-target");
    const filter = screen.getByRole("button", { name: /show only this retailer/ });
    expect(filter).toHaveClass("relative", "min-h-6");
    expect(lastQuery()?.get("status")).toBe("pending_review");
    expect(lastQuery()?.get("limit")).toBe("50");
    expect(lastQuery()?.get("offset")).toBe("0");
    const row = link.closest("li")!;
    expect(row).toHaveTextContent("Sharma General Store");
    expect(row).toHaveTextContent("Pending review");
    expect(row).toHaveTextContent("Submitted total ₹7,776.00");
    expect(
      within(screen.getByRole("group", { name: "Filter by status" })).getByRole("button", {
        name: "Pending review",
      }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("wraps the status chips so every status stays reachable on a phone", async () => {
    renderQueue(page([first]));
    await screen.findByRole("link", { name: "OB-2026-000123" });

    const filters = screen.getByRole("group", { name: "Filter by status" });
    expect(filters).toHaveClass("flex-wrap");
    expect(filters).not.toHaveClass("overflow-x-auto");
  });

  it("labels confirmed totals as confirmed", async () => {
    renderQueue(page([second]));

    const row = (await screen.findByRole("link", { name: "OB-2026-000124" })).closest("li")!;
    expect(row).toHaveTextContent("Confirmed total ₹12,500.00");
  });

  it("explains an empty review queue", async () => {
    renderQueue(page([]));

    expect(
      await screen.findByRole("heading", { name: "No orders waiting for review" }),
    ).toBeInTheDocument();
  });

  it("shows an error with a working retry", async () => {
    const { user } = renderQueue(sequence(json(500, { detail: "boom" }), page([first])));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong on our side");
    expect(alert).not.toHaveTextContent("boom");
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("link", { name: "OB-2026-000123" })).toBeInTheDocument();
  });

  it("lists every status with All, and searches on the backend", async () => {
    const { user, lastQuery } = renderQueue(page([first]));
    await screen.findByRole("link", { name: "OB-2026-000123" });

    await user.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => expect(lastQuery()?.has("status")).toBe(false));

    await user.type(screen.getByLabelText("Order number or retailer"), "  Sharma ");
    await waitFor(() => expect(lastQuery()?.get("search")).toBe("Sharma"));
  });

  it("filters by a retailer picked from a row, and can show all again", async () => {
    const { user, lastQuery } = renderQueue(page([first, second]));
    await screen.findByRole("link", { name: "OB-2026-000124" });

    // The label is the retailer name plus a screen-reader-only suffix. jsdom
    // has no layout, so it joins the two with a space a browser would not
    // add; the pattern accepts either.
    await user.click(
      screen.getByRole("button", {
        name: /^Gupta Kirana Store\s*: show only this retailer's orders$/,
      }),
    );

    await waitFor(() => expect(lastQuery()?.get("retailer_id")).toBe(second.retailer_id));
    expect(screen.getByText(/Retailer:/)).toHaveTextContent("Retailer: Gupta Kirana Store");

    await user.click(screen.getByRole("button", { name: "Show all retailers" }));
    await waitFor(() => expect(lastQuery()?.has("retailer_id")).toBe(false));
  });

  it("sends an inclusive date range as India-time bounds", async () => {
    const { lastQuery } = renderQueue(page([first]));
    await screen.findByRole("link", { name: "OB-2026-000123" });

    fireEvent.change(screen.getByLabelText("Placed from"), { target: { value: "2026-11-05" } });
    fireEvent.change(screen.getByLabelText("Placed to"), { target: { value: "2026-11-07" } });

    await waitFor(() => {
      expect(lastQuery()?.get("created_from")).toBe("2026-11-05T00:00:00+05:30");
      expect(lastQuery()?.get("created_to")).toBe("2026-11-08T00:00:00+05:30");
    });
  });

  it("refuses an inverted date range without sending it", async () => {
    const { lastQuery } = renderQueue(page([first]));
    await screen.findByRole("link", { name: "OB-2026-000123" });

    fireEvent.change(screen.getByLabelText("Placed from"), { target: { value: "2026-11-08" } });
    fireEvent.change(screen.getByLabelText("Placed to"), { target: { value: "2026-11-07" } });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The start date must not be after the end date.",
    );
    expect(screen.getByLabelText("Placed from")).toHaveAttribute("aria-invalid", "true");
    // The inverted range is never sent: the query drops the date filter.
    await waitFor(() => {
      expect(lastQuery()?.has("created_from")).toBe(false);
      expect(lastQuery()?.has("created_to")).toBe(false);
    });
  });

  it("loads more orders using the backend's offset", async () => {
    const { user, lastQuery } = renderQueue((request: RecordedRequest) =>
      request.query.get("offset") === "1" ? page([second], 2, 1)(request) : page([first], 2)(request),
    );
    await screen.findByRole("link", { name: "OB-2026-000123" });
    expect(screen.getByText("Showing 1 of 2 orders")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show more orders" }));

    expect(await screen.findByRole("link", { name: "OB-2026-000124" })).toBeInTheDocument();
    expect(lastQuery()?.get("offset")).toBe("1");
  });

  it("clears filters from a filtered empty result", async () => {
    const { user, lastQuery } = renderQueue((request: RecordedRequest) =>
      request.query.has("search") ? page([])(request) : page([first])(request),
    );
    await screen.findByRole("link", { name: "OB-2026-000123" });

    await user.type(screen.getByLabelText("Order number or retailer"), "nothing");
    expect(
      await screen.findByRole("heading", { name: "No orders match these filters" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(await screen.findByRole("link", { name: "OB-2026-000123" })).toBeInTheDocument();
    expect(lastQuery()?.has("search")).toBe(false);
    expect(lastQuery()?.has("status")).toBe(false);
  });
});
