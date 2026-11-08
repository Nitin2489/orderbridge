"use client";

import { AlertCircleIcon, ArrowLeftIcon, CircleCheckIcon } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Spinner } from "@/components/ui/spinner";

import { useVerifyOtp } from "../api";
import { now } from "../clock";
import { formatCountdown, useSecondsUntil } from "../countdown";
import { isRateLimited, verifyOtpErrorMessage } from "../errors";
import { CODE_INCOMPLETE_MESSAGE, otpCodeSchema } from "../schemas";
import { EMPTY_OTP, OtpInput } from "./otp-input";

interface CodeFormProps {
  /** Normalised +91 number the code was requested for. */
  phoneNumber: string;
  /**
   * When the resend button becomes available (epoch milliseconds): the end
   * of the resend cooldown or of a Retry-After window, whichever is later.
   */
  resendAvailableAt: number;
  /** Polite status shown above the code, e.g. after a resend. */
  notice: string | null;
  resendPending: boolean;
  resendError: string | null;
  onResend: () => void;
  onChangeNumber: () => void;
}

/**
 * Step 2: the one-time code. The parent remounts this form (via `key`) for
 * every newly issued code, which resets the boxes, errors and countdown.
 */
export function CodeForm({
  phoneNumber,
  resendAvailableAt,
  notice,
  resendPending,
  resendError,
  onResend,
  onChangeNumber,
}: CodeFormProps) {
  const verify = useVerifyOtp();
  const inFlight = useRef(false);
  const [digits, setDigits] = useState<string[]>([...EMPTY_OTP]);
  const [incomplete, setIncomplete] = useState(false);
  // Incremented after each rejected attempt to send focus back to box 1.
  const [focusRequest, setFocusRequest] = useState(0);
  // After a 429: when the server lets us verify again (epoch milliseconds).
  const [verifyRetryAt, setVerifyRetryAt] = useState(0);
  const resendIn = useSecondsUntil(resendAvailableAt);
  const verifyIn = useSecondsUntil(verifyRetryAt);

  const verifyError = verify.isError ? verifyOtpErrorMessage(verify.error) : null;
  const error = incomplete ? CODE_INCOMPLETE_MESSAGE : verifyError;
  // A rate limit says nothing about the code: show the message, but do not
  // mark the entered digits as invalid.
  const codeInvalid = error !== null && !(!incomplete && isRateLimited(verify.error));
  const busy = verify.isPending || verify.isSuccess;
  const verifyWaiting = verifyIn > 0;

  const handleChange = (next: string[]) => {
    setDigits(next);
    setIncomplete(false);
    if (verify.isError) {
      verify.reset();
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (inFlight.current || busy || verifyWaiting) {
      return;
    }

    const code = digits.join("");
    if (!otpCodeSchema.safeParse(code).success) {
      setIncomplete(true);
      return;
    }

    inFlight.current = true;
    verify.mutate(
      { phoneNumber, code },
      {
        onError: (failure) => {
          if (isRateLimited(failure)) {
            // Not a judgement on the code: keep the digits so the user can
            // retry the same code once the window has passed.
            if (failure.retryAfterSeconds) {
              setVerifyRetryAt(now() + failure.retryAfterSeconds * 1000);
            }
            return;
          }
          // Start the next attempt from empty boxes with focus on the first.
          // Both updates commit together, so focus moves after the boxes are
          // cleared.
          setDigits([...EMPTY_OTP]);
          setFocusRequest((request) => request + 1);
        },
        onSettled: () => {
          inFlight.current = false;
        },
      },
    );
  };

  return (
    <form noValidate className="flex flex-col gap-6" onSubmit={handleSubmit}>
      {notice ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg border border-primary/30 bg-accent/20 px-3.5 py-2.5 text-sm font-medium text-accent-foreground"
        >
          <CircleCheckIcon className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span>{notice}</span>
        </div>
      ) : null}

      <div className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between">
          <p id="otp-label" className="text-sm font-semibold">
            6-digit code
          </p>
          <span className="text-xs font-medium text-muted-foreground">Step 2 of 2</span>
        </div>
        <OtpInput
          value={digits}
          onChange={handleChange}
          labelledBy="otp-label"
          describedBy={error ? "otp-error" : undefined}
          invalid={codeInvalid}
          disabled={busy}
          autoFocus
          focusRequest={focusRequest}
        />
        <FormMessage id="otp-error">{error}</FormMessage>
      </div>

      <Button type="submit" size="lg" className="w-full" disabled={busy || verifyWaiting}>
        {busy ? (
          <>
            <Spinner />
            Verifying…
          </>
        ) : verifyWaiting ? (
          `Try again in ${formatCountdown(verifyIn)}`
        ) : (
          "Verify and sign in"
        )}
      </Button>

      {resendError ? (
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertDescription>{resendError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant="ghost"
          className="h-11 px-2"
          onClick={onChangeNumber}
          disabled={busy}
        >
          <ArrowLeftIcon aria-hidden="true" />
          Change number
        </Button>
        <Button
          type="button"
          variant="ghost"
          className="h-11 px-2"
          onClick={onResend}
          disabled={busy || resendPending || resendIn > 0}
        >
          {resendPending ? (
            <>
              <Spinner />
              Sending…
            </>
          ) : resendIn > 0 ? (
            `Resend code in ${formatCountdown(resendIn)}`
          ) : (
            "Resend code"
          )}
        </Button>
      </div>
    </form>
  );
}
