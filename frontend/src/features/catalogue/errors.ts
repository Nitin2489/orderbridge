import { isApiError } from "@/lib/api";

/** A short, user-facing reason a catalogue request failed. */
export function catalogueErrorMessage(error: unknown): string {
  if (isApiError(error) && (error.status === 0 || error.status >= 500)) {
    return error.message;
  }
  return "The catalogue could not be loaded.";
}
