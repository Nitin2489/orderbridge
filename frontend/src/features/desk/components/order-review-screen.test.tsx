import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { endClientSession, SESSION_QUERY_KEY } from "@/features/auth/api";
import {
  deferred,
  deskUser,
  json,
  mockApi,
  networkFailure,
  sequence,
} from "@/test/api-mock";
import {
  FINGERPRINT,
  NEW_FINGERPRINT,
  ORDER_ID,
  makeDeskDetail,
  makeEvent,
  makePreview,
  makePreviewLine,
} from "@/test/desk-fixtures";
import { renderWithSession } from "@/test/render";

import { deskKeys } from "../api";
import type { DeskOrderDetail } from "../types";
import { OrderReviewScreen } from "./order-review-screen";

const DETAIL = `GET /desk/orders/${ORDER_ID}`;
const PREVIEW = `GET /desk/orders/${ORDER_ID}/preview`;
const CONFIRM = `POST /desk/orders/${ORDER_ID}/confirm`;
const REJECT = `POST /desk/orders/${ORDER_ID}/reject`;

const pending = makeDeskDetail();
const confirmedOrder = makeDeskDetail({
  status: "confirmed",
  confirmed_at: "2026-11-07T12:00:00Z",
  events: [makeEvent(), makeEvent({ id: "e2", event_type: "confirmed", actor_name: "Order Desk Operator" })],
});
const heldOrder = makeDeskDetail({
  status: "on_hold",
  events: [
    makeEvent(),
    makeEvent({
      id: "e2",
      event_type: "placed_on_hold",
      actor_name: "Order Desk Operator",
      reason: "Order total exceeds the retailer's available credit.",
      details: { required_credit: "60000.00", available_credit: "40000.00", shortfall: "20000.00" },
    }),
  ],
});
const rejectedOrder = makeDeskDetail({
  status: "rejected",
  events: [makeEvent(), makeEvent({ id: "e3", event_type: "rejected", reason: "Shop closed" })],
});

const otherDeskUser = { ...deskUser, id: "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9" };

async function renderReview(routes: Parameters<typeof mockApi>[0] = {}, orderId = ORDER_ID) {
  const backend = mockApi({
    "GET /auth/me": json(200, deskUser),
    [DETAIL]: json(200, pending),
    [PREVIEW]: json(200, makePreview()),
    ...routes,
  });
  const user = userEvent.setup();
  const result = renderWithSession(<OrderReviewScreen orderId={orderId} />);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser),
  );
  return { ...result, backend, user };
}

/** "Confirm order" is disabled until the preview has loaded. */
async function openConfirm(user: ReturnType<typeof userEvent.setup>) {
  const button = await screen.findByRole("button", { name: "Confirm order" });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
}

async function startConfirm(user: ReturnType<typeof userEvent.setup>) {
  await openConfirm(user);
  await user.click(screen.getByRole("button", { name: /Yes, confirm order|Retry confirming/ }));
}

function fingerprints(backend: Awaited<ReturnType<typeof renderReview>>["backend"]) {
  return backend
    .calls(CONFIRM)
    .map((request) => (request.body as { approved_fingerprint: string }).approved_fingerprint);
}

