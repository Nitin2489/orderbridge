import { apiDownload, isApiError } from "@/lib/api";

import { batchCsvApiPath } from "./paths";
import type { DownloadableBatch } from "./types";

/**
 * Downloading an export's CSV.
 *
 * The file is fetched through the same-origin API proxy with the session
 * cookie (a plain link would show a raw JSON error page on 401 or 404).
 * When the browser has Web Crypto, the bytes' SHA-256 must equal the
 * checksum the backend recorded for the batch, or the file is not saved.
 * Without Web Crypto (an insecure context or an old browser) the file can
 * only be saved unverified, and the UI says so before and after.
 */

export const CSV_ACCEPT = "text/csv, application/json;q=0.9";
export const CSV_TYPE = "text/csv;charset=utf-8";

export class ChecksumMismatchError extends Error {
  constructor() {
    super("The downloaded file does not match the export's checksum.");
    this.name = "ChecksumMismatchError";
  }
}

export class VerificationError extends Error {
  constructor() {
    super("The downloaded file could not be verified.");
    this.name = "VerificationError";
  }
}

export class UnexpectedFileError extends Error {
  constructor() {
    super("The server did not return a CSV file.");
    this.name = "UnexpectedFileError";
  }
}

/** The signed-in user changed while the file was downloading. */
export class SessionChangedError extends Error {
  constructor() {
    super("The session changed during the download.");
    this.name = "SessionChangedError";
  }
}

/** True when this browser can compute SHA-256 (Web Crypto's SubtleCrypto). */
export function canVerifyDownloads(): boolean {
  return typeof globalThis.crypto?.subtle?.digest === "function";
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}

export interface FetchedCsv {
  bytes: ArrayBuffer;
  /** true when the SHA-256 was checked and matched. */
  verified: boolean;
}

/**
 * Fetch a batch's CSV. With `verify`, a checksum that cannot be computed or
 * does not match throws, so the caller never has unverified bytes to save.
 */
export async function fetchBatchCsv(
  batch: DownloadableBatch,
  { verify }: { verify: boolean },
): Promise<FetchedCsv> {
  const { bytes, contentType } = await apiDownload(batchCsvApiPath(batch.id), {
    accept: CSV_ACCEPT,
  });
  if (!contentType.toLowerCase().startsWith("text/csv")) {
    throw new UnexpectedFileError();
  }
  if (!verify) {
    return { bytes, verified: false };
  }

  let actual: string;
  try {
    actual = await sha256Hex(bytes);
  } catch {
    throw new VerificationError();
  }
  if (actual !== batch.csv_sha256.toLowerCase()) {
    throw new ChecksumMismatchError();
  }
  return { bytes, verified: true };
}

/** A message for a failed download, or null when nothing should be shown. */
export function downloadErrorMessage(error: unknown): string | null {
  if (error instanceof SessionChangedError) {
    return null;
  }
  if (error instanceof ChecksumMismatchError) {
    return "The downloaded file did not match the checksum recorded for this export, so it was not saved. Try downloading again.";
  }
  if (error instanceof VerificationError) {
    return "The downloaded file could not be verified, so it was not saved. Try downloading again.";
  }
  if (error instanceof UnexpectedFileError) {
    return "The server did not return a CSV file, so nothing was saved.";
  }
  if (isApiError(error)) {
    if (error.status === 0) {
      return error.message;
    }
    if (error.status === 404) {
      return "This export batch was not found.";
    }
    if (error.status === 403) {
      return "The download was refused. Reload the page and try again.";
    }
    if (error.status >= 500) {
      return "Something went wrong on our side. Please try downloading again.";
    }
  }
  return "The CSV could not be downloaded. Please try again.";
}
