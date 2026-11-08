import { ChevronDownIcon, TagIcon, TriangleAlertIcon } from "lucide-react";

import {
  describeScheme,
  formatRupees,
  schemeBenefit,
  schemeValidity,
  unitLabel,
} from "../format";
import type { CatalogueProduct, SchemeReference } from "../types";

const ZERO = /^-?0+(\.0+)?$/;

function ReferenceExample({
  reference,
  unitName,
}: {
  reference: SchemeReference;
  unitName: string;
}) {
  const rows: { label: string; value: string }[] = [
    {
      label: `${reference.quantity} ${unitLabel(unitName, reference.quantity)}`,
      value: formatRupees(reference.subtotal),
    },
  ];
  if (!ZERO.test(reference.discount_amount)) {
    rows.push({ label: "Discount", value: `−${formatRupees(reference.discount_amount)}` });
  }
  if (reference.free_quantity > 0) {
    rows.push({
      label: "Free",
      value: `${reference.free_quantity} ${unitLabel(unitName, reference.free_quantity)}`,
    });
  }

  return (
    <details className="group mt-2">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-md text-sm font-medium text-primary outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        Example at {reference.quantity} {unitLabel(unitName, reference.quantity)}
        <ChevronDownIcon
          aria-hidden="true"
          className="size-4 transition-transform group-open:rotate-180 motion-reduce:transition-none"
        />
      </summary>
      <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
        {rows.map(({ label, value }) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right tabular-nums">{value}</dd>
          </div>
        ))}
        <div className="contents">
          <dt className="border-t pt-1 font-medium">Total</dt>
          <dd className="border-t pt-1 text-right font-semibold tabular-nums">
            {formatRupees(reference.illustrative_total)}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        An illustration, not a quote. Excludes GST; the final price is calculated when you
        place the order.
      </p>
    </details>
  );
}

/**
 * The product's current scheme exactly as the backend reports it. Nothing
 * is calculated here: the rule comes from the scheme fields and the example
 * amounts from the backend's own reference calculation.
 */
export function SchemeInfo({ product }: { product: CatalogueProduct }) {
  if (product.scheme_status === "configuration_error") {
    return (
      <div className="flex gap-2 rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-400/30 dark:bg-amber-950 dark:text-amber-100">
        <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <p>
          <span className="font-medium">Scheme under review.</span> This product cannot be
          ordered until your distributor corrects its offer.
        </p>
      </div>
    );
  }

  const scheme = product.scheme_status === "active" ? product.scheme : null;
  if (!scheme) {
    return null;
  }

  const rule = describeScheme(scheme, product.unit_name);
  const validity = schemeValidity(scheme);

  return (
    <div className="rounded-lg bg-accent/70 p-3 text-accent-foreground">
      <p className="flex items-start gap-2 text-sm font-semibold">
        <TagIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span>
          <span className="sr-only">Scheme: </span>
          {rule}
        </span>
      </p>
      <p className="mt-1 pl-6 text-sm">{schemeBenefit(scheme, product.unit_name)}</p>
      {scheme.name !== rule || validity ? (
        <p className="mt-1 pl-6 text-xs opacity-80">
          {[scheme.name !== rule ? scheme.name : null, validity].filter(Boolean).join(" · ")}
        </p>
      ) : null}
      {scheme.reference ? (
        <div className="pl-6">
          <ReferenceExample reference={scheme.reference} unitName={product.unit_name} />
        </div>
      ) : null}
    </div>
  );
}
