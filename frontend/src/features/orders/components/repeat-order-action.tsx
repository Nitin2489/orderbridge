"use client";

import { AlertCircleIcon, RepeatIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { formatCountdown, useSecondsUntil } from "@/features/auth/countdown";
import { isApiError } from "@/lib/api";

import { useRepeatOrder } from "../api";
import { repeatFailure } from "../errors";
import { useRepeatAttempts } from "../repeat-attempts";
import type { RetailerOrderDetail } from "../types";

/**
 * Repeat an order: after an explicit confirmation, POST
 * /retailer/orders/{id}/repeat with an idempotency key from the retailer's
 * pending repeat attempts. The key is kept while the outcome is unknown
 * (network or server failure, 429) and released once it is final.
 */
export function RepeatOrderAction({
  order,
  onRepeated,
}: {
  order: RetailerOrderDetail;
  onRepeated: (created: RetailerOrderDetail) => void;
}) {
  const attempts = useRepeatAttempts();
  const inFlight = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [confirming, setConfirming] = useState(false);

  const repeat = useRepeatOrder({
    onSuccess: (created, { sourceOrderId, clientRequestId }) => {
      attempts.finish(sourceOrderId, clientRequestId);
      onRepeated(created);
    },
    onError: (error, { sourceOrderId, clientRequestId }) => {
      if (!repeatFailure(error, order).keepKey) {
        attempts.finish(sourceOrderId, clientRequestId);
      }
    },
  });

  const failure = repeat.isError ? repeatFailure(repeat.error, order) : null;
  const retryAfter =
    isApiError(repeat.error) && repeat.error.status === 429 ? repeat.error.retryAfterSeconds : null;
  const waitSeconds = useSecondsUntil(
    retryAfter && repeat.submittedAt ? repeat.submittedAt + retryAfter * 1000 : 0,
  );
  const waiting = waitSeconds > 0;

  useEffect(() => {
    if (confirming) {
      headingRef.current?.focus();
    }
  }, [confirming]);

  const submit = () => {
    if (inFlight.current || repeat.isPending || waiting) {
      return;
    }
    // Same key while an earlier attempt's outcome is unknown; a new one otherwise.
    const clientRequestId = attempts.keyFor(order.id);
    if (!clientRequestId) {
      return;
    }
    inFlight.current = true;
    repeat.mutate(
      { sourceOrderId: order.id, clientRequestId },
      {
        onSettled: () => {
          inFlight.current = false;
        },
      },
    );
  };

  if (!confirming) {
    return (
      <Button type="button" variant="outline" size="lg" onClick={() => setConfirming(true)}>
        <RepeatIcon aria-hidden="true" />
        Repeat order
      </Button>
    );
  }

  return (
    <section
      aria-labelledby="repeat-order-heading"
      className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs"
    >
      <h2
        id="repeat-order-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        Repeat order {order.order_number}?
      </h2>
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
        <li>A new order will be created.</li>
        <li>It will contain the same products and quantities.</li>
        <li>Current prices, schemes and stock will be checked again.</li>
        <li>The new order will go to the order desk for review.</li>
        <li>This order will remain unchanged.</li>
      </ul>

      {failure ? (
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertDescription>
            <p>{failure.message}</p>
            {failure.issues.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {failure.issues.map((issue, index) => (
                  <li key={`${issue.productName}-${index}`}>
                    <span className="font-medium">{issue.productName}:</span> {issue.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-col gap-2 sm:flex-row">
        <Button
          type="button"
          size="lg"
          disabled={repeat.isPending || waiting}
          onClick={submit}
        >
          {repeat.isPending ? (
            <>
              <Spinner />
              Creating order…
            </>
          ) : waiting ? (
            `Try again in ${formatCountdown(waitSeconds)}`
          ) : failure?.uncertain ? (
            "Retry creating order"
          ) : (
            "Create new order"
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="lg"
          disabled={repeat.isPending}
          onClick={() => {
            repeat.reset();
            setConfirming(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </section>
  );
}