describe("OrderReviewScreen: detail and preview", () => {
  it("shows the order, the retailer's credit, current pricing and history", async () => {
    const { backend } = await renderReview();

    expect(await screen.findByRole("heading", { name: "Order OB-2026-000123" })).toBeInTheDocument();
    const credit = screen.getByRole("region", { name: /Sharma General Store: credit now/ });
    expect(within(credit).getAllByRole("definition").map((d) => d.textContent)).toEqual([
      "₹50,000.00",
      "₹10,000.00",
      "₹40,000.00",
    ]);
    const pricing = screen.getByRole("region", { name: "Current pricing" });
    expect(await within(pricing).findByText("Grand total")).toBeInTheDocument();
    expect(within(pricing).getByText(/Amounts exclude GST/)).toBeInTheDocument();
    expect(screen.getByText("Deliver before noon")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "History" })).toHaveTextContent("Submitted");
    expect(backend.calls(PREVIEW)).toHaveLength(1);
  });

  it("marks lines whose pricing changed since submission", async () => {
    await renderReview({
      [PREVIEW]: json(
        200,
        makePreview({
          has_price_changes: true,
          lines: [makePreviewLine({ current_unit_price: "800.00", current_line_total: "8000.00", price_changed: true })],
          subtotal: "8000.00",
          grand_total: "8000.00",
        }),
      ),
    });

    expect(await screen.findByText("Changed since submission")).toBeInTheDocument();
    expect(screen.getByText(/Prices or schemes changed since the order was submitted/)).toBeInTheDocument();
    const line = screen.getByRole("listitem", { name: "1. Glucose biscuit 75 g" });
    // Stored price, then the current one (the arrow is hidden from screen readers).
    expect(line).toHaveTextContent(/₹777\.60.*changed to ₹800\.00/);
  });

  it("blocks confirmation while lines have issues, with partial totals", async () => {
    await renderReview({
      [PREVIEW]: json(
        200,
        makePreview({
          lines: [],
          issues: [
            {
              line_number: 1,
              order_item_id: "d1",
              product_id: "p1",
              sku: "SKU-BS-075",
              code: "insufficient_stock",
              message: "Insufficient stock for Glucose biscuit 75 g.",
            },
          ],
          totals_cover_all_lines: false,
          can_confirm: false,
          fingerprint: null,
          subtotal: "0.00",
          grand_total: "0.00",
        }),
      ),
    });

    expect(await screen.findByText(/Line 1 \(SKU-BS-075\):/)).toBeInTheDocument();
    expect(screen.getByText("Partial total")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm order" })).toBeDisabled();
  });

  it("warns when confirming would place the order on hold", async () => {
    await renderReview({
      [PREVIEW]: json(200, makePreview({ exceeds_credit_limit: true, available_credit: "1000.00" })),
    });

    expect(
      await screen.findByText(/Confirming will place the order on hold instead/),
    ).toBeInTheDocument();
  });

  it("treats a malformed id as not found without a request", async () => {
    const { backend } = await renderReview({}, "not-an-id");

    expect(screen.getByRole("heading", { name: "Order not found" })).toBeInTheDocument();
    expect(backend.requests.filter((r) => r.path.startsWith("/desk"))).toEqual([]);
  });

  it("shows not found for a 404", async () => {
    await renderReview({ [DETAIL]: json(404, { detail: "Order not found." }) });

    expect(await screen.findByRole("heading", { name: "Order not found" })).toBeInTheDocument();
  });

  it("offers only rejection for an inactive retailer, without pricing", async () => {
    const order = makeDeskDetail();
    order.retailer = { ...order.retailer, is_active: false };
    const { backend } = await renderReview({ [DETAIL]: json(200, order) });

    expect(await screen.findByText(/This retailer account is inactive/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm order" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject order" })).toBeInTheDocument();
    expect(backend.calls(PREVIEW)).toHaveLength(0);
  });

  it("explains a held order, shows its hold figures and offers only rejection", async () => {
    const { backend } = await renderReview({ [DETAIL]: json(200, heldOrder) });

    expect(await screen.findByText(/It can only be rejected for now/)).toBeInTheDocument();
    const history = screen.getByRole("region", { name: "History" });
    expect(history).toHaveTextContent("Placed on hold (credit limit)");
    expect(history).toHaveTextContent("₹60,000.00");
    expect(history).toHaveTextContent("₹20,000.00");
    expect(screen.queryByRole("button", { name: "Confirm order" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject order" })).toBeInTheDocument();
    expect(backend.calls(PREVIEW)).toHaveLength(0);
  });

  it("shows when each event happened, as a date and time in India", async () => {
    // 10:00 UTC is 3:30 pm in India.
    await renderReview();

    const history = await screen.findByRole("region", { name: "History" });
    const time = within(history).getByText("7 Nov 2026, 3:30 pm IST");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-11-07T10:00:00.000Z");
    expect(history).toHaveTextContent("7 Nov 2026, 3:30 pm IST · Amit Sharma");
  });

  it("uses India time even when the event crosses midnight there", async () => {
    // 18:45 UTC on 31 Dec is 12:15 am on 1 Jan in India.
    await renderReview({
      [DETAIL]: json(200, makeDeskDetail({ events: [makeEvent({ created_at: "2026-12-31T18:45:00Z" })] })),
    });

    const history = await screen.findByRole("region", { name: "History" });
    expect(history).toHaveTextContent("1 Jan 2027, 12:15 am IST");
  });

  it.each(["2026-11-07T10:00:00", "not a timestamp", ""])(
    "shows an unreadable event time %j as unavailable, never raw",
    async (createdAt) => {
      await renderReview({
        [DETAIL]: json(200, makeDeskDetail({ events: [makeEvent({ created_at: createdAt })] })),
      });

      const history = await screen.findByRole("region", { name: "History" });
      expect(history).toHaveTextContent("Submitted");
      expect(history).toHaveTextContent("Time not available · Amit Sharma");
      expect(history.querySelector("time")).toBeNull();
      if (createdAt) {
        expect(history).not.toHaveTextContent(createdAt);
      }
    },
  );

  it("links an exported event to its export batch", async () => {
    const batchId = "55555555-5555-4555-8555-555555555555";
    const order = makeDeskDetail({
      status: "exported",
      events: [
        makeEvent(),
        makeEvent({
          id: "e4",
          event_type: "exported",
          actor_name: "Order Desk Operator",
          details: { batch_id: batchId, batch_number: "EX-2026-000001", order_net_total: "7776.00" },
        }),
      ],
    });
    await renderReview({ [DETAIL]: json(200, order) });

    const history = await screen.findByRole("region", { name: "History" });
    expect(history).toHaveTextContent("Exported for billing");
    expect(within(history).getByRole("link", { name: "EX-2026-000001" })).toHaveAttribute(
      "href",
      `/desk/exports/${batchId}`,
    );
  });

  it("shows no batch link when an exported event's details are malformed", async () => {
    const order = makeDeskDetail({
      status: "exported",
      events: [
        makeEvent({
          event_type: "exported",
          details: { batch_id: "../../retailer", batch_number: "EX-2026-000001" },
        }),
      ],
    });
    await renderReview({ [DETAIL]: json(200, order) });

    const history = await screen.findByRole("region", { name: "History" });
    expect(history).toHaveTextContent("Exported for billing");
    expect(within(history).queryByRole("link")).not.toBeInTheDocument();
  });

  it.each([
    ["confirmed", confirmedOrder],
    ["rejected", rejectedOrder],
  ])("is view-only for a %s order", async (_status, order) => {
    await renderReview({ [DETAIL]: json(200, order) });

    expect(await screen.findByRole("heading", { name: "Order OB-2026-000123" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm order" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject order" })).not.toBeInTheDocument();
  });
});

describe("OrderReviewScreen: confirm", () => {
  it("asks for explicit confirmation, then sends the exact preview fingerprint", async () => {
    const { user, backend } = await renderReview({
      [CONFIRM]: json(200, { outcome: "confirmed", order: confirmedOrder }),
    });

    await openConfirm(user);
    expect(screen.getByRole("heading", { name: "Confirm order OB-2026-000123?" })).toHaveFocus();
    expect(backend.calls(CONFIRM)).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Yes, confirm order" }));

    expect(await screen.findByText("Order confirmed")).toBeInTheDocument();
    expect(fingerprints(backend)).toEqual([FINGERPRINT]);
    // The cache now holds the confirmed order: its totals are final.
    expect(screen.getByRole("heading", { name: "Confirmed totals" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm order" })).not.toBeInTheDocument();
  });

  it("shows a credit hold distinctly from a confirmation", async () => {
    const { user } = await renderReview({
      [CONFIRM]: json(200, {
        outcome: "on_hold_credit_limit",
        required_credit: "60000.00",
        available_credit: "40000.00",
        order: heldOrder,
      }),
    });

    await startConfirm(user);

    const status = await screen.findByText("Placed on hold: not confirmed");
    expect(status.closest("[role=status]")).toHaveTextContent(
      "The total of ₹60,000.00 exceeds the retailer's available credit of ₹40,000.00",
    );
    expect(screen.queryByText("Order confirmed")).not.toBeInTheDocument();
    expect(screen.getByText("On hold")).toBeInTheDocument();
  });

  it("reports an order that was already confirmed", async () => {
    const { user } = await renderReview({
      [CONFIRM]: json(200, { outcome: "already_confirmed", order: confirmedOrder }),
    });

    await startConfirm(user);

    expect(await screen.findByText("Already confirmed")).toBeInTheDocument();
  });

  it("after an outdated preview, shows the new figures and requires a fresh confirmation", async () => {
    const fresh = makePreview({
      fingerprint: NEW_FINGERPRINT,
      has_price_changes: true,
      lines: [makePreviewLine({ current_unit_price: "800.00", price_changed: true })],
    });
    const { user, backend } = await renderReview({
      [CONFIRM]: sequence(
        json(409, { detail: "Prices changed.", preview: fresh }),
        json(200, { outcome: "confirmed", order: confirmedOrder }),
      ),
    });

    await startConfirm(user);

    expect(await screen.findByText(/Review the updated pricing and confirm again/)).toBeInTheDocument();
    expect(screen.getByText("Changed since submission")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, confirm order" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review again" }));
    await startConfirm(user);

    await screen.findByText("Order confirmed");
    expect(fingerprints(backend)).toEqual([FINGERPRINT, NEW_FINGERPRINT]);
    // The new preview came from the 409, not from a new preview request.
    expect(backend.calls(PREVIEW)).toHaveLength(1);
  });

  it("shows line issues from a 422 and re-prices", async () => {
    const { user, backend } = await renderReview({
      [CONFIRM]: json(422, {
        detail: "Some lines cannot be confirmed.",
        issues: [
          {
            line_number: 1,
            order_item_id: "d1",
            product_id: "p1",
            sku: "SKU-BS-075",
            code: "insufficient_stock",
            message: "Insufficient stock.",
          },
        ],
      }),
    });

    await startConfirm(user);

    const alert = await screen.findByText(/Some lines cannot be confirmed. Nothing was changed./);
    expect(alert.closest("[role=alert]")).toHaveTextContent("Line 1 (SKU-BS-075): Insufficient stock.");
    await waitFor(() => expect(backend.calls(PREVIEW)).toHaveLength(2));
  });

  it("re-reads the order after an ambiguous 409", async () => {
    const { user, backend } = await renderReview({
      [DETAIL]: sequence(json(200, pending), json(200, heldOrder)),
      [CONFIRM]: json(409, { detail: "This order is not awaiting review." }),
    });

    await startConfirm(user);

    expect(await screen.findByText(/changed since it was loaded/)).toBeInTheDocument();
    expect(await screen.findByText("On hold")).toBeInTheDocument();
    expect(backend.calls(DETAIL)).toHaveLength(2);
  });

  it("after an uncertain failure, shows a completed confirmation from the re-read", async () => {
    const { user, backend } = await renderReview({
      [DETAIL]: sequence(json(200, pending), json(200, confirmedOrder)),
      [CONFIRM]: networkFailure(),
    });

    await startConfirm(user);

    expect(await screen.findByText(/could not tell whether the confirmation went through/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Confirmed totals" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(backend.calls(CONFIRM)).toHaveLength(1);
  });

  it("after an uncertain failure on a still-pending order, retries only on request, with the same fingerprint", async () => {
    const { user, backend } = await renderReview({
      [CONFIRM]: sequence(
        json(502, { detail: "Bad gateway" }),
        json(200, { outcome: "confirmed", order: confirmedOrder }),
      ),
    });

    await startConfirm(user);
    await screen.findByText(/could not tell whether the confirmation went through/);
    // Never retried automatically.
    expect(backend.calls(CONFIRM)).toHaveLength(1);

    const retry = await screen.findByRole("button", { name: "Retry confirming" });
    await waitFor(() => expect(retry).toBeEnabled());
    await user.click(retry);

    await screen.findByText("Order confirmed");
    expect(fingerprints(backend)).toEqual([FINGERPRINT, FINGERPRINT]);
  });

  it("sends one request when confirmed repeatedly", async () => {
    const slow = deferred(json(200, { outcome: "confirmed", order: confirmedOrder }));
    const { user, backend } = await renderReview({ [CONFIRM]: slow.handler });

    await startConfirm(user);
    const busy = screen.getByRole("button", { name: /Confirming/ });
    expect(busy).toBeDisabled();
    await user.click(busy);
    expect(screen.getByRole("button", { name: "Reject order" })).toBeDisabled();
    expect(backend.calls(CONFIRM)).toHaveLength(1);

    act(() => slow.resolve());
    await screen.findByText("Order confirmed");
    expect(backend.calls(CONFIRM)).toHaveLength(1);
  });
});

describe("OrderReviewScreen: reject", () => {
  it("requires a reason of 3 to 500 characters", async () => {
    const { user, backend } = await renderReview();

    await user.click(await screen.findByRole("button", { name: "Reject order" }));
    const field = screen.getByLabelText("Reason for rejection");
    expect(field).toHaveFocus();
    await user.type(field, "  ab  ");
    await user.click(screen.getByRole("button", { name: "Reject order" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Enter a reason of 3 to 500 characters.");
    expect(backend.calls(REJECT)).toHaveLength(0);
  });

  it("rejects with the trimmed reason and shows the result", async () => {
    const { user, backend } = await renderReview({
      [REJECT]: json(200, {
        already_rejected: false,
        rejection: makeEvent({ id: "e3", event_type: "rejected", reason: "Shop closed" }),
        order: rejectedOrder,
      }),
    });

    await user.click(await screen.findByRole("button", { name: "Reject order" }));
    await user.type(screen.getByLabelText("Reason for rejection"), "  Shop closed  ");
    await user.click(screen.getByRole("button", { name: "Reject order" }));

    expect(await screen.findByText("Order rejected")).toBeInTheDocument();
    expect(backend.calls(REJECT)[0]?.body).toEqual({ reason: "Shop closed" });
    expect(screen.getByRole("region", { name: "History" })).toHaveTextContent("Reason: Shop closed");
  });

  it("reports an order that was already rejected", async () => {
    const { user } = await renderReview({
      [REJECT]: json(200, { already_rejected: true, rejection: null, order: rejectedOrder }),
    });

    await user.click(await screen.findByRole("button", { name: "Reject order" }));
    await user.type(screen.getByLabelText("Reason for rejection"), "Duplicate order");
    await user.click(screen.getByRole("button", { name: "Reject order" }));

    expect(await screen.findByText("Already rejected")).toBeInTheDocument();
  });

  it("re-reads the order when it can no longer be rejected", async () => {
    const { user, backend } = await renderReview({
      [DETAIL]: sequence(json(200, pending), json(200, confirmedOrder)),
      [REJECT]: json(409, { detail: "This order can no longer be rejected." }),
    });

    await user.click(await screen.findByRole("button", { name: "Reject order" }));
    await user.type(screen.getByLabelText("Reason for rejection"), "Duplicate order");
    await user.click(screen.getByRole("button", { name: "Reject order" }));

    expect(await screen.findByText(/the rejection was not applied/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Confirmed totals" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Reason for rejection")).not.toBeInTheDocument();
    expect(backend.calls(DETAIL)).toHaveLength(2);
  });

  it("after an uncertain failure, retries only on request with the same reason", async () => {
    const { user, backend } = await renderReview({
      [REJECT]: sequence(
        networkFailure(),
        json(200, { already_rejected: false, rejection: null, order: rejectedOrder }),
      ),
    });

    await user.click(await screen.findByRole("button", { name: "Reject order" }));
    await user.type(screen.getByLabelText("Reason for rejection"), "Duplicate order");
    await user.click(screen.getByRole("button", { name: "Reject order" }));
    await screen.findByText(/could not tell whether the rejection went through/);
    expect(backend.calls(REJECT)).toHaveLength(1);

    const retry = await screen.findByRole("button", { name: "Retry rejecting" });
    await waitFor(() => expect(retry).toBeEnabled());
    await user.click(retry);

    await screen.findByText("Order rejected");
    expect(backend.calls(REJECT).map((r) => r.body)).toEqual([
      { reason: "Duplicate order" },
      { reason: "Duplicate order" },
    ]);
  });
});

describe("OrderReviewScreen: a session change while an action is in flight", () => {
  type Rendered = Awaited<ReturnType<typeof renderReview>>;

  function cachedStatus(queryClient: Rendered["queryClient"]) {
    return queryClient.getQueryData<DeskOrderDetail>(deskKeys.detail(ORDER_ID))?.status;
  }

  async function openReject(user: Rendered["user"], reason = "Duplicate order") {
    await user.click(await screen.findByRole("button", { name: "Reject order" }));
    await user.type(screen.getByLabelText("Reason for rejection"), reason);
    await user.click(screen.getByRole("button", { name: "Reject order" }));
  }

  /** Another desk user is now signed in (the review is for them now). */
  function switchUser(queryClient: Rendered["queryClient"]) {
    act(() => {
      queryClient.setQueryData(SESSION_QUERY_KEY, otherDeskUser);
    });
  }

  async function settle(queryClient: Rendered["queryClient"]) {
    await waitFor(() => expect(queryClient.isMutating()).toBe(0));
    // Give any (wrongly) triggered refetch or render a chance to happen.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }

  it("ignores a confirmation that succeeds after another user signed in", async () => {
    const slow = deferred(json(200, { outcome: "confirmed", order: confirmedOrder }));
    const { user, queryClient, backend } = await renderReview({ [CONFIRM]: slow.handler });

    await startConfirm(user);
    switchUser(queryClient);
    // The new user's review starts afresh: no half-finished confirm step.
    expect(
      await screen.findByRole("button", { name: "Confirm order" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Confirm order OB-/ })).not.toBeInTheDocument();

    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText("Order confirmed")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Totals at submission" })).toBeInTheDocument();
    expect(cachedStatus(queryClient)).toBe("pending_review");
    expect(backend.calls(DETAIL)).toHaveLength(1);
  });

  it("ignores an outdated-pricing result after another user signed in", async () => {
    const fresh = makePreview({ fingerprint: NEW_FINGERPRINT });
    const slow = deferred(json(409, { detail: "Prices changed.", preview: fresh }));
    const { user, queryClient } = await renderReview({ [CONFIRM]: slow.handler });

    await startConfirm(user);
    switchUser(queryClient);
    act(() => slow.resolve());
    await settle(queryClient);

    // The old session's fresh preview is not written into the new session.
    expect(
      queryClient.getQueryData<{ fingerprint: string }>(deskKeys.preview(ORDER_ID))?.fingerprint,
    ).toBe(FINGERPRINT);
    expect(screen.queryByText(/confirm again/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review again" })).not.toBeInTheDocument();
  });

  it("does not re-read or offer a retry for an uncertain confirmation from the old session", async () => {
    const slow = deferred(networkFailure());
    const { user, queryClient, backend } = await renderReview({ [CONFIRM]: slow.handler });

    await startConfirm(user);
    switchUser(queryClient);
    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText(/could not tell whether/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry confirming" })).not.toBeInTheDocument();
    expect(backend.calls(DETAIL)).toHaveLength(1);
    expect(backend.calls(CONFIRM)).toHaveLength(1);
  });

  it("ignores a confirmation that resolves after sign-out", async () => {
    const slow = deferred(json(200, { outcome: "confirmed", order: confirmedOrder }));
    const { user, queryClient } = await renderReview({ [CONFIRM]: slow.handler });

    await startConfirm(user);
    await act(async () => {
      await endClientSession(queryClient);
    });
    act(() => slow.resolve());
    await settle(queryClient);

    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull();
    expect(screen.queryByText("Order confirmed")).not.toBeInTheDocument();
    // Sign-out dropped the order; the late response did not put it back.
    expect(cachedStatus(queryClient)).not.toBe("confirmed");
  });

  it("ignores a rejection that succeeds after another user signed in", async () => {
    const slow = deferred(
      json(200, { already_rejected: false, rejection: null, order: rejectedOrder }),
    );
    const { user, queryClient, backend } = await renderReview({ [REJECT]: slow.handler });

    await openReject(user);
    switchUser(queryClient);
    // The new user's review starts afresh: the reason form is closed.
    expect(await screen.findByRole("button", { name: "Reject order" })).toBeEnabled();
    expect(screen.queryByLabelText("Reason for rejection")).not.toBeInTheDocument();

    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText("Order rejected")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(cachedStatus(queryClient)).toBe("pending_review");
    expect(backend.calls(DETAIL)).toHaveLength(1);
  });

  it("does not re-read or show a failure for a rejection from the old session", async () => {
    const slow = deferred(json(409, { detail: "This order can no longer be rejected." }));
    const { user, queryClient, backend } = await renderReview({ [REJECT]: slow.handler });

    await openReject(user);
    switchUser(queryClient);
    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText(/the rejection was not applied/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry rejecting" })).not.toBeInTheDocument();
    expect(backend.calls(DETAIL)).toHaveLength(1);
  });

  it("ignores a rejection that resolves after sign-out", async () => {
    const slow = deferred(
      json(200, { already_rejected: false, rejection: null, order: rejectedOrder }),
    );
    const { user, queryClient } = await renderReview({ [REJECT]: slow.handler });

    await openReject(user);
    await act(async () => {
      await endClientSession(queryClient);
    });
    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText("Order rejected")).not.toBeInTheDocument();
    expect(cachedStatus(queryClient)).not.toBe("rejected");
  });

  it("still applies a result when the same user stays signed in", async () => {
    const slow = deferred(
      json(200, { already_rejected: false, rejection: null, order: rejectedOrder }),
    );
    const { user, queryClient } = await renderReview({ [REJECT]: slow.handler });

    await openReject(user);
    // A session re-check that returns the same user changes nothing.
    act(() => {
      queryClient.setQueryData(SESSION_QUERY_KEY, { ...deskUser });
    });
    act(() => slow.resolve());

    expect(await screen.findByText("Order rejected")).toBeInTheDocument();
    expect(cachedStatus(queryClient)).toBe("rejected");
  });
});
