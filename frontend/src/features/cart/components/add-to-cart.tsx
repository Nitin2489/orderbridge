"use client";

import { ShoppingCartIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import type { CatalogueProduct } from "@/features/catalogue/types";

import { useCart } from "../cart-provider";
import { MAX_ORDER_LINES, unorderableReason } from "../cart-state";
import { CART_PATH } from "../paths";
import { QuantityStepper } from "./quantity-stepper";

/** Add a catalogue product to the cart, or adjust it once it is there. */
export function AddToCart({ product }: { product: CatalogueProduct }) {
  const cart = useCart();
  const reason = unorderableReason(product);
  const line = cart.lines.find((entry) => entry.product.id === product.id);

  if (reason) {
    return (
      <Button type="button" variant="outline" className="h-11 w-full" disabled>
        {reason}
      </Button>
    );
  }

  if (!line) {
    const full = cart.itemCount >= MAX_ORDER_LINES;
    return (
      <div className="flex flex-col gap-1">
        <Button
          type="button"
          className="h-11 w-full"
          disabled={cart.locked || full}
          onClick={() => cart.add(product)}
        >
          <ShoppingCartIcon aria-hidden="true" />
          Add to cart
          <span className="sr-only">: {product.name}</span>
        </Button>
        {full ? (
          <p className="text-xs text-muted-foreground">
            An order can have at most {MAX_ORDER_LINES} products.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <QuantityStepper
        productName={product.name}
        unitName={product.unit_name}
        value={line.quantity}
        disabled={cart.locked}
        onChange={(quantity) => cart.setQuantity(product.id, quantity)}
      />
      <Link
        href={CART_PATH}
        className="w-fit rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        In cart · View cart
      </Link>
    </div>
  );
}
