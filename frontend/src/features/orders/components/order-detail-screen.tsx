"use client";

import { AlertCircleIcon, ArrowLeftIcon, SearchXIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Figure, Figures } from "@/components/ui/figures";
import { LoadingState } from "@/components/ui/loading-state";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { OrderConfirmation } from "@/features/cart/components/order-confirmation";
import { formatCount, formatIndiaDate, formatRupees } from "@/features/catalogue/format";
import { isApiError } from "@/lib/api";

import { useOrder } from "../api";
import { ORDERS_PATH, isOrderId, orderPath } from "../paths";
import { hasConfirmedTotals, sourceLabel, statusInfo } from "../status";
import type { OrderLine, RetailerOrderDetail } from "../types";
import { OrderProgress } from "./order-progress";
import { OrderStatusBadge } from "./order-status-badge";
import { RepeatOrderAction } from "./repeat-order-action";

function BackToOrders() {
  return (
    <Link
      href={ORDERS_PATH}
      className="inline-flex w-fit items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <ArrowLeftIcon className="size-4" aria-hidden="true" />
      All orders
    </Link>
  );
}

function OrderNotFound() {
  return (
    <section
      aria-labelledby="order-not-found-heading"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <SearchXIcon className="size-6" aria-hidden="true" />
      </span>
      <h1 id="order-not-found-heading" className="text-2xl font-semibold tracking-tight">
        Order not found
      </h1>
      <p className="text-muted-foreground">
        This order does not exist or is not one of your orders.
      </p>
      <Button asChild size="lg">
        <Link href={ORDERS_PATH}>Back to your orders</Link>
      </Button>
    </section>
  );
}

function Amount({ label, value }: { label: string; value: string }) {
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right tabular-nums">{value}</dd>
    </div>
  );
}

function LineRow({ line }: { line: OrderLine }) {
  return (
    <li aria-labelledby={`order-line-${line.line_number}`} className="flex flex-col gap-2 py-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <p id={`order-line-${line.line_number}`} className="font-medium">
            {line.product_name}
          </p>
          {line.free_quantity > 0 ? (
            <Badge variant="success">+{formatCount(line.free_quantity)} free</Badge>
          ) : null}
        </div>
        <p className="font-mono text-xs text-muted-foreground">
          <span className="sr-only">SKU </span>
          {line.sku}
        </p>
        {line.scheme_name ? (
          <p className="mt-1 text-sm text-accent-foreground">Scheme: {line.scheme_name}</p>
        ) : null}
      </div>
      {/* The API gives no unit for order lines, so quantities stay neutral. */}
      <Figures columns={5}>
        <Figure label="Quantity">{formatCount(line.quantity)}</Figure>
        <Figure label="Free quantity">{formatCount(line.free_quantity)}</Figure>
        <Figure label="Unit price">{formatRupees(line.unit_price)}</Figure>
        <Figure label="Discount">{formatRupees(line.discount_amount)}</Figure>
        <Figure label="Line total">{formatRupees(line.line_total)}</Figure>
      </Figures>
    </li>
  );
}

function OrderDetail({
  order,
  onRepeated,
}: {
  order: RetailerOrderDetail;
  onRepeated: (created: RetailerOrderDetail) => void;
}) {
  const info = statusInfo(order.status);
  const confirmedTotals = hasConfirmedTotals(order.status);
  const placed = formatIndiaDate(order.created_at) ?? order.created_at;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Breadcrumbs
          items={[
            { label: "Orders", href: ORDERS_PATH },
            { label: order.order_number, current: true },
          ]}
        />
      </div>

      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Order <span className="font-mono">{order.order_number}</span>
        </h1>
        <p className="text-sm text-muted-foreground">
          Placed {placed} · {sourceLabel(order.source)} ·{" "}
          {formatCount(order.item_count)} {order.item_count === 1 ? "product" : "products"}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <OrderStatusBadge status={order.status} />
          {info.description ? <p className="text-sm">{info.description}</p> : null}
        </div>
      </div>

      <OrderProgress order={order} />

      <section aria-labelledby="order-items-heading" className="rounded-xl border bg-card px-5 shadow-xs">
        <h2 id="order-items-heading" className="pt-5 text-base font-semibold">
          Items
        </h2>
        <ul className="divide-y">
          {order.items.map((line) => (
            <LineRow key={line.line_number} line={line} />
          ))}
        </ul>
      </section>

      {order.notes ? (
        <section aria-labelledby="order-notes-heading">
          <h2 id="order-notes-heading" className="text-base font-semibold">
            Notes
          </h2>
          <p className="mt-1 text-sm whitespace-pre-line">{order.notes}</p>
        </section>
      ) : null}

      <section
        aria-labelledby="order-totals-heading"
        className="rounded-xl border bg-card p-5 shadow-xs"
      >
        <h2 id="order-totals-heading" className="text-base font-semibold">
          {confirmedTotals ? "Totals" : "Estimated totals"}
        </h2>
        <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
          <Amount label="Subtotal" value={formatRupees(order.subtotal)} />
          <Amount label="Discount total" value={formatRupees(order.discount_total)} />
          <div className="contents font-semibold">
            <dt>{confirmedTotals ? "Grand total" : "Estimated grand total"}</dt>
            <dd className="text-right tabular-nums">{formatRupees(order.grand_total)}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">
          Amounts exclude GST, which is added at invoicing.
          {confirmedTotals
            ? " These are the prices the order desk confirmed."
            : " These are the prices when the order was placed; the order desk re-checks " +
              "prices, schemes and stock when it confirms the order, so they may change."}
        </p>
      </section>

      <RepeatOrderAction order={order} onRepeated={onRepeated} />
    </div>
  );
}

/** /retailer/orders/[orderId]: one of the signed-in retailer's orders. */
export function OrderDetailScreen({ orderId }: { orderId: string }) {
  const order = useOrder(orderId);
  const [repeated, setRepeated] = useState<RetailerOrderDetail | null>(null);

  if (repeated) {
    return (
      <OrderConfirmation
        order={repeated}
        orderHref={orderPath(repeated.id)}
        onDone={() => setRepeated(null)}
      />
    );
  }

  const notFound =
    !isOrderId(orderId) ||
    (order.isError && isApiError(order.error) && [404, 422].includes(order.error.status));
  if (notFound) {
    return <OrderNotFound />;
  }

  if (order.isPending) {
    return <LoadingState label="Loading order…" />;
  }

  if (order.isError) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <BackToOrders />
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertTitle>
            {isApiError(order.error) && order.error.status === 0
              ? order.error.message
              : isApiError(order.error) && order.error.status >= 500
                ? "Something went wrong on our side. Please try again."
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

  return <OrderDetail order={order.data} onRepeated={setRepeated} />;
}
