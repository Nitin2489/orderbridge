"use client";

import { useRef, useState } from "react";

import { useRequestOtp } from "../api";
import { now } from "../clock";
import { isRateLimited, requestOtpErrorMessage } from "../errors";
import { formatPhoneNumber, nationalNumber } from "../schemas";
import { AuthLayout } from "./auth-layout";
import { CodeForm } from "./code-form";
import { PhoneForm } from "./phone-form";

/**
 * Mirrors the backend's AUTH_OTP_RESEND_COOLDOWN_SECONDS default. The
 * backend does not report the remaining cooldown (a request inside it just
 * gets the same generic response and no new code), so the client keeps its
 * own countdown per number.
 */
export const RESEND_COOLDOWN_SECONDS = 60;

interface IssuedCode {
  /** When the current code was requested (epoch milliseconds). */
  at: number;
}

/**
 * Track when a usable code was last issued for a number. A request inside
 * the cooldown issues nothing new, so the earlier code and countdown stay.
 */
function nextIssuedCode(previous: IssuedCode | undefined, timestamp: number): IssuedCode {
  if (previous && timestamp - previous.at < RESEND_COOLDOWN_SECONDS * 1000) {
    return previous;
  }
  return { at: timestamp };
}

/** The two-step OTP sign-in: mobile number, then code. */
export function SignInFlow() {
  const requestOtp = useRequestOtp();
  const inFlight = useRef(false);
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phoneNumber, setPhoneNumber] = useState<string | null>(null);
  const [issued, setIssued] = useState<Record<string, IssuedCode>>({});
  const [resent, setResent] = useState(false);
  // Per number: when the backend's Retry-After (429) lets us ask again.
  const [retryAt, setRetryAt] = useState<Record<string, number>>({});
  // The number the latest request was for, so its error is only shown for it.
  const [requestedNumber, setRequestedNumber] = useState<string | null>(null);

  const sendCode = (target: string, isResend: boolean) => {
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    setRequestedNumber(target);
    requestOtp.mutate(target, {
      onSuccess: () => {
        const timestamp = now();
        setIssued((current) => ({
          ...current,
          [target]: nextIssuedCode(current[target], timestamp),
        }));
        setPhoneNumber(target);
        setResent(isResend);
        setStep("code");
      },
      onError: (error) => {
        // Hold this number's request button until the server's window ends.
        if (isRateLimited(error) && error.retryAfterSeconds) {
          const until = now() + error.retryAfterSeconds * 1000;
          setRetryAt((current) => ({ ...current, [target]: until }));
        }
      },
      onSettled: () => {
        inFlight.current = false;
      },
    });
  };

  const requestError = requestOtp.isError ? requestOtpErrorMessage(requestOtp.error) : null;
  const current = phoneNumber ? issued[phoneNumber] : undefined;

  if (step === "code" && phoneNumber && current) {
    return (
      <AuthLayout
        title="Enter your code"
        description={
          <p>
            If <span className="font-medium text-foreground">{formatPhoneNumber(phoneNumber)}</span>{" "}
            is registered with OrderBridge, a 6-digit code is on its way. It expires in a few
            minutes.
          </p>
        }
      >
        <CodeForm
          key={`${phoneNumber}:${current.at}`}
          phoneNumber={phoneNumber}
          resendAvailableAt={Math.max(
            current.at + RESEND_COOLDOWN_SECONDS * 1000,
            retryAt[phoneNumber] ?? 0,
          )}
          notice={resent ? "A new code has been requested." : null}
          resendPending={requestOtp.isPending}
          resendError={requestError}
          onResend={() => sendCode(phoneNumber, true)}
          onChangeNumber={() => {
            requestOtp.reset();
            setResent(false);
            setStep("phone");
          }}
        />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Sign in to OrderBridge"
      description={<p>Use the mobile number registered with your distributor.</p>}
    >
      <PhoneForm
        defaultPhone={phoneNumber ? nationalNumber(phoneNumber) : ""}
        pending={requestOtp.isPending}
        error={requestError}
        errorPhoneNumber={requestedNumber}
        retryAt={retryAt}
        onSubmit={(target) => sendCode(target, false)}
      />
    </AuthLayout>
  );
}
