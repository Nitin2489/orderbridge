"use client";

import { AlertCircleIcon, ShoppingCartIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { formatCountdown, useSecondsUntil } from "@/features/auth/countdown";
import { orderPath } from "@/features/orders/paths";
import { describeScheme, formatCount, formatRupees, unitLabel } from "@/features/catalogue/format";
import { lineAmount, sumAmounts } from "@/features/catalogue/money";

import { useCart } from "../cart-provider";
import { MAX_NOTES_LENGTH, type CartLine } from "../cart-state";
import { CATALOGUE_PATH } from "../paths";
import type { OrderLineIssue } from "@/features/orders/types";
import { OrderConfirmation } from "./order-confirmation";
import { QuantityStepper } from "./quantity-stepper";

function EmptyCart() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <ShoppingCartIcon className="size-6" aria-hidden="true" />
      </span>
      <h2 className="text-lg font-semibold">Your cart is empty</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        Add products from the catalogue to start an order.
      </p>
      <Button asChild variant="outline" className="h-11">
        <Link href={CATALOGUE_PATH}>Browse the catalogue</Link>
      </Button>
    </div>
  );
}

/**
 * Products and the estimated subtotal at catalogue prices. An estimate
 * only: OrderBridge prices the order (schemes, free goods, discounts) when
 * it is submitted, and the order desk confirms the final amount.
 */
function OrderSummary({ lines }: { lines: readonly CartLine[] }) {
  const subtotal = sumAmounts(
    lines.map((line) => lineAmount(line.product.unit_price, line.quantity)),
  );
  return (
    <section
      aria-labelledby="cart-summary-heading"
      className="rounded-xl border bg-card p-5 shadow-xs"
    >
      <h2 id="cart-summary-heading" className="text-base font-semibold">
        Order summary
      </h2>
      <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Products</dt>
        <dd className="text-right tabular-nums">{formatCount(lines.length)}</dd>
        <dt className="font-medium">Estimated subtotal</dt>
        <dd className="text-right text-base font-semibold tabular-nums">
          {subtotal ? formatRupees(subtotal) : "Calculated when you submit"}
        </dd>
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        At your catalogue prices, before scheme discounts and free goods. Excludes GST.
        OrderBridge prices your order when you submit it, and the order desk confirms the final
        amount, so it may change.
      </p>
    </section>
  );
}

