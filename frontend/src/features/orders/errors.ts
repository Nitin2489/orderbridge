import { describeWait } from "@/features/auth/countdown";
import { isApiError } from "@/lib/api";

import { orderLinesErrorSchema, type RetailerOrderDetail } from "./types";

export interface RepeatIssue {
  productName: string;
  message: string;
}

/** What a failed repeat means, and what to do with its idempotency key. */
export interface RepeatFailure {
  message: string;
  /** 422: the products that cannot be ordered again, by name, with reasons. */
  issues: RepeatIssue[];
  /** The outcome is unknown: a retry must reuse the same key. */
  uncertain: boolean;
  /**
   * Keep the key for the next attempt: only when the outcome is unknown or
   * the request was merely rate limited. Otherwise the attempt is over and
   * the next explicit repeat starts with a new key.
   */
  keepKey: boolean;
}

const SAFE_RETRY = "Trying again is safe: it cannot create a second order.";

function failure(message: string, extra: Partial<RepeatFailure> = {}): RepeatFailure {
  return { message, issues: [], uncertain: false, keepKey: false, ...extra };
}

export function repeatFailure(error: unknown, source: RetailerOrderDetail): RepeatFailure {
  if (!isApiError(error)) {
    return failure(`We could not confirm whether the new order was created. ${SAFE_RETRY}`, {
      uncertain: true,
      keepKey: true,
    });
  }

  switch (error.status) {
    case 0:
      return failure(`We could not reach OrderBridge. ${SAFE_RETRY}`, {
        uncertain: true,
        keepKey: true,
      });
    case 401:
      return failure("Your session has ended. Please sign in again.");
    case 403:
      return failure("Your account cannot place orders right now. Please contact the order desk.");
    case 404:
      return failure("The original order is no longer available, so it cannot be repeated.");
    case 409:
      return failure(
        "This repeat request conflicts with an earlier request, so no new order was created. " +
          "Choose Repeat order again to start a new request.",
      );
    case 422: {
      const parsed = orderLinesErrorSchema.safeParse(error.body);
      if (parsed.success) {
        const names = new Map(source.items.map((line) => [line.product_id, line.product_name]));
        return failure(
          "Some products cannot be ordered again as they are. No new order was created.",
          {
            issues: parsed.data.issues.map((issue) => ({
              productName: names.get(issue.product_id) ?? `Line ${issue.line_number}`,
              message: issue.message,
            })),
          },
        );
      }
      return failure(
        "This order cannot be repeated as it is. No new order was created. " +
          "Please place a new order from the catalogue.",
      );
    }
    case 429: {
      const wait = error.retryAfterSeconds;
      return failure(
        wait
          ? `Too many attempts. Please wait ${describeWait(wait)} and try again.`
          : "Too many attempts. Please wait a few minutes and try again.",
        { keepKey: true },
      );
    }
    default:
      if (error.status >= 500) {
        return failure(`Something went wrong on our side. ${SAFE_RETRY}`, {
          uncertain: true,
          keepKey: true,
        });
      }
      return failure("The order could not be repeated. Please try again.");
  }
}
