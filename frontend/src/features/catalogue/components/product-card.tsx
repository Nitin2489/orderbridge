import { Skeleton } from "@/components/ui/skeleton";
import { AddToCart } from "@/features/cart/components/add-to-cart";
import { cn } from "@/lib/utils";

import { formatRupees, packSize } from "../format";
import { perUnitPrice } from "../money";
import type { CatalogueProduct } from "../types";
import { ProductImage } from "./product-image";
import { SchemeInfo } from "./scheme-info";
import { StockStatus } from "./stock-status";

const TAX_LABEL: Record<CatalogueProduct["tax_treatment"], string> = {
  exclusive: "Excludes GST",
};

/**
 * One catalogue product: a large picture, identity, the retailer's case
 * price (and the exact price per unit it contains), stock and scheme.
 */
export function ProductCard({ product }: { product: CatalogueProduct }) {
  const headingId = `product-${product.id}`;
  const unitPrice = perUnitPrice(product.unit_price, product.units_per_case);

  return (
    <article
      aria-labelledby={headingId}
      className={cn(
        "flex h-full flex-col overflow-hidden rounded-xl border bg-card text-card-foreground shadow-xs",
        !product.is_available && "bg-card/60",
      )}
    >
      <ProductImage category={product.category} unavailable={!product.is_available} />
      <div className="flex flex-1 flex-col gap-4 p-5">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {product.brand} · {product.category}
        </p>
        <h3 id={headingId} className="mt-1 text-base leading-snug font-semibold text-balance">
          {product.name}
        </h3>
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          <span className="sr-only">SKU </span>
          {product.sku}
        </p>
        {product.description ? (
          <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">{product.description}</p>
        ) : null}
      </div>

      <div>
        <p className="flex flex-wrap items-baseline gap-x-1.5">
          <span className="text-2xl font-semibold tracking-tight tabular-nums">
            {formatRupees(product.unit_price)}
          </span>
          <span className="text-sm text-muted-foreground">per {product.unit_name}</span>
        </p>
        {unitPrice ? (
          // Exact: shown only when the case price divides into whole paise.
          <p className="mt-0.5 text-sm tabular-nums">
            {formatRupees(unitPrice)} <span className="text-muted-foreground">per unit</span>
          </p>
        ) : null}
        <p className="mt-0.5 text-xs text-muted-foreground">
          {TAX_LABEL[product.tax_treatment]} · {packSize(product)}
        </p>
      </div>

      <StockStatus product={product} />

      <div className="mt-auto flex flex-col gap-4">
        <SchemeInfo product={product} />
        <AddToCart product={product} />
      </div>
      </div>
    </article>
  );
}

export function ProductCardSkeletonList({ count }: { count: number }) {
  return (
    <ul aria-hidden="true" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: count }, (_, index) => (
        <li key={index} className="flex flex-col overflow-hidden rounded-xl border bg-card shadow-xs">
          <Skeleton className="aspect-[16/9] rounded-none" />
          <div className="flex flex-col gap-4 p-5">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-3 w-1/2" />
            <Skeleton className="h-5 w-4/5" />
            <Skeleton className="h-3 w-1/4" />
          </div>
          <Skeleton className="h-7 w-2/5" />
          <Skeleton className="h-5 w-1/3 rounded-full" />
          </div>
        </li>
      ))}
    </ul>
  );
}
