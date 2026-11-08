import { isApiError } from "@/lib/api";

import {
  orderIssuesSchema,
  previewOutdatedSchema,
  type DeskOrderPreview,
  type LineIssue,
} from "./types";

/**
 * What a failed confirm or reject means. Server message text is never
 * matched: ambiguous outcomes are resolved by re-reading the order.
 */
export type DeskFailure =
  /** 409 with a fresh preview: the figures changed; nothing was done. */
  | { kind: "outdated"; preview: DeskOrderPreview }
  /** 422 with line issues: nothing was done. */
  | { kind: "issues"; issues: LineIssue[] }
  /** Other 409: the order's state does not allow it; re-read the order. */
  | { kind: "state" }
  | { kind: "not_found" }
  | { kind: "forbidden" }
  /** Other 422: the request itself was invalid (e.g. the reason). */
  | { kind: "invalid" }
  /** Network failure, 5xx or unreadable response: the outcome is unknown. */
  | { kind: "uncertain" };

export function deskFailure(error: unknown): DeskFailure {
  if (!isApiError(error)) {
    return { kind: "uncertain" };
  }
  switch (error.status) {
    case 409: {
      const outdated = previewOutdatedSchema.safeParse(error.body);
      return outdated.success
        ? { kind: "outdated", preview: outdated.data.preview }
        : { kind: "state" };
    }
    case 422: {
      const issues = orderIssuesSchema.safeParse(error.body);
      return issues.success ? { kind: "issues", issues: issues.data.issues } : { kind: "invalid" };
    }
    case 404:
      return { kind: "not_found" };
    case 403:
      return { kind: "forbidden" };
    default:
      return error.status === 0 || error.status >= 500 ? { kind: "uncertain" } : { kind: "state" };
  }
}

/** Failures whose real outcome must be read back from the server. */
export function needsRefresh(failure: DeskFailure): boolean {
  return failure.kind === "state" || failure.kind === "uncertain" || failure.kind === "not_found";
}
