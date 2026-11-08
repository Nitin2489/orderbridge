import { CircleCheckIcon, CircleSlashIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

import { formatCount, unitLabel } from "../format";
import type { CatalogueProduct } from "../types";

/**
 * Availability from the backend's is_available (stock_quantity > 0). The
 * backend defines no low-stock threshold, so none is invented here; the
 * exact stock count is shown instead.
 */
export function StockStatus({ product }: { product: CatalogueProduct }) {
  if (!product.is_available) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="destructive">
          <CircleSlashIcon aria-hidden="true" />
          Out of stock
        </Badge>
        <span className="text-sm text-muted-foreground">Currently unavailable</span>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="success">
        <CircleCheckIcon aria-hidden="true" />
        In stock
      </Badge>
      <span className="text-sm text-muted-foreground">
        {formatCount(product.stock_quantity)}{" "}
        {unitLabel(product.unit_name, product.stock_quantity)} available
      </span>
    </div>
  );
}
