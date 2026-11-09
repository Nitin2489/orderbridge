"use client";

import { AlertCircleIcon, ReceiptTextIcon } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { CATALOGUE_PATH } from "@/features/cart/paths";
import { formatCount, formatIndiaDate, formatRupees } from "@/features/catalogue/format";
import { isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useOrders } from "../api";
import { orderPath } from "../paths";
import {
  FILTERABLE_STATUSES,
  hasConfirmedTotals,
  sourceLabel,
  statusInfo,
  type OrderStatus,
} from "../status";
import type { RetailerOrderSummary } from "../types";
import { OrderStatusBadge } from "./order-status-badge";

function loadErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 0) {
    return error.message;
  }
  if (isApiError(error) && error.status >= 500) {
    // Never the server's own text.
    return "Something went wrong on our side. Please try again.";
  }
  return "Your orders could not be loaded.";
}

function productCount(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "product" : "products"}`;
}

function StatusChip({
  label,
  pressed,
  onClick,
}: {
  label: string;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        "inline-flex h-11 shrink-0 items-center rounded-full border px-4 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        pressed
          ? "border-primary bg-primary text-primary-foreground"
          : "bg-card text-foreground hover:bg-secondary",
      )}
    >
      {label}
    </button>
  );
}

function OrderRow({ order }: { order: RetailerOrderSummary }) {
  const placed = formatIndiaDate(order.created_at) ?? order.created_at;
  return (
    <li>
      <Link
        href={orderPath(order.id)}
        className="flex flex-col gap-3 rounded-xl border bg-card p-5 text-card-foreground shadow-xs transition-colors outline-none hover:bg-secondary/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="flex flex-col gap-1">
          <span className="font-mono font-semibold">{order.order_number}</span>
          <span className="text-sm text-muted-foreground">
            Placed {placed} · {productCount(order.item_count)}
            {order.source !== "retailer_app" ? ` · ${sourceLabel(order.source)}` : ""}
          </span>
          {statusInfo(order.status).description ? (
            <span className="text-xs text-muted-foreground">
              {statusInfo(order.status).description}
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-3 sm:justify-end">
          <OrderStatusBadge status={order.status} />
          <span className="text-sm">
            <span className="text-muted-foreground">
              {hasConfirmedTotals(order.status) ? "Total" : "Estimated total"}{" "}
            </span>
            <span className="font-semibold tabular-nums">{formatRupees(order.grand_total)}</span>
          </span>
        </div>
      </Link>
    </li>
  );
}

function OrdersSkeleton() {
  return (
    <ul aria-hidden="true" className="flex flex-col gap-3">
      {Array.from({ length: 4 }, (_, index) => (
        <li key={index} className="flex flex-col gap-2 rounded-xl border bg-card p-5 shadow-xs">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-56" />
        </li>
      ))}
    </ul>
  );
}

function EmptyOrders({ status, onShowAll }: { status: OrderStatus | null; onShowAll: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <ReceiptTextIcon className="size-6" aria-hidden="true" />
      </span>
      <h2 className="text-lg font-semibold">
        {status ? `No ${statusInfo(status).label.toLowerCase()} orders` : "No orders yet"}
      </h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        {status
          ? "Try another status, or show all your orders."
          : "Orders you place will appear here."}
      </p>
      {status ? (
        <Button type="button" variant="outline" className="h-11" onClick={onShowAll}>
          Show all orders
        </Button>
      ) : (
        <Button asChild variant="outline" className="h-11">
          <Link href={CATALOGUE_PATH}>Browse the catalogue</Link>
        </Button>
      )}
    </div>
  );
}

/** /retailer/orders: the signed-in retailer's orders, newest first. */
export function OrdersScreen() {
  const [status, setStatus] = useState<OrderStatus | null>(null);
  const orders = useOrders(status);

  const pages = orders.data?.pages ?? [];
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;
  const updating =
    orders.isPlaceholderData ||
    (orders.isFetching && !orders.isFetchingNextPage && !orders.isPending);

  let body: ReactNode;
  if (orders.isPending) {
    body = (
      <>
        <p role="status" className="sr-only">
          Loading orders…
        </p>
        <OrdersSkeleton />
      </>
    );
  } else if (orders.isError && items.length === 0) {
    body = (
      <Alert variant="destructive" role="alert">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>{loadErrorMessage(orders.error)}</AlertTitle>
        <AlertDescription>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => void orders.refetch()}
          >
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (items.length === 0) {
    body = <EmptyOrders status={status} onShowAll={() => setStatus(null)} />;
  } else {
    body = (
      <div className="flex flex-col gap-6">
        <ul
          aria-label="Orders"
          aria-busy={updating || undefined}
          className={cn("flex flex-col gap-3 transition-opacity", updating && "opacity-60")}
        >
          {items.map((order) => (
            <OrderRow key={order.id} order={order} />
          ))}
        </ul>

        {orders.isFetchNextPageError ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>
              More orders could not be loaded. {loadErrorMessage(orders.error)}
            </AlertDescription>
          </Alert>
        ) : null}

        {orders.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="self-center"
            disabled={orders.isFetchingNextPage}
            onClick={() => void orders.fetchNextPage()}
          >
            {orders.isFetchingNextPage ? (
              <>
                <Spinner />
                Loading…
              </>
            ) : orders.isFetchNextPageError ? (
              "Try loading more again"
            ) : (
              "Show more orders"
            )}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Your orders</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Amounts exclude GST. Totals of orders not yet confirmed are estimates and may change
          when the order desk confirms them.
        </p>
      </div>

      <div
        role="group"
        aria-label="Filter by status"
        // Wraps at every width, so on a 320 px phone every status is visible
        // and tappable (a sideways-scrolling row hid the last ones).
        className="flex flex-wrap gap-2 pb-1"
      >
        <StatusChip label="All" pressed={status === null} onClick={() => setStatus(null)} />
        {FILTERABLE_STATUSES.map((value) => (
          <StatusChip
            key={value}
            label={statusInfo(value).label}
            pressed={status === value}
            onClick={() => setStatus(status === value ? null : value)}
          />
        ))}
      </div>

      <p
        role="status"
        aria-live="polite"
        className="flex min-h-5 items-center gap-2 text-sm text-muted-foreground"
      >
        {orders.isSuccess && !updating && items.length > 0
          ? items.length < total
            ? `Showing ${formatCount(items.length)} of ${formatCount(total)} orders`
            : `${formatCount(total)} ${total === 1 ? "order" : "orders"}`
          : null}
        {updating ? (
          <>
            <Spinner />
            Updating orders…
          </>
        ) : null}
      </p>

      {body}
    </div>
  );
}
