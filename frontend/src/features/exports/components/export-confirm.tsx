"use client";

import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { formatCount, formatRupees } from "@/features/catalogue/format";

import type { EligibleExport } from "../types";

interface ExportConfirmProps {
  /** The waiting summary last read from the backend, if any. */
  eligible: EligibleExport | undefined;
  pending: boolean;
  /** The last attempt's outcome is unknown: offer it again, unchanged. */
  retry: boolean;
  onExport: () => void;
  onCancel: () => void;
}

function orders(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "order" : "orders"}`;
}

/** The explicit second step before an export is created. */
export function ExportConfirm({ eligible, pending, retry, onExport, onCancel }: ExportConfirmProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section
      aria-labelledby="export-confirm-heading"
      className="flex flex-col gap-3 rounded-xl border bg-card p-5 shadow-xs"
    >
      <h2
        id="export-confirm-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        Export all confirmed orders waiting now?
      </h2>
      {eligible && eligible.order_count > 0 ? (
        <p className="text-sm">
          Currently waiting: {orders(eligible.order_count)},{" "}
          <span className="font-semibold tabular-nums">{formatRupees(eligible.net_total)}</span>{" "}
          (excl. GST).
        </p>
      ) : null}
      <ul className="list-disc space-y-1 pl-5 text-sm">
        <li>
          Every order still confirmed and not yet exported when you confirm is included, so the
          final count can differ from the figure above. The result shows the exact count and
          total.
        </li>
        <li>
          Exported orders change from <strong>Confirmed</strong> to <strong>Exported</strong>{" "}
          and will not be exported again.
        </li>
        <li>
          The file is a generic CSV, not a native Tally import: ledgers, GST and voucher types
          must be mapped in Tally.
        </li>
      </ul>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="button" size="lg" disabled={pending} onClick={onExport}>
          {pending ? (
            <>
              <Spinner />
              Exporting…
            </>
          ) : retry ? (
            "Retry export"
          ) : (
            "Yes, export orders"
          )}
        </Button>
        <Button type="button" variant="ghost" size="lg" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
