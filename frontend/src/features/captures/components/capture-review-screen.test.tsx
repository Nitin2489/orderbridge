import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi, retailerUser, sequence } from "@/test/api-mock";
import {
  BISCUIT,
  CAPTURE_ID,
  DETERGENT,
  TEA,
  demoLines,
  makeCapture,
  makeLine,
} from "@/test/capture-fixtures";
import { mockRouter } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import type { CaptureDetail } from "../types";
import { CaptureReviewScreen } from "./capture-review-screen";

const DETAIL = `GET /captures/${CAPTURE_ID}`;
const LINE = (n: number) => `POST /captures/${CAPTURE_ID}/lines/${n}`;
const SUBMIT = `POST /captures/${CAPTURE_ID}/submit`;
const DISCARD = `POST /captures/${CAPTURE_ID}/discard`;
const ORDER_ID = "44444444-4444-4444-8444-444444444444";

async function renderReview(
  routes: Parameters<typeof mockApi>[0] = {},
  { role = "order_desk" as "order_desk" | "retailer" } = {},
) {
  const user = role === "order_desk" ? deskUser : retailerUser;
  const backend = mockApi({
    "GET /auth/me": json(200, user),
    [DETAIL]: json(200, makeCapture()),
    ...routes,
  });
  const result = renderWithSession(<CaptureReviewScreen captureId={CAPTURE_ID} role={role} />);
  await waitFor(() => expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(user));
  await screen.findByRole("heading", { name: "Review message" });
  return { ...result, backend, user: userEvent.setup() };
}

function lineCard(number: number) {
  return screen.getByRole("article", { name: `Line ${number}` });
}

function withLine(capture: CaptureDetail, number: number, patch: Partial<CaptureDetail["lines"][number]>) {
  return {
    ...capture,
    lines: capture.lines.map((line) => (line.line_number === number ? { ...line, ...patch } : line)),
  };
}

