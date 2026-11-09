import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi, sequence } from "@/test/api-mock";
import {
  BATCH_ID,
  csvResponse,
  makeBatchDetail,
  withWebCrypto,
} from "@/test/export-fixtures";
import { renderWithSession } from "@/test/render";

import { saveFile } from "../save-file";
import { BatchScreen } from "./batch-screen";

vi.mock("../save-file", () => ({ saveFile: vi.fn() }));

const DETAIL = `GET /desk/exports/${BATCH_ID}`;
const CSV = `GET /desk/exports/${BATCH_ID}/csv`;
const MASTERS = `GET /desk/exports/${BATCH_ID}/tally-masters`;
const XML = `GET /desk/exports/${BATCH_ID}/tally.xml`;

function makeMasters(overrides: Record<string, unknown> = {}) {
  return {
    batch_number: "EX-2026-000001",
    voucher_type: "Sales",
    sales_ledger: "Sales Account",
    discount_ledger: "Discount Allowed",
    discount_needed: true,
    party_ledgers: ["Gupta Kirana Store", "Sharma General Store"],
    stock_items: [{ name: "Glucose Biscuit 75g", unit: "case" }],
    units: ["case"],
    missing_settings: [],
    xml_url: `/api/v1/desk/exports/${BATCH_ID}/tally.xml`,
    ...overrides,
  };
}

function xmlResponse(contentType = "application/xml; charset=utf-8"): () => Response {
  return () =>
    new Response("<ENVELOPE></ENVELOPE>", {
      status: 200,
      headers: { "content-type": contentType },
    });
}

async function renderBatch(routes: Parameters<typeof mockApi>[0] = {}, batchId = BATCH_ID) {
  const backend = mockApi({
    "GET /auth/me": json(200, deskUser),
    [DETAIL]: json(200, makeBatchDetail()),
    [CSV]: csvResponse(),
    [MASTERS]: json(200, makeMasters()),
    [XML]: xmlResponse(),
    ...routes,
  });
  const user = userEvent.setup();
  const result = renderWithSession(<BatchScreen batchId={batchId} />);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser),
  );
  return { ...result, backend, user };
}

beforeEach(() => {
  withWebCrypto();
});

