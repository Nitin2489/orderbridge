"use client";

import {
  AlertCircleIcon,
  ArrowLeftIcon,
  CircleCheckIcon,
  CirclePauseIcon,
  SearchXIcon,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Figure, Figures } from "@/components/ui/figures";
import { LoadingState } from "@/components/ui/loading-state";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { useSession } from "@/features/auth/session-provider";
import {
  formatCount,
  formatIndiaDate,
  formatIndiaDateTime,
  formatRupees,
  parseOffsetTimestamp,
} from "@/features/catalogue/format";
import { exportBatchPath, isBatchId } from "@/features/exports/paths";
import { OrderStatusBadge } from "@/features/orders/components/order-status-badge";
import { isOrderId } from "@/features/orders/paths";
import { hasConfirmedTotals, sourceLabel } from "@/features/orders/status";
import type { OrderLine } from "@/features/orders/types";
import { isApiError } from "@/lib/api";

import { allowedActions } from "../actions";
import { useConfirmOrder, useDeskOrder, useDeskPreview, useRejectOrder } from "../api";
import { deskFailure, type DeskFailure } from "../errors";
import { DESK_PATH } from "../paths";
import type { ConfirmOrderResponse, DeskOrderDetail, OrderEvent, RejectOrderResponse } from "../types";
import { ConfirmAction } from "./confirm-action";
import { LineIssues, PreviewPanel } from "./preview-panel";
import { RejectAction } from "./reject-action";

function Amount({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right tabular-nums">{children}</dd>
    </div>
  );
}

function BackToQueue() {
  return (
    <Link
      href={DESK_PATH}
      className="inline-flex w-fit items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <ArrowLeftIcon className="size-4" aria-hidden="true" />
      Order queue
    </Link>
  );
}

function NotFound() {
  return (
    <section
      aria-labelledby="desk-order-not-found"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <SearchXIcon className="size-6" aria-hidden="true" />
      </span>
      <h1 id="desk-order-not-found" className="text-2xl font-semibold tracking-tight">
        Order not found
      </h1>
      <Button asChild size="lg">
        <Link href={DESK_PATH}>Back to the order queue</Link>
      </Button>
    </section>
  );
}

const EVENT_LABEL: Record<string, string> = {
  submitted: "Submitted",
  confirmed: "Confirmed",
  placed_on_hold: "Placed on hold (credit limit)",
  rejected: "Rejected",
  exported: "Exported for billing",
  hold_released: "Hold released",
};

const HOLD_FIGURES: [string, string][] = [
  ["required_credit", "Required credit"],
  ["available_credit", "Available credit"],
  ["shortfall", "Shortfall"],
];

/**
 * When an event happened, in India time. A timestamp that cannot be parsed
 * is not shown raw (or guessed at): the event says its time is unknown.
 */
function EventTime({ timestamp }: { timestamp: string }) {
  const instant = parseOffsetTimestamp(timestamp);
  const label = formatIndiaDateTime(timestamp);
  if (instant === null || label === null) {
    return <span>Time not available</span>;
  }
  return <time dateTime={new Date(instant).toISOString()}>{label}</time>;
}

/**
 * The export batch an "exported" event names, as a link to it. Shown only
 * when the event's details carry a well-formed batch id and number.
 */
function ExportedBatchLink({ event }: { event: OrderEvent }) {
  const batchId = event.details?.batch_id;
  const batchNumber = event.details?.batch_number;
  if (typeof batchId !== "string" || !isBatchId(batchId) || typeof batchNumber !== "string") {
    return null;
  }
  return (
    <p className="mt-1">
      Batch{" "}
      <Link
        href={exportBatchPath(batchId)}
        className="rounded-sm font-mono font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        {batchNumber}
      </Link>
    </p>
  );
}

