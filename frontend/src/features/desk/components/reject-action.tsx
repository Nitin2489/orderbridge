"use client";

import { CircleXIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

/** The backend's limits (desk_order_service), applied after trimming. */
export const REJECTION_REASON_MIN_LENGTH = 3;
export const REJECTION_REASON_MAX_LENGTH = 500;

interface RejectActionProps {
  orderNumber: string;
  disabled: boolean;
  pending: boolean;
  retry: boolean;
  onReject: (reason: string) => void;
}

/** Reject with a free-text reason, after an explicit confirmation step. */
export function RejectAction({ orderNumber, disabled, pending, retry, onReject }: RejectActionProps) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [showError, setShowError] = useState(false);
  const fieldRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) {
      fieldRef.current?.focus();
    }
  }, [open]);

  const trimmed = reason.trim();
  const valid =
    trimmed.length >= REJECTION_REASON_MIN_LENGTH && trimmed.length <= REJECTION_REASON_MAX_LENGTH;

  if (!open) {
    return (
      <Button
        type="button"
        variant="outline"
        size="lg"
        className="w-fit"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <CircleXIcon aria-hidden="true" />
        Reject order
      </Button>
    );
  }

  return (
    <form
      noValidate
      aria-labelledby="reject-order-heading"
      className="flex flex-col gap-3 rounded-xl border bg-card p-5 shadow-xs"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid) {
          setShowError(true);
          return;
        }
        onReject(trimmed);
      }}
    >
      <h2 id="reject-order-heading" className="text-lg font-semibold">
        Reject order {orderNumber}?
      </h2>
      <p className="text-sm text-muted-foreground">
        The retailer&apos;s stock and balance are not affected. This cannot be undone.
      </p>
      <div className="flex flex-col gap-2">
        <Label htmlFor="reject-reason">Reason for rejection</Label>
        <textarea
          id="reject-reason"
          ref={fieldRef}
          rows={3}
          maxLength={REJECTION_REASON_MAX_LENGTH * 2}
          value={reason}
          disabled={pending}
          aria-invalid={showError && !valid ? true : undefined}
          aria-describedby="reject-reason-hint reject-reason-error"
          onChange={(event) => {
            setReason(event.target.value);
            setShowError(false);
          }}
          className="w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 aria-invalid:border-destructive"
        />
        <p id="reject-reason-hint" className="text-xs text-muted-foreground">
          The retailer will see this reason. {REJECTION_REASON_MIN_LENGTH} to{" "}
          {REJECTION_REASON_MAX_LENGTH} characters · {trimmed.length} entered
        </p>
        <FormMessage id="reject-reason-error">
          {showError && !valid
            ? `Enter a reason of ${REJECTION_REASON_MIN_LENGTH} to ${REJECTION_REASON_MAX_LENGTH} characters.`
            : null}
        </FormMessage>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="submit" variant="destructive" size="lg" disabled={disabled || pending}>
          {pending ? (
            <>
              <Spinner />
              Rejecting…
            </>
          ) : retry ? (
            "Retry rejecting"
          ) : (
            "Reject order"
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="lg"
          disabled={pending}
          onClick={() => {
            setOpen(false);
            setShowError(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
