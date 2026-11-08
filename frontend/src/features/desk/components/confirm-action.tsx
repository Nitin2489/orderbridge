"use client";

import { CircleCheckIcon, TriangleAlertIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { formatRupees } from "@/features/catalogue/format";

import type { DeskOrderPreview } from "../types";

interface ConfirmActionProps {
  orderNumber: string;
  /** The preview currently shown (undefined while loading). */
  preview: DeskOrderPreview | undefined;
  /** Another action is in flight. */
  disabled: boolean;
  pending: boolean;
  /** The last attempt's outcome is unknown: offer it as a retry. */
  retry: boolean;
  onConfirm: (fingerprint: string) => void;
}

/**
 * Confirming takes two explicit steps. Opening the step snapshots the
 * preview being shown; "Yes, confirm" sends exactly that preview's
 * fingerprint. If the preview changes underneath (a refresh, or the
 * backend's 409 with fresh figures), the snapshot no longer matches and the
 * desk must review the new figures and confirm again.
 */
export function ConfirmAction({
  orderNumber,
  preview,
  disabled,
  pending,
  retry,
  onConfirm,
}: ConfirmActionProps) {
  const [approved, setApproved] = useState<DeskOrderPreview | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const stale = approved !== null && preview?.fingerprint !== approved.fingerprint;
  const canStart = Boolean(preview?.can_confirm && preview.fingerprint);

  useEffect(() => {
    if (approved) {
      headingRef.current?.focus();
    }
  }, [approved]);

  if (!approved) {
    return (
      <div className="flex flex-col gap-1">
        <Button
          type="button"
          size="lg"
          className="w-fit"
          disabled={!canStart || disabled}
          onClick={() => preview && setApproved(preview)}
        >
          <CircleCheckIcon aria-hidden="true" />
          Confirm order
        </Button>
        {preview && !preview.can_confirm ? (
          <p className="text-sm text-muted-foreground">
            Resolve the line issues before confirming, or reject the order.
          </p>
        ) : null}
      </div>
    );
  }

  if (stale) {
    return (
      <Alert variant="info" role="alert">
        <TriangleAlertIcon aria-hidden="true" />
        <AlertDescription>
          <p>
            The figures changed after you started confirming. Nothing was confirmed. Review the
            current pricing above, then confirm again.
          </p>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => setApproved(null)}
          >
            Review again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <section
      aria-labelledby="confirm-order-heading"
      className="flex flex-col gap-3 rounded-xl border bg-card p-5 shadow-xs"
    >
      <h2
        id="confirm-order-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        Confirm order {orderNumber}?
      </h2>
      <p className="text-sm">
        At the current pricing shown above:{" "}
        <span className="font-semibold tabular-nums">{formatRupees(approved.grand_total)}</span>{" "}
        (excl. GST). Stock is deducted and the retailer&apos;s balance increases by this amount.
      </p>
      {approved.exceeds_credit_limit ? (
        <p className="text-sm font-medium text-destructive">
          This exceeds the retailer&apos;s available credit of{" "}
          {formatRupees(approved.available_credit)}: the order will be placed on hold instead of
          being confirmed.
        </p>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button
          type="button"
          size="lg"
          disabled={disabled || pending}
          onClick={() => {
            if (approved.fingerprint) {
              onConfirm(approved.fingerprint);
            }
          }}
        >
          {pending ? (
            <>
              <Spinner />
              Confirming…
            </>
          ) : retry ? (
            "Retry confirming"
          ) : (
            "Yes, confirm order"
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="lg"
          disabled={pending}
          onClick={() => setApproved(null)}
        >
          Cancel
        </Button>
      </div>
    </section>
  );
}
