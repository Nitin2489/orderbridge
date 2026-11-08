"use client";

import { AlertCircleIcon, TriangleAlertIcon, RefreshCwIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Figure, Figures } from "@/components/ui/figures";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { formatCount, formatRupees } from "@/features/catalogue/format";
import { isApiError } from "@/lib/api";

import type { DeskOrderPreview, LineIssue, PreviewLine } from "../types";

/** "stored → current" when they differ; the single value otherwise. */
function Change({ stored, current }: { stored: string; current: string }) {
  if (stored === current) {
    return <>{current}</>;
  }
  return (
    <>
      <span className="text-muted-foreground line-through">{stored}</span>
      <span aria-hidden="true"> → </span>
      <span className="sr-only"> changed to </span>
      <span className="font-semibold">{current}</span>
    </>
  );
}

const Row = Figure;

/** A totals row: label left, amount right (kept as a summary list). */
function TotalRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right tabular-nums">{children}</dd>
    </div>
  );
}

function PreviewLineRow({ line }: { line: PreviewLine }) {
  return (
    <li
      aria-labelledby={`preview-line-${line.line_number}`}
      className="flex flex-col gap-2 py-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <p id={`preview-line-${line.line_number}`} className="font-medium">
          {line.line_number}. {line.product_name}
        </p>
        {line.price_changed ? <Badge variant="secondary">Changed since submission</Badge> : null}
      </div>
      <p className="font-mono text-xs text-muted-foreground">
        <span className="sr-only">SKU </span>
        {line.sku}
      </p>
      <p className="text-sm">
        <span className="text-muted-foreground">Scheme: </span>
        <Change
          stored={line.stored_scheme_name ?? "None"}
          current={line.current_scheme_name ?? "None"}
        />
      </p>
      <Figures columns={6}>
        <Row label="Quantity">{formatCount(line.quantity)}</Row>
        <Row label="Free quantity">
          <Change
            stored={formatCount(line.stored_free_quantity)}
            current={formatCount(line.current_free_quantity)}
          />
        </Row>
        <Row label="Stock required">{formatCount(line.stock_required)}</Row>
        <Row label="Unit price">
          <Change
            stored={formatRupees(line.stored_unit_price)}
            current={formatRupees(line.current_unit_price)}
          />
        </Row>
        <Row label="Discount">
          <Change
            stored={formatRupees(line.stored_discount_amount)}
            current={formatRupees(line.current_discount_amount)}
          />
        </Row>
        <Row label="Line total">
          <Change
            stored={formatRupees(line.stored_line_total)}
            current={formatRupees(line.current_line_total)}
          />
        </Row>
      </Figures>
    </li>
  );
}

export function LineIssues({ issues }: { issues: readonly LineIssue[] }) {
  return (
    <ul className="mt-2 list-disc space-y-1 pl-5">
      {issues.map((issue) => (
        <li key={issue.order_item_id}>
          <span className="font-medium">
            Line {issue.line_number} ({issue.sku}):
          </span>{" "}
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

function previewErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 409) {
    return "This order can no longer be priced for confirmation. Its current state is shown above.";
  }
  if (isApiError(error) && error.status === 0) {
    return error.message;
  }
  return "The current prices could not be loaded.";
}

interface PreviewPanelProps {
  preview: DeskOrderPreview | undefined;
  isPending: boolean;
  isFetching: boolean;
  error: unknown;
  onRefresh: () => void;
}

/**
 * The order priced now (read-only), beside the figures stored at
 * submission. All figures and verdicts are the backend's.
 */
export function PreviewPanel({ preview, isPending, isFetching, error, onRefresh }: PreviewPanelProps) {
  return (
    <section
      aria-labelledby="preview-heading"
      className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="preview-heading" className="text-base font-semibold">
          Current pricing
        </h2>
        <Button
          type="button"
          variant="ghost"
          className="h-11"
          onClick={onRefresh}
          disabled={isFetching}
        >
          <RefreshCwIcon aria-hidden="true" />
          Refresh prices
        </Button>
      </div>

      {!preview ? (
        error ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>{previewErrorMessage(error)}</AlertDescription>
          </Alert>
        ) : isPending ? (
          <LoadingState label="Pricing the order at current prices…" className="min-h-24" />
        ) : null
      ) : (
        <>
          {preview.has_price_changes ? (
            <Alert variant="info" role="status">
              <TriangleAlertIcon aria-hidden="true" />
              <AlertDescription>
                Prices or schemes changed since the order was submitted. Confirming uses the
                current figures.
              </AlertDescription>
            </Alert>
          ) : null}

          {preview.issues.length > 0 ? (
            <Alert variant="destructive" role="alert">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>These lines cannot be confirmed</AlertTitle>
              <AlertDescription>
                <LineIssues issues={preview.issues} />
              </AlertDescription>
            </Alert>
          ) : null}

          <ul aria-label="Lines priced now" className="divide-y">
            {preview.lines.map((line) => (
              <PreviewLineRow key={line.order_item_id} line={line} />
            ))}
          </ul>

          <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 border-t pt-3 text-sm">
            <TotalRow label="Subtotal">{formatRupees(preview.subtotal)}</TotalRow>
            <TotalRow label="Discount total">{formatRupees(preview.discount_total)}</TotalRow>
            <div className="contents font-semibold">
              <dt>{preview.totals_cover_all_lines ? "Grand total" : "Partial total"}</dt>
              <dd className="text-right tabular-nums">{formatRupees(preview.grand_total)}</dd>
            </div>
            <TotalRow label="Available credit">
              {formatRupees(preview.available_credit)}
            </TotalRow>
          </dl>
          {!preview.totals_cover_all_lines ? (
            <p className="text-xs text-muted-foreground">
              Lines that cannot be confirmed are left out, so this is not the order total.
            </p>
          ) : null}

          {preview.exceeds_credit_limit ? (
            <Alert variant="destructive" role="status">
              <TriangleAlertIcon aria-hidden="true" />
              <AlertDescription>
                {preview.totals_cover_all_lines
                  ? "This total exceeds the retailer's available credit. Confirming will place the order on hold instead: no stock is deducted and the balance does not change."
                  : "Even the lines that can be priced exceed the retailer's available credit."}
              </AlertDescription>
            </Alert>
          ) : null}

          <p className="text-xs text-muted-foreground">Amounts exclude GST.</p>
        </>
      )}
    </section>
  );
}