describe("BatchScreen", () => {
  it("shows the batch, its checksum and its orders", async () => {
    await renderBatch();

    expect(
      await screen.findByRole("heading", { name: "Export EX-2026-000001" }),
    ).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Batch summary" });
    const values = within(summary)
      .getAllByRole("definition")
      .map((value) => value.textContent);
    expect(values).toEqual([
      "7 Nov 2026, 3:30 pm IST",
      "Order Desk Operator",
      "2",
      "₹17,621.50 (excl. GST)",
      makeBatchDetail().csv_sha256,
    ]);
    expect(screen.getByText(/not a\s+verified native Tally import/)).toBeInTheDocument();

    const orders = screen.getByRole("region", { name: "Orders in this export" });
    const link = within(orders).getByRole("link", { name: "OB-2026-000123" });
    expect(link).toHaveAttribute("href", "/desk/orders/44444444-4444-4444-8444-444444444444");
    const row = link.closest("li")!;
    expect(row).toHaveTextContent("Sharma General Store");
    expect(row).toHaveTextContent("Confirmed 7 Nov 2026, 2:30 pm IST");
    expect(row).toHaveTextContent("Exported");
    expect(row).toHaveTextContent("₹7,776.00");
    expect(within(orders).getAllByRole("listitem")).toHaveLength(2);
    // The back link and the breadcrumb both lead to the export screen.
    const backLinks = screen.getAllByRole("link", { name: "Tally export" });
    expect(backLinks.length).toBeGreaterThan(0);
    for (const back of backLinks) {
      expect(back).toHaveAttribute("href", "/desk/exports");
    }
  });

  it("downloads the verified CSV", async () => {
    const { user } = await renderBatch();
    await screen.findByRole("heading", { name: "Export EX-2026-000001" });

    await user.click(screen.getByRole("button", { name: /^Download CSV\s+for EX-2026-000001$/ }));

    expect(await screen.findByText(/matches its SHA-256 checksum/)).toBeInTheDocument();
    expect(saveFile).toHaveBeenCalledTimes(1);
  });

  it("treats a malformed id as not found without a request", async () => {
    const { backend } = await renderBatch({}, "not-an-id");

    expect(screen.getByRole("heading", { name: "Export batch not found" })).toBeInTheDocument();
    expect(backend.requests.filter((r) => r.path.startsWith("/desk"))).toEqual([]);
  });

  it("shows not found for a 404", async () => {
    await renderBatch({ [DETAIL]: json(404, { detail: "Export batch not found." }) });

    expect(
      await screen.findByRole("heading", { name: "Export batch not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Tally export" })).toHaveAttribute(
      "href",
      "/desk/exports",
    );
  });

  it("recovers from a load error", async () => {
    const { user } = await renderBatch({
      [DETAIL]: sequence(json(500, {}), json(200, makeBatchDetail())),
    });

    expect(await screen.findByText(/Something went wrong on our side/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", { name: "Export EX-2026-000001" }),
    ).toBeInTheDocument();
  });

  it("handles a missing creator and confirmation time", async () => {
    const detail = makeBatchDetail({ created_by_name: null });
    detail.orders = [{ ...detail.orders[0]!, confirmed_at: null }];
    await renderBatch({ [DETAIL]: json(200, detail) });

    const summary = await screen.findByRole("region", { name: "Batch summary" });
    expect(summary).toHaveTextContent("Not recorded");
    expect(screen.getByRole("region", { name: "Orders in this export" })).toHaveTextContent(
      "Confirmed time not recorded",
    );
  });

  it("labels the XML export as experimental and keeps both warnings", async () => {
    await renderBatch();

    const heading = await screen.findByRole("heading", {
      level: 2,
      name: "Tally XML Export (Experimental)",
    });
    const panel = screen.getByRole("region", { name: "Tally XML Export (Experimental)" });
    expect(panel).toContainElement(heading);
    expect(within(panel).getByText(/not yet tested in TallyPrime/)).toBeInTheDocument();
    expect(within(panel).getByText(/Import each batch only once/)).toBeInTheDocument();
    // The CSV export is not part of the experimental panel.
    expect(within(panel).queryByRole("button", { name: /Download CSV/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download CSV/ })).toBeInTheDocument();
  });

  it("lists the Tally masters the XML vouchers need", async () => {
    await renderBatch();

    const panel = await screen.findByRole("region", { name: "Tally XML Export (Experimental)" });
    expect(await within(panel).findByText("Sales Account (sales)")).toBeInTheDocument();
    expect(within(panel).getByText("Discount Allowed (discounts)")).toBeInTheDocument();
    expect(within(panel).getByText("Sharma General Store")).toBeInTheDocument();
    expect(within(panel).getByText("Glucose Biscuit 75g (unit: case)")).toBeInTheDocument();
    expect(within(panel).getByText(/Amounts exclude GST/)).toBeInTheDocument();
    expect(
      within(panel).getByText(/Follows Tally's documented XML voucher format; not yet tested in TallyPrime\./),
    ).toBeInTheDocument();
    expect(within(panel).getByText(/Import each batch only once/)).toBeInTheDocument();
  });

  it("keeps the import-once warning visible when the masters list cannot load", async () => {
    await renderBatch({ [MASTERS]: json(500, { detail: "Internal Server Error" }) });

    const panel = await screen.findByRole("region", { name: "Tally XML Export (Experimental)" });
    expect(
      await within(panel).findByText(/list of Tally masters could not be loaded/),
    ).toBeInTheDocument();
    expect(within(panel).getByText(/Import each batch only once/)).toBeInTheDocument();
    expect(within(panel).getByText(/not yet tested in TallyPrime/)).toBeInTheDocument();
  });

  it("downloads the Tally XML", async () => {
    const { user, backend } = await renderBatch();

    const panel = await screen.findByRole("region", { name: "Tally XML Export (Experimental)" });
    await within(panel).findByText("Sales Account (sales)");
    await user.click(within(panel).getByRole("button", { name: /Download Tally XML/ }));

    expect(await within(panel).findByText("Downloaded.")).toBeInTheDocument();
    expect(backend.calls(XML)).toHaveLength(1);
    expect(saveFile).toHaveBeenCalledWith(
      expect.any(ArrayBuffer),
      "orderbridge-tally-EX-2026-000001.xml",
      "application/xml;charset=utf-8",
    );
  });

  it("disables the XML download and says which settings are missing", async () => {
    const { backend } = await renderBatch({
      [MASTERS]: json(
        200,
        makeMasters({ sales_ledger: null, missing_settings: ["TALLY_SALES_LEDGER_NAME"] }),
      ),
    });

    const panel = await screen.findByRole("region", { name: "Tally XML Export (Experimental)" });
    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "set TALLY_SALES_LEDGER_NAME on the server",
    );
    expect(within(panel).getByRole("button", { name: /Download Tally XML/ })).toBeDisabled();
    expect(backend.calls(XML)).toHaveLength(0);
    // The CSV download is unaffected.
    expect(screen.getByRole("button", { name: /Download CSV/ })).toBeEnabled();
  });

  it("shows the server's reason when the XML is refused", async () => {
    const { user } = await renderBatch({
      [XML]: json(409, { detail: "Set TALLY_DISCOUNT_LEDGER_NAME to the ledger names in Tally." }),
    });

    const panel = await screen.findByRole("region", { name: "Tally XML Export (Experimental)" });
    await within(panel).findByText("Sales Account (sales)");
    await user.click(within(panel).getByRole("button", { name: /Download Tally XML/ }));

    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "Set TALLY_DISCOUNT_LEDGER_NAME to the ledger names in Tally.",
    );
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("does not save a response that is not XML", async () => {
    const { user } = await renderBatch({ [XML]: xmlResponse("text/html") });

    const panel = await screen.findByRole("region", { name: "Tally XML Export (Experimental)" });
    await within(panel).findByText("Sales Account (sales)");
    await user.click(within(panel).getByRole("button", { name: /Download Tally XML/ }));

    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "did not return an XML file",
    );
    expect(saveFile).not.toHaveBeenCalled();
  });
});
