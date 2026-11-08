"use client";

import { AlertCircleIcon, ArrowLeftIcon, SearchXIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { useSession } from "@/features/auth/session-provider";
import { formatCount, formatIndiaDateTime, formatRupees } from "@/features/catalogue/format";
import { deskOrderPath } from "@/features/desk/paths";
import { OrderStatusBadge } from "@/features/orders/components/order-status-badge";
import { isApiError } from "@/lib/api";

import { useExportBatch } from "../api";
import { EXPORTS_PATH, isBatchId } from "../paths";
import type { ExportBatchDetail } from "../types";
import { CsvNotice } from "./csv-notice";
import { DownloadButton } from "./download-button";
import { loadErrorMessage } from "./export-history";

function BackToExports() {
  return (
    <Link
      href={EXPORTS_PATH}
      className="inline-flex w-fit items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <ArrowLeftIcon className="size-4" aria-hidden="true" />
      Tally export
    </Link>
  );
}

function NotFound() {
  return (
    <section
      aria-labelledby="batch-not-found"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <SearchXIcon className="size-6" aria-hidden="true" />
      </span>
      <h1 id="batch-not-found" className="text-2xl font-semibold tracking-tight">
        Export batch not found
      </h1>
      <Button asChild size="lg">
        <Link href={EXPORTS_PATH}>Back to Tally export</Link>
      </Button>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function BatchDetail({ batch }: { batch: ExportBatchDetail }) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Breadcrumbs
          items={[
            { label: "Tally export", href: EXPORTS_PATH },
            { label: batch.batch_number, current: true },
          ]}
        />
        <BackToExports />
      </div>
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Export <span className="font-mono">{batch.batch_number}</span>
        </h1>
        <CsvNotice />
      </div>

      <section
        aria-labelledby="batch-summary-heading"
        className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs"
      >
        <h2 id="batch-summary-heading" className="text-base font-semibold">
          Batch summary
        </h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <Field label="Created">
            {formatIndiaDateTime(batch.created_at) ?? "Time not available"}
          </Field>
          <Field label="Created by">{batch.created_by_name ?? "Not recorded"}</Field>
          <Field label="Orders">{formatCount(batch.order_count)}</Field>
          <Field label="Net total">
            <span className="font-semibold tabular-nums">{formatRupees(batch.net_total)}</span>{" "}
            (excl. GST)
          </Field>
          <Field label="SHA-256">
            <code className="font-mono text-xs break-all">{batch.csv_sha256}</code>
          </Field>
        </dl>
        <DownloadButton batch={batch} />
      </section>

      <section aria-labelledby="batch-orders-heading" className="flex flex-col gap-3">
        <h2 id="batch-orders-heading" className="text-base font-semibold">
          Orders in this export
        </h2>
        {batch.orders.length === 0 ? (
          <p className="text-sm text-muted-foreground">No orders are linked to this batch.</p>
        ) : (
          <ul className="flex flex-col divide-y rounded-xl border bg-card px-5 shadow-xs">
            {batch.orders.map((order) => (
              <li
                key={order.id}
                className="flex flex-col gap-2 py-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex flex-col gap-1">
                  <Link
                    href={deskOrderPath(order.id)}
                    className="w-fit rounded-sm font-mono font-semibold text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  >
                    {order.order_number}
                  </Link>
                  <span className="text-sm">{order.retailer_business_name}</span>
                  <span className="text-sm text-muted-foreground">
                    Confirmed{" "}
                    {order.confirmed_at
                      ? (formatIndiaDateTime(order.confirmed_at) ?? "time not available")
                      : "time not recorded"}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-3 sm:justify-end">
                  <OrderStatusBadge status={order.status} />
                  <span className="text-sm font-semibold tabular-nums">
                    {formatRupees(order.net_total)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function BatchView({ batchId }: { batchId: string }) {
  const batch = useExportBatch(batchId);

  const notFound =
    !isBatchId(batchId) ||
    (batch.isError && isApiError(batch.error) && [404, 422].includes(batch.error.status));
  if (notFound) {
    return <NotFound />;
  }
  if (batch.isPending) {
    return <LoadingState label="Loading export…" />;
  }
  if (batch.isError) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <BackToExports />
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertTitle>{loadErrorMessage(batch.error, "This export")}</AlertTitle>
          <AlertDescription>
            <Button
              type="button"
              variant="outline"
              className="mt-3 h-11 text-foreground"
              onClick={() => void batch.refetch()}
            >
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  return <BatchDetail batch={batch.data} />;
}

/** /desk/exports/[batchId]: one export batch, its orders and its CSV. */
export function BatchScreen({ batchId }: { batchId: string }) {
  const { user } = useSession();
  if (!user) {
    return <LoadingState label="Loading…" />;
  }
  // Keyed by the signed-in user, so download results never carry over.
  return <BatchView key={user.id} batchId={batchId} />;
}
