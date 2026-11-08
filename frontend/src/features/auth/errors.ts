import { isApiError, type ApiError } from "@/lib/api";

import { describeWait } from "./countdown";

/**
 * User-facing messages for auth failures.
 *
 * The backend deliberately returns one 401 for every verification failure
 * (wrong, expired, already used or too many attempts) and one generic 200
 * for every OTP request, so the UI cannot, and must not try to, say which
 * case happened or whether a number has an account.
 */

const FALLBACK = "Something went wrong. Please try again.";

/**
 * 429 from the OTP endpoints' rate limits. The same text whatever the
 * number, so it reveals nothing about the account.
 */
export function tooManyAttemptsMessage(error: ApiError): string {
  const wait = error.retryAfterSeconds;
  if (wait === null || wait <= 0) {
    return "Too many attempts. Please wait a few minutes and try again.";
  }
  return `Too many attempts. Please wait ${describeWait(wait)} and try again.`;
}

/** True for a 429 response. */
export function isRateLimited(error: unknown): error is ApiError {
  return isApiError(error) && error.status === 429;
}

export function requestOtpErrorMessage(error: unknown): string {
  if (!isApiError(error)) {
    return FALLBACK;
  }
  if (error.status === 422) {
    return "Enter a valid 10-digit Indian mobile number.";
  }
  if (error.status === 429) {
    return tooManyAttemptsMessage(error);
  }
  return error.status === 0 || error.status >= 500 ? error.message : FALLBACK;
}

export function verifyOtpErrorMessage(error: unknown): string {
  if (!isApiError(error)) {
    return FALLBACK;
  }
  switch (error.status) {
    case 401:
      return "That code is incorrect or has expired. Check the code, or request a new one.";
    case 403:
      return "Sign-in was blocked by a security check. Reload the page and try again.";
    case 422:
      return "Enter the 6-digit code.";
    case 429:
      return tooManyAttemptsMessage(error);
    default:
      return error.status === 0 || error.status >= 500 ? error.message : FALLBACK;
  }
}

export function logoutErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 0) {
    return "Could not sign out: no connection. Try again.";
  }
  if (isApiError(error) && error.status === 403) {
    return "Sign-out was blocked by a security check. Reload the page and try again.";
  }
  return "Could not sign out. Try again.";
}

export function sessionErrorMessage(error: unknown): string {
  if (isApiError(error) && (error.status === 0 || error.status >= 500)) {
    return error.message;
  }
  return "We could not check your session.";
}