describe("CaptureReviewScreen", () => {
  it("shows the message unchanged, rule-based, with each line's original words and choices", async () => {
    const { user } = await renderReview();

    expect(screen.getByText(/not an AI model/)).toBeInTheDocument();
    expect(screen.getByText(/“bhaiya” \(greeting\)/)).toBeInTheDocument();
    const first = within(lineCard(1));
    expect(first.getByText(/“5 peti biscuit chhota”/)).toBeInTheDocument();
    expect(first.getByText("Suggested")).toBeInTheDocument();
    expect(first.getByText("60/100 · Possible match")).toBeInTheDocument();
    expect(first.getByText(/'chhota' does not identify a pack size/)).toBeInTheDocument();
    // Collapsed: only the suggestion, without its detailed reasons.
    expect(first.getAllByRole("radio", { name: /SKU/ })).toHaveLength(1);
    expect(first.queryByText("Weak match")).not.toBeInTheDocument();
    expect(first.queryByText(/Recognised 'biscuit'/)).not.toBeInTheDocument();
    const toggle = first.getByRole("button", { name: "Show alternatives (2)" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // Expanded: two alternatives, marked as weak, and every reason.
    await user.click(toggle);
    expect(first.getByRole("button", { name: "Hide alternatives" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(first.getAllByRole("radio", { name: /SKU/ })).toHaveLength(3);
    expect(first.getAllByText("Weak match")).toHaveLength(2);
    // The unit is never pre-selected.
    expect(first.getByRole("radio", { name: "Cases" })).not.toBeChecked();
    expect(first.getByRole("radio", { name: "Pieces" })).not.toBeChecked();
    expect(first.getByRole("button", { name: "Include line" })).toBeDisabled();

    // Tea is out of stock and says so; it is not hidden.
    expect(within(lineCard(3)).getAllByText("Out of stock").length).toBeGreaterThan(0);
  });

  it("says when the AI reader read the message", async () => {
    const description =
      "Read by an AI language model (Gemini); products were matched by OrderBridge's rules.";
    await renderReview({
      [DETAIL]: json(
        200,
        makeCapture({
          interpretation: {
            name: "gemini@1",
            kind: "language_model",
            is_ai_model: true,
            description,
            fallback_note: null,
          },
        }),
      ),
    });

    expect(await screen.findByText(new RegExp(description.replace(/[().]/g, "\\$&")))).toBeInTheDocument();
    expect(screen.queryByText(/not an AI model/)).not.toBeInTheDocument();
  });

  it("tells the desk its order goes to the queue, and a retailer that the desk confirms it", async () => {
    await renderReview();
    expect(await screen.findByText(/adds it to the order queue, where it is confirmed/)).toBeInTheDocument();
    expect(screen.queryByText(/the order desk confirms it/)).not.toBeInTheDocument();
  });

  it("tells a retailer that the order desk confirms the order", async () => {
    await renderReview({}, { role: "retailer" });
    expect(await screen.findByText(/until you create the order and the order desk confirms it/)).toBeInTheDocument();
    expect(screen.queryByText(/adds it to the order queue/)).not.toBeInTheDocument();
  });

  it("says when the AI reader was not used and the rules read the message", async () => {
    const note = "The AI reader did not answer in time. The rule-based reader was used instead.";
    await renderReview({
      [DETAIL]: json(
        200,
        makeCapture({
          interpretation: {
            name: "rules@1",
            kind: "rules",
            is_ai_model: false,
            description: "Rule-based interpretation. Not an AI model.",
            fallback_note: note,
          },
        }),
      ),
    });

    expect(await screen.findByText(note)).toBeInTheDocument();
    expect(screen.getByText(/not an AI model/)).toBeInTheDocument();
  });

  it("gives each line's quantity section and field a distinct name", async () => {
    await renderReview();

    for (const number of [1, 2, 3]) {
      const card = within(lineCard(number));
      expect(card.getByRole("region", { name: `Quantity for line ${number}` })).toBeInTheDocument();
      const field = card
        .getAllByLabelText(`Quantity for line ${number}`)
        .find((element) => element.tagName === "INPUT");
      expect(field).toBeDefined();
    }
    // Sighted users still see just "Quantity".
    expect(within(lineCard(1)).getByText("Quantity", { selector: "h4" })).toBeInTheDocument();
  });

  it("asks for the unit before confirming the quantity", async () => {
    const confirmed = withLine(makeCapture({ version: 2 }), 1, {
      unit: { confirmed: true, unit: "case", quantity: 5, quantity_cases: null },
    });
    const { user, backend } = await renderReview({ [LINE(1)]: json(200, confirmed) });
    const first = within(lineCard(1));

    await user.click(first.getByRole("button", { name: "Confirm quantity" }));
    expect(first.getByText("Choose cases or pieces.")).toBeInTheDocument();
    expect(backend.calls(LINE(1))).toHaveLength(0);

    await user.click(first.getByRole("radio", { name: "Cases" }));
    await user.click(first.getByRole("button", { name: "Confirm quantity" }));

    await waitFor(() => expect(backend.calls(LINE(1))).toHaveLength(1));
    expect(backend.calls(LINE(1))[0]?.body).toEqual({
      expected_version: 1,
      action: "confirm_unit",
      unit: "case",
      quantity: 5,
    });
    expect(await within(lineCard(1)).findByText("5 cases")).toBeInTheDocument();
  });

  it("confirms 'peti' as cases for all lines only on request, one versioned change at a time", async () => {
    const after = (version: number, confirmedUpTo: number) =>
      makeCapture({
        version,
        lines: demoLines().map((line) =>
          line.line_number <= confirmedUpTo
            ? {
                ...line,
                unit: {
                  confirmed: true,
                  unit: "case" as const,
                  quantity: line.suggested_quantity,
                  quantity_cases: null,
                },
              }
            : line,
        ),
      });
    const { user, backend } = await renderReview({
      [LINE(1)]: json(200, after(2, 1)),
      [LINE(2)]: json(200, after(3, 2)),
      [LINE(3)]: json(200, after(4, 3)),
    });

    expect(screen.getByText(/3 lines say “peti”/)).toBeInTheDocument();
    expect(backend.requests.filter((r) => r.method === "POST")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Yes, “peti” means cases" }));

    await waitFor(() => expect(backend.calls(LINE(3))).toHaveLength(1));
    expect(
      [1, 2, 3].map((n) => backend.calls(LINE(n))[0]?.body),
    ).toEqual([
      { expected_version: 1, action: "confirm_unit", unit: "case", quantity: 5 },
      { expected_version: 2, action: "confirm_unit", unit: "case", quantity: 2 },
      { expected_version: 3, action: "confirm_unit", unit: "case", quantity: 1 },
    ]);
    // Products are still undecided: nothing was included.
    expect(await within(lineCard(1)).findByText("Needs a decision")).toBeInTheDocument();
  });

  it("includes the chosen product only after the unit is confirmed", async () => {
    const ready = withLine(makeCapture({ version: 2 }), 1, {
      unit: { confirmed: true, unit: "case", quantity: 5, quantity_cases: null },
    });
    const { user, backend } = await renderReview({
      [DETAIL]: json(200, ready),
      [LINE(1)]: json(
        200,
        withLine(ready, 1, {
          resolution: "included",
          selected_product: { id: BISCUIT.product_id, sku: BISCUIT.sku, name: BISCUIT.name, units_per_case: 96 },
          unit: { confirmed: true, unit: "case", quantity: 5, quantity_cases: 5 },
        }),
      ),
    });

    await user.click(within(lineCard(1)).getByRole("button", { name: "Include line" }));

    await waitFor(() => expect(backend.calls(LINE(1))).toHaveLength(1));
    expect(backend.calls(LINE(1))[0]?.body).toEqual({
      expected_version: 2,
      action: "include",
      product_id: BISCUIT.product_id,
    });
    expect(await within(lineCard(1)).findByText("Included")).toBeInTheDocument();
  });

  it("excludes a line only with a reason", async () => {
    const { user, backend } = await renderReview({
      [LINE(3)]: json(
        200,
        withLine(makeCapture({ version: 2 }), 3, {
          resolution: "excluded",
          exclusion_reason: "Out of stock",
        }),
      ),
    });
    const tea = within(lineCard(3));

    await user.click(tea.getByRole("button", { name: "Exclude…" }));
    await user.type(tea.getByLabelText("Reason for excluding this line"), " x ");
    await user.click(tea.getByRole("button", { name: "Exclude line" }));
    expect(tea.getByText("Enter a reason of 3 to 500 characters.")).toBeInTheDocument();
    expect(backend.calls(LINE(3))).toHaveLength(0);

    await user.clear(tea.getByLabelText("Reason for excluding this line"));
    await user.type(tea.getByLabelText("Reason for excluding this line"), "  Out of stock  ");
    await user.click(tea.getByRole("button", { name: "Exclude line" }));

    await waitFor(() => expect(backend.calls(LINE(3))).toHaveLength(1));
    expect(backend.calls(LINE(3))[0]?.body).toEqual({
      expected_version: 1,
      action: "exclude",
      reason: "Out of stock",
    });
    expect(await screen.findByText("Out of stock", { selector: "p" })).toBeInTheDocument();
  });

  it("offers an alias only to the order desk, only for a corrected line", async () => {
    const { user } = await renderReview();
    const first = within(lineCard(1));
    expect(first.queryByRole("checkbox")).not.toBeInTheDocument();

    await user.click(first.getByRole("button", { name: "Show alternatives (2)" }));
    await user.click(first.getByRole("radio", { name: /Detergent Powder 1kg/ }));

    expect(
      first.getByRole("checkbox", { name: /Remember “biscuit chhota” as Detergent Powder 1kg for Sharma General Store/ }),
    ).toBeInTheDocument();
  });

  it("keeps a chosen alternative visible after hiding the others", async () => {
    const { user } = await renderReview();
    const first = within(lineCard(1));

    await user.click(first.getByRole("button", { name: "Show alternatives (2)" }));
    await user.click(first.getByRole("radio", { name: /Detergent Powder 1kg/ }));
    await user.click(first.getByRole("button", { name: "Hide alternatives" }));

    // The suggestion and the chosen product stay; one alternative is hidden.
    expect(first.getAllByRole("radio", { name: /SKU/ })).toHaveLength(2);
    expect(first.getByRole("radio", { name: /Detergent Powder 1kg/ })).toBeChecked();
    expect(first.getByRole("button", { name: "Show alternatives (1)" })).toBeInTheDocument();
  });

  it("shows every option when there is no suggestion", async () => {
    const capture = makeCapture();
    const line = capture.lines[0]!;
    const unproposed = withLine(capture, 1, {
      match: {
        ...line.match,
        proposal: null,
        alternatives: [line.match.proposal!, ...line.match.alternatives].slice(0, 2),
      },
    });
    await renderReview({ [DETAIL]: json(200, unproposed) });
    const first = within(lineCard(1));

    expect(first.getAllByRole("radio", { name: /SKU/ })).toHaveLength(2);
    expect(first.queryByRole("button", { name: /alternatives|match details/ })).not.toBeInTheDocument();
  });

  it("never offers an alias to a retailer", async () => {
    const { user } = await renderReview({}, { role: "retailer" });
    const first = within(lineCard(1));

    await user.click(first.getByRole("button", { name: "Show alternatives (2)" }));
    await user.click(first.getByRole("radio", { name: /Detergent Powder 1kg/ }));

    expect(first.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("reloads after a stale version and says why", async () => {
    const { user, backend } = await renderReview({
      [DETAIL]: sequence(json(200, makeCapture()), json(200, makeCapture({ version: 5 }))),
      [LINE(1)]: json(409, { detail: "Changed.", current_version: 5 }),
    });
    const first = within(lineCard(1));

    await user.click(first.getByRole("radio", { name: "Cases" }));
    await user.click(first.getByRole("button", { name: "Confirm quantity" }));

    expect(await screen.findByText(/Someone else changed this capture/)).toBeInTheDocument();
    await waitFor(() => expect(backend.calls(DETAIL)).toHaveLength(2));
  });

  it("creates the order and opens it on the order desk", async () => {
    const ready = makeCapture({ status: "ready", can_submit: true, version: 7, unresolved_lines: [] });
    const { user, backend } = await renderReview({
      [DETAIL]: json(200, ready),
      [SUBMIT]: json(201, {
        created: true,
        order: { id: ORDER_ID, order_number: "OB-2026-000200", status: "pending_review" },
        capture: { ...ready, status: "submitted", can_submit: false },
      }),
    });

    await user.click(screen.getByRole("button", { name: "Create order for review" }));

    await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith(`/desk/orders/${ORDER_ID}`));
    const body = backend.calls(SUBMIT)[0]?.body as Record<string, unknown>;
    expect(body.expected_version).toBe(7);
    expect(typeof body.client_request_id).toBe("string");
  });

  it("shows why lines cannot be ordered on the lines themselves", async () => {
    const ready = makeCapture({ status: "ready", can_submit: true, unresolved_lines: [] });
    const { user } = await renderReview({
      [DETAIL]: json(200, ready),
      [SUBMIT]: json(422, {
        detail: "Some included lines cannot be ordered. No order was created.",
        issues: [
          {
            line_number: 3,
            product_id: TEA.product_id,
            code: "insufficient_stock",
            message: "Insufficient stock for Tea 250g.",
          },
        ],
      }),
    });

    await user.click(screen.getByRole("button", { name: "Create order for review" }));

    expect(await within(lineCard(3)).findByText("Insufficient stock for Tea 250g.")).toBeInTheDocument();
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it("discards only with a reason", async () => {
    const { user, backend } = await renderReview({
      [DISCARD]: json(
        200,
        makeCapture({ status: "discarded", discard_reason: "Duplicate", version: 2 }),
      ),
    });

    await user.click(screen.getByRole("button", { name: "Discard message…" }));
    await user.click(screen.getByRole("button", { name: "Discard message" }));
    expect(screen.getByText("Enter a reason of 3 to 500 characters.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Reason for discarding"), "Duplicate");
    await user.click(screen.getByRole("button", { name: "Discard message" }));

    expect(await screen.findByText("This message was discarded")).toBeInTheDocument();
    expect(backend.calls(DISCARD)[0]?.body).toEqual({ expected_version: 1, reason: "Duplicate" });
    expect(screen.queryByRole("button", { name: "Create order for review" })).not.toBeInTheDocument();
  });

  it("is read-only once the order has been created", async () => {
    await renderReview({
      [DETAIL]: json(
        200,
        makeCapture({
          status: "submitted",
          order: { id: ORDER_ID, order_number: "OB-2026-000200", status: "pending_review" },
          lines: [makeLine({ resolution: "excluded", exclusion_reason: "Not needed" })],
        }),
      ),
    });

    expect(screen.getByText(/Order OB-2026-000200 was created from this message/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View the order" })).toHaveAttribute(
      "href",
      `/desk/orders/${ORDER_ID}`,
    );
    expect(screen.queryByRole("button", { name: "Change decision" })).not.toBeInTheDocument();
  });

  it("detergent and tea lines propose their products", async () => {
    await renderReview();

    expect(within(lineCard(2)).getByText("85/100 · Strong match")).toBeInTheDocument();
    expect(within(lineCard(2)).getAllByText(DETERGENT.name).length).toBeGreaterThan(0);
    expect(within(lineCard(3)).getByText("85/100 · Strong match")).toBeInTheDocument();
  });
});