function EventTimeline({ events }: { events: readonly OrderEvent[] }) {
  if (events.length === 0) {
    return null;
  }
  return (
    <section aria-labelledby="events-heading" className="flex flex-col gap-3">
      <h2 id="events-heading" className="text-base font-semibold">
        History
      </h2>
      <ol className="flex flex-col gap-3 border-l pl-4">
        {events.map((event) => (
          <li key={event.id} className="text-sm">
            <p className="font-medium">
              {EVENT_LABEL[event.event_type] ?? event.event_type.replace(/_/g, " ")}
            </p>
            <p className="text-muted-foreground">
              <EventTime timestamp={event.created_at} />
              {event.actor_name ? ` · ${event.actor_name}` : ""}
            </p>
            {event.reason ? <p className="mt-1">Reason: {event.reason}</p> : null}
            {event.event_type === "exported" ? <ExportedBatchLink event={event} /> : null}
            {event.event_type === "placed_on_hold" && event.details ? (
              <dl className="mt-1 grid grid-cols-[auto_auto] justify-start gap-x-4 gap-y-0.5">
                {HOLD_FIGURES.map(([key, label]) => {
                  const value = event.details?.[key];
                  return typeof value === "string" ? (
                    <Amount key={key} label={label}>
                      {formatRupees(value)}
                    </Amount>
                  ) : null;
                })}
              </dl>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function SubmittedLines({ items }: { items: readonly OrderLine[] }) {
  return (
    <section aria-labelledby="lines-heading" className="rounded-xl border bg-card px-5 shadow-xs">
      <h2 id="lines-heading" className="pt-5 text-base font-semibold">
        Lines
      </h2>
      <ul className="divide-y">
        {items.map((line) => (
          <li
            key={line.line_number}
            aria-labelledby={`desk-line-${line.line_number}`}
            className="flex flex-col gap-2 py-4"
          >
            <div>
              <p id={`desk-line-${line.line_number}`} className="font-medium">
                {line.line_number}. {line.product_name}
              </p>
              <p className="font-mono text-xs text-muted-foreground">
                <span className="sr-only">SKU </span>
                {line.sku}
              </p>
              {line.scheme_name ? <p className="mt-1 text-sm">Scheme: {line.scheme_name}</p> : null}
            </div>
            <Figures columns={5}>
              <Figure label="Quantity">{formatCount(line.quantity)}</Figure>
              <Figure label="Free quantity">{formatCount(line.free_quantity)}</Figure>
              <Figure label="Unit price">{formatRupees(line.unit_price)}</Figure>
              <Figure label="Discount">{formatRupees(line.discount_amount)}</Figure>
              <Figure label="Line total">{formatRupees(line.line_total)}</Figure>
            </Figures>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CreditPanel({ order }: { order: DeskOrderDetail }) {
  const { retailer } = order;
  return (
    <section
      aria-labelledby="credit-heading"
      className="rounded-xl border bg-card p-5 shadow-xs"
    >
      <h2 id="credit-heading" className="text-base font-semibold">
        {retailer.business_name}: credit now
      </h2>
      {!retailer.is_active ? (
        <p className="mt-2 text-sm font-medium text-destructive">
          This retailer account is inactive. Its orders cannot be confirmed, only rejected.
        </p>
      ) : null}
      <Figures columns={3} className="mt-3">
        <Figure label="Credit limit">{formatRupees(retailer.credit_limit)}</Figure>
        <Figure label="Outstanding balance">{formatRupees(retailer.outstanding_balance)}</Figure>
        <Figure label="Available credit">{formatRupees(retailer.available_credit)}</Figure>
      </Figures>
    </section>
  );
}

function ConfirmOutcome({ result }: { result: ConfirmOrderResponse }) {
  if (result.outcome === "on_hold_credit_limit") {
    return (
      <Alert role="status">
        <CirclePauseIcon aria-hidden="true" />
        <AlertTitle>Placed on hold: not confirmed</AlertTitle>
        <AlertDescription>
          <p>
            The total of {result.required_credit ? formatRupees(result.required_credit) : "the order"}{" "}
            exceeds the retailer&apos;s available credit
            {result.available_credit ? ` of ${formatRupees(result.available_credit)}` : ""}. No
            stock was deducted and the balance did not change. A held order can only be rejected
            for now.
          </p>
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert variant="info" role="status">
      <CircleCheckIcon aria-hidden="true" />
      <AlertTitle>
        {result.outcome === "confirmed" ? "Order confirmed" : "Already confirmed"}
      </AlertTitle>
      <AlertDescription>
        {result.outcome === "confirmed"
          ? `Confirmed at ${formatRupees(result.order.grand_total)} (excl. GST). Stock was deducted and the retailer's balance updated.`
          : "This order was already confirmed. Nothing changed."}
      </AlertDescription>
    </Alert>
  );
}

function RejectOutcome({ result }: { result: RejectOrderResponse }) {
  return (
    <Alert variant="info" role="status">
      <CircleCheckIcon aria-hidden="true" />
      <AlertTitle>{result.already_rejected ? "Already rejected" : "Order rejected"}</AlertTitle>
      <AlertDescription>
        {result.already_rejected
          ? "This order was already rejected. Nothing changed."
          : "The order was rejected. Stock and the retailer's balance are unchanged."}
      </AlertDescription>
    </Alert>
  );
}

function failureMessage(action: "confirm" | "reject", failure: DeskFailure): ReactNode {
  const verb = action === "confirm" ? "confirmation" : "rejection";
  switch (failure.kind) {
    case "outdated":
      return "Prices or schemes changed since you reviewed this order. Nothing was changed. Review the updated pricing and confirm again.";
    case "issues":
      return (
        <>
          <p>Some lines cannot be confirmed. Nothing was changed.</p>
          <LineIssues issues={failure.issues} />
        </>
      );
    case "state":
      return `This order changed since it was loaded, so the ${verb} was not applied. Its current state is shown below.`;
    case "not_found":
      return "This order no longer exists.";
    case "forbidden":
      return "The request was refused. Reload the page and try again; if it persists, sign in again.";
    case "invalid":
      return action === "reject"
        ? "The reason must be 3 to 500 characters."
        : "The confirmation could not be processed. Refresh the prices and try again.";
    case "uncertain":
      return `We could not tell whether the ${verb} went through. The order's current state is shown below; if it still needs action, you can try again.`;
  }
}

function OrderReview({ order, sessionUserId }: { order: DeskOrderDetail; sessionUserId: string }) {
  const allowed = allowedActions(order);
  const preview = useDeskPreview(order.id, allowed.confirm);
  const confirm = useConfirmOrder(order.id);
  const reject = useRejectOrder(order.id);
  const [lastAction, setLastAction] = useState<"confirm" | "reject" | null>(null);
  const inFlight = useRef(false);
  const busy = confirm.isPending || reject.isPending;

  const confirmFailure = confirm.isError ? deskFailure(confirm.error) : null;
  const rejectFailure = reject.isError ? deskFailure(reject.error) : null;
  const failure = lastAction === "confirm" ? confirmFailure : rejectFailure;

  const run = (action: "confirm" | "reject", start: () => void) => {
    if (inFlight.current || busy) {
      return;
    }
    inFlight.current = true;
    setLastAction(action);
    start();
  };
  const settle = {
    onSettled: () => {
      inFlight.current = false;
    },
  };

  const placed = formatIndiaDate(order.created_at) ?? order.created_at;
  const lastRejection = [...order.events].reverse().find((e) => e.event_type === "rejected");

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Breadcrumbs
          items={[
            { label: "Order queue", href: DESK_PATH },
            { label: order.order_number, current: true },
          ]}
        />
      </div>

      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Order <span className="font-mono">{order.order_number}</span>
        </h1>
        <p className="text-sm text-muted-foreground">
          {order.retailer.business_name} · Placed {placed} · {sourceLabel(order.source)}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <OrderStatusBadge status={order.status} />
          {order.status === "on_hold" ? (
            <p className="text-sm">
              Held because the total exceeded the retailer&apos;s available credit. It can only be
              rejected for now.
            </p>
          ) : null}
          {order.status === "rejected" && lastRejection?.reason ? (
            <p className="text-sm">Reason: {lastRejection.reason}</p>
          ) : null}
        </div>
      </div>

      <div aria-live="polite" className="flex flex-col gap-3">
        {lastAction === "confirm" && confirm.isSuccess ? (
          <ConfirmOutcome result={confirm.data} />
        ) : null}
        {lastAction === "reject" && reject.isSuccess ? <RejectOutcome result={reject.data} /> : null}
        {failure && lastAction ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>{failureMessage(lastAction, failure)}</AlertDescription>
          </Alert>
        ) : null}
      </div>

      <CreditPanel order={order} />

      {allowed.confirm ? (
        <>
          <PreviewPanel
            preview={preview.data}
            isPending={preview.isPending}
            isFetching={preview.isFetching}
            error={preview.error}
            onRefresh={() => void preview.refetch()}
          />
          <ConfirmAction
            orderNumber={order.order_number}
            preview={preview.data}
            disabled={busy || preview.isFetching}
            pending={confirm.isPending}
            retry={lastAction === "confirm" && confirmFailure?.kind === "uncertain"}
            onConfirm={(fingerprint) =>
              run("confirm", () =>
                confirm.mutate({ fingerprint, sessionUserId }, settle),
              )
            }
          />
        </>
      ) : (
        <SubmittedLines items={order.items} />
      )}

      {order.notes ? (
        <section aria-labelledby="desk-notes-heading">
          <h2 id="desk-notes-heading" className="text-base font-semibold">
            Retailer notes
          </h2>
          <p className="mt-1 text-sm whitespace-pre-line">{order.notes}</p>
        </section>
      ) : null}

      <section aria-labelledby="stored-totals-heading" className="rounded-xl border bg-card p-5 shadow-xs">
        <h2 id="stored-totals-heading" className="text-base font-semibold">
          {hasConfirmedTotals(order.status) ? "Confirmed totals" : "Totals at submission"}
        </h2>
        <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
          <Amount label="Subtotal">{formatRupees(order.subtotal)}</Amount>
          <Amount label="Discount total">{formatRupees(order.discount_total)}</Amount>
          <div className="contents font-semibold">
            <dt>Grand total</dt>
            <dd className="text-right tabular-nums">{formatRupees(order.grand_total)}</dd>
          </div>
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">Amounts exclude GST.</p>
      </section>

      {allowed.reject ? (
        <RejectAction
          orderNumber={order.order_number}
          disabled={busy}
          pending={reject.isPending}
          retry={lastAction === "reject" && rejectFailure?.kind === "uncertain"}
          onReject={(reason) =>
            run("reject", () => reject.mutate({ reason, sessionUserId }, settle))
          }
        />
      ) : null}

      <EventTimeline events={order.events} />
    </div>
  );
}

/** /desk/orders/[orderId]: review one order, then confirm or reject it. */
export function OrderReviewScreen({ orderId }: { orderId: string }) {
  const { user } = useSession();
  const order = useDeskOrder(orderId);

  const notFound =
    !isOrderId(orderId) ||
    (order.isError && isApiError(order.error) && [404, 422].includes(order.error.status));
  if (notFound) {
    return <NotFound />;
  }
  if (order.isPending || !user) {
    return <LoadingState label="Loading order…" />;
  }
  if (order.isError) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <BackToQueue />
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertTitle>
            {isApiError(order.error) && order.error.status === 0
              ? order.error.message
              : "This order could not be loaded."}
          </AlertTitle>
          <AlertDescription>
            <Button
              type="button"
              variant="outline"
              className="mt-3 h-11 text-foreground"
              onClick={() => void order.refetch()}
            >
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  // Keyed by the signed-in user: if the session changes while a confirm or
  // reject is in flight, the review remounts with fresh mutation observers,
  // so the old request's result, banners, retry offers and half-finished
  // confirm or reject steps never appear in the new session.
  return <OrderReview key={user.id} order={order.data} sessionUserId={user.id} />;
}