function CartLineRow({
  line,
  issue,
  locked,
  onQuantity,
  onRemove,
}: {
  line: CartLine;
  issue: OrderLineIssue | undefined;
  locked: boolean;
  onQuantity: (quantity: number) => void;
  onRemove: () => void;
}) {
  const { product, quantity } = line;
  const issueId = `cart-issue-${product.id}`;
  const activeScheme =
    product.scheme_status === "active" && product.scheme ? product.scheme : null;
  const scheme = activeScheme ? describeScheme(activeScheme, product.unit_name) : null;
  const overStock = quantity > product.stock_quantity;
  // Exact (integer paise), at the catalogue price, before schemes.
  const estimate = lineAmount(product.unit_price, quantity);
  const shortOfScheme = activeScheme ? activeScheme.minimum_quantity - quantity : 0;

  return (
    <li
      aria-labelledby={`cart-line-${product.id}`}
      aria-describedby={issue ? issueId : undefined}
      className="flex flex-col gap-3 py-4 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="min-w-0">
        <p id={`cart-line-${product.id}`} className="font-medium">
          {product.name}
        </p>
        <p className="font-mono text-xs text-muted-foreground">
          <span className="sr-only">SKU </span>
          {product.sku}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {formatRupees(product.unit_price)} per {product.unit_name} (catalogue price, excl. GST) ·{" "}
          {formatCount(product.stock_quantity)} {unitLabel(product.unit_name, product.stock_quantity)}{" "}
          in stock
        </p>
        {scheme ? (
          <p className="mt-1 text-sm text-accent-foreground">
            Scheme: {scheme}
            <span className="block text-xs text-muted-foreground">
              {shortOfScheme > 0
                ? `Order ${formatCount(shortOfScheme)} more ${unitLabel(product.unit_name, shortOfScheme)} to qualify.`
                : "This quantity qualifies. OrderBridge applies the scheme when you submit."}
            </span>
          </p>
        ) : null}
        {overStock ? (
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
            More than the {formatCount(product.stock_quantity)}{" "}
            {unitLabel(product.unit_name, product.stock_quantity)} in stock.
          </p>
        ) : null}
        {issue ? (
          <p id={issueId} className="mt-2 text-sm font-medium text-destructive">
            {issue.message}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 sm:shrink-0 sm:flex-col sm:items-end">
        {estimate ? (
          <p className="text-sm tabular-nums">
            <span className="text-muted-foreground">Estimate </span>
            <span className="font-semibold">{formatRupees(estimate)}</span>
          </p>
        ) : null}
        <div className="flex items-center gap-2">
        <QuantityStepper
          productName={product.name}
          unitName={product.unit_name}
          value={quantity}
          disabled={locked}
          onChange={onQuantity}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`Remove ${product.name} from cart`}
          disabled={locked}
          onClick={onRemove}
        >
          <Trash2Icon aria-hidden="true" />
        </Button>
        </div>
      </div>
    </li>
  );
}

/** /retailer/cart: review the cart and submit it as an order. */
export function CartScreen() {
  const cart = useCart();
  const waitSeconds = useSecondsUntil(cart.retryAvailableAt);

  if (cart.confirmation) {
    return (
      <OrderConfirmation
        order={cart.confirmation}
        orderHref={orderPath(cart.confirmation.id)}
        onDone={cart.dismissConfirmation}
      />
    );
  }

  const count = cart.itemCount;
  const waiting = waitSeconds > 0;
  const failure = cart.failure;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Your cart</h1>
        <p className="mt-1 text-sm text-muted-foreground" role="status" aria-live="polite">
          {count === 0 ? "No products yet." : `${count} ${count === 1 ? "product" : "products"}`}
        </p>
      </div>

      {count === 0 ? (
        <EmptyCart />
      ) : (
        <form
          noValidate
          className="flex flex-col gap-6"
          onSubmit={(event) => {
            event.preventDefault();
            if (!waiting) {
              cart.submit();
            }
          }}
        >
          <ul aria-label="Products in your cart" className="divide-y rounded-xl border bg-card px-5 shadow-xs">
            {cart.lines.map((line) => (
              <CartLineRow
                key={line.product.id}
                line={line}
                issue={failure?.lineIssues.get(line.product.id)}
                locked={cart.locked}
                onQuantity={(quantity) => cart.setQuantity(line.product.id, quantity)}
                onRemove={() => cart.remove(line.product.id)}
              />
            ))}
          </ul>

          <div className="flex flex-col gap-2">
            <Label htmlFor="order-notes">Notes for the order desk (optional)</Label>
            <textarea
              id="order-notes"
              rows={3}
              maxLength={MAX_NOTES_LENGTH}
              value={cart.notes}
              disabled={cart.locked}
              onChange={(event) => cart.setNotes(event.target.value)}
              aria-describedby="order-notes-count"
              className="w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
            />
            <p id="order-notes-count" className="text-xs text-muted-foreground">
              {cart.notes.length}/{MAX_NOTES_LENGTH} characters
            </p>
          </div>

          <OrderSummary lines={cart.lines} />

          {failure ? (
            <Alert variant="destructive" role="alert">
              <AlertCircleIcon aria-hidden="true" />
              <AlertDescription>{failure.message}</AlertDescription>
            </Alert>
          ) : null}

          <Button
            type="submit"
            size="lg"
            className="w-full sm:w-fit sm:self-end"
            disabled={cart.submitting || waiting}
          >
            {cart.submitting ? (
              <>
                <Spinner />
                Submitting order…
              </>
            ) : waiting ? (
              `Try again in ${formatCountdown(waitSeconds)}`
            ) : failure?.uncertain ? (
              "Retry submitting order"
            ) : (
              "Submit order"
            )}
          </Button>
        </form>
      )}
    </div>
  );
}
