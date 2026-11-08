import { describeWait } from "@/features/auth/countdown";
import { isApiError } from "@/lib/api";

import { orderLinesErrorSchema, type OrderLineIssue } from "@/features/orders/types";

/**
 * What a failed submission means for the retailer, and whether retrying the
 * same submission (same key, same payload) is safe and useful.
 */
export interface SubmissionFailure {
  message: string;
  /** Per product: the backend's reason the line cannot be ordered (422). */
  lineIssues: Map<string, OrderLineIssue>;
  /**
   * The outcome is unknown (network failure or server error): the order may
   * or may not exist, so a retry must reuse the same key and payload.
   */
  uncertain: boolean;
  /** 409: the key was used for a different request; a new key is needed. */
  conflict: boolean;
}

const RETRY_SAFELY =
  "Your cart is kept. Submitting again is safe: it cannot create a second order.";

function failure(message: string, extra: Partial<SubmissionFailure> = {}): SubmissionFailure {
  return { message, lineIssues: new Map(), uncertain: false, conflict: false, ...extra };
}

export function submissionFailure(error: unknown): SubmissionFailure {
  if (!isApiError(error)) {
    // An unreadable success response: the order may exist.
    return failure(`We could not confirm your order. ${RETRY_SAFELY}`, { uncertain: true });
  }

  switch (error.status) {
    case 0:
      return failure(`We could not reach OrderBridge. ${RETRY_SAFELY}`, { uncertain: true });
    case 401:
      // The session handling signs the user out; nothing to explain here.
      return failure("Your session has ended. Please sign in again.");
    case 403:
      return failure(
        "Your account cannot place orders right now. Please contact the order desk.",
      );
    case 409:
      return failure(
        "This submission conflicts with an earlier request, so no new order was created. " +
          "Review your cart and submit again to place it as a new order.",
        { conflict: true },
      );
    case 422: {
      const parsed = orderLinesErrorSchema.safeParse(error.body);
      if (parsed.success) {
        return failure(
          "Some items cannot be ordered as they are. No order was created; " +
            "adjust or remove the highlighted items.",
          {
            lineIssues: new Map(parsed.data.issues.map((issue) => [issue.product_id, issue])),
          },
        );
      }
      return failure("The order could not be accepted. Check your cart and try again.");
    }
    case 429: {
      const wait = error.retryAfterSeconds;
      return failure(
        wait
          ? `Too many attempts. Please wait ${describeWait(wait)} and try again. Your cart is kept.`
          : "Too many attempts. Please wait a few minutes and try again. Your cart is kept.",
      );
    }
    default:
      if (error.status >= 500) {
        // Never show server internals; the outcome is unknown.
        return failure(`Something went wrong on our side. ${RETRY_SAFELY}`, { uncertain: true });
      }
      return failure("The order could not be submitted. Please try again.");
  }
}
