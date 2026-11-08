"use client";

import { CircleCheckIcon } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatRupees } from "@/features/catalogue/format";
import { statusInfo } from "@/features/orders/status";
import type { RetailerOrderDetail } from "@/features/orders/types";

import { CATALOGUE_PATH } from "../paths";

/** What the server accepted: shown from its response, never from the cart. */
export function OrderConfirmation({
  order,
  onDone,
  orderHref,
}: {
  order: RetailerOrderDetail;
  onDone: () => void;
  /** Link to the new order's detail page, when available. */
  orderHref?: string;
}) {
  return (
    <section
      aria-labelledby="order-confirmation-heading"
      className="mx-auto flex w-full max-w-2xl flex-col gap-6"
    >
      <div className="flex flex-col items-start gap-3">
        <span className="inline-flex size-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
          <CircleCheckIcon className="size-6" aria-hidden="true" />
        </span>
        <h1 id="order-confirmation-heading" className="text-2xl font-semibold tracking-tight">
          Order submitted
        </h1>
        <p className="text-muted-foreground" role="status">
          Order <span className="font-mono font-semibold text-foreground">{order.order_number}</span>{" "}
          is waiting for the order desk to review it. It is not confirmed yet.
        </p>
        <Badge variant="secondary">{statusInfo(order.status).label}</Badge>
      </div>

      <div className="rounded-xl border bg-card p-5 shadow-xs">
        <h2 className="text-base font-semibold">Items</h2>
        <ul className="mt-3 divide-y">
          {order.items.map((line) => (
            <li key={line.line_number} className="flex flex-col gap-0.5 py-3 text-sm">
              <span className="font-medium">{line.product_name}</span>
              <span className="font-mono text-xs text-muted-foreground">{line.sku}</span>
              <span className="text-muted-foreground">
                Quantity {line.quantity}
                {line.free_quantity > 0 ? ` · ${line.free_quantity} free` : ""}
                {line.scheme_name ? ` · ${line.scheme_name}` : ""}
              </span>
            </li>
          ))}
        </ul>
        <dl className="mt-3 flex items-baseline justify-between gap-4 border-t pt-3 text-sm">
          <dt className="text-muted-foreground">Estimated total at submission (excl. GST)</dt>
          <dd className="font-semibold tabular-nums">{formatRupees(order.grand_total)}</dd>
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">
          Calculated by OrderBridge when you submitted. The order desk re-checks prices, schemes
          and stock when it confirms the order, so the final amount may differ.
        </p>
        {order.notes ? (
          <p className="mt-3 text-sm">
            <span className="text-muted-foreground">Notes: </span>
            {order.notes}
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        {orderHref ? (
          <Button asChild size="lg" className="w-fit">
            <Link href={orderHref}>View order details</Link>
          </Button>
        ) : null}
        <Button asChild size="lg" variant={orderHref ? "outline" : "default"} className="w-fit">
          <Link href={CATALOGUE_PATH} onClick={onDone}>
            Back to catalogue
          </Link>
        </Button>
      </div>
    </section>
  );
}
