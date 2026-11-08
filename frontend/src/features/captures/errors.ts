import { isApiError } from "@/lib/api";

import {
  aliasConflictSchema,
  linesInvalidSchema,
  notReadySchema,
  staleCaptureSchema,
  type LineIssue,
} from "./types";

/**
 * What a failed capture request means, from its status and body shape
 * (never from message text). "uncertain" outcomes are re-read from the
 * server; nothing is retried automatically.
 */
export type CaptureFailure =
  | { kind: "stale"; currentVersion: number; message: string }
  | { kind: "not_ready"; unresolvedLines: number[]; message: string }
  | { kind: "alias_conflict"; lineNumber: number; aliasText: string; message: string }
  | { kind: "lines_invalid"; issues: LineIssue[]; message: string }
  | { kind: "state"; message: string }
  | { kind: "invalid"; message: string }
  | { kind: "forbidden"; message: string }
  | { kind: "not_found"; message: string }
  | { kind: "rate_limited"; message: string }
  | { kind: "uncertain"; message: string };

const UNCERTAIN = "We could not tell whether that went through. The capture has been reloaded.";

export function captureFailure(error: unknown): CaptureFailure {
  if (!isApiError(error)) {
    return { kind: "uncertain", message: UNCERTAIN };
  }
  const body = error.body;
  const message = error.message;
  switch (error.status) {
    case 409: {
      const stale = staleCaptureSchema.safeParse(body);
      if (stale.success) {
        return {
          kind: "stale",
          currentVersion: stale.data.current_version,
          message: "Someone else changed this capture. It has been reloaded; check it and try again.",
        };
      }
      const notReady = notReadySchema.safeParse(body);
      if (notReady.success) {
        return { kind: "not_ready", unresolvedLines: notReady.data.unresolved_lines, message };
      }
      const alias = aliasConflictSchema.safeParse(body);
      if (alias.success) {
        return {
          kind: "alias_conflict",
          lineNumber: alias.data.line_number,
          aliasText: alias.data.alias_text,
          message,
        };
      }
      return { kind: "state", message };
    }
    case 422: {
      const lines = linesInvalidSchema.safeParse(body);
      if (lines.success) {
        return { kind: "lines_invalid", issues: lines.data.issues, message: lines.data.detail };
      }
      return { kind: "invalid", message };
    }
    case 403:
      return { kind: "forbidden", message };
    case 404:
      return { kind: "not_found", message: "This capture no longer exists or is not yours." };
    case 429:
      return { kind: "rate_limited", message };
    default:
      return error.status === 0 || error.status >= 500
        ? { kind: "uncertain", message: error.status === 0 ? message : UNCERTAIN }
        : { kind: "invalid", message };
  }
}

/** Failures after which the capture must be re-read from the server. */
export function needsReload(failure: CaptureFailure): boolean {
  return ["stale", "state", "uncertain", "not_ready"].includes(failure.kind);
}
