import { isApiError } from "@/lib/api";

/**
 * What a failed POST /desk/exports means. Server message text is never
 * matched; the response's status and body shape decide.
 *
 * Every kind except "uncertain" is definite: the backend exported nothing,
 * so the next attempt uses a new client_request_id. "uncertain" keeps the
 * id, so a manual retry returns the original batch if the first attempt
 * was in fact created.
 */
export type ExportFailure =
  /** 422 with a message: no confirmed orders were waiting. */
  | { kind: "nothing_eligible" }
  /** 409: orders were not eligible, or the request id was reused. */
  | { kind: "conflict" }
  | { kind: "forbidden" }
  /** Other 4xx, e.g. a malformed request (422 with validation issues). */
  | { kind: "rejected" }
  /** Network failure, 5xx or an unreadable success: the outcome is unknown. */
  | { kind: "uncertain" };

export function exportFailure(error: unknown): ExportFailure {
  if (!isApiError(error)) {
    // Includes a 2xx whose body could not be read or parsed: the batch may
    // have been created.
    return { kind: "uncertain" };
  }
  if (error.status === 0 || error.status >= 500) {
    return { kind: "uncertain" };
  }
  switch (error.status) {
    case 422:
      // FastAPI's request validation sends a list of issues; the
      // "nothing eligible" answer is a single message.
      return typeof error.body?.detail === "string"
        ? { kind: "nothing_eligible" }
        : { kind: "rejected" };
    case 409:
      return { kind: "conflict" };
    case 403:
      return { kind: "forbidden" };
    default:
      return { kind: "rejected" };
  }
}

export function exportFailureMessage(failure: ExportFailure): string {
  switch (failure.kind) {
    case "nothing_eligible":
      return "No confirmed orders are waiting for export. Nothing was exported.";
    case "conflict":
      return "The export could not be created and nothing was exported. Check the orders waiting for export and try again.";
    case "forbidden":
      return "The request was refused and nothing was exported. Reload the page and try again; if it persists, sign in again.";
    case "rejected":
      return "The export request was not accepted and nothing was exported.";
    case "uncertain":
      return "We could not tell whether the export was created. The waiting orders and export history below have been refreshed. Retry to finish it: a retry cannot export the same orders twice.";
  }
}
