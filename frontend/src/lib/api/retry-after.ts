/**
 * Parsing of the HTTP Retry-After response header (RFC 9110 §10.2.3).
 *
 * The value is either a number of seconds ("120") or an HTTP date in the
 * IMF-fixdate form ("Sun, 06 Nov 1994 08:49:37 GMT"). Anything else is
 * ignored rather than guessed at. Results are clamped to
 * [0, MAX_RETRY_AFTER_SECONDS], so a hostile or broken value can never
 * produce a negative wait or lock the UI for an absurd time.
 */

/** One day: longer than any OrderBridge rate-limit window. */
export const MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60;

const DELAY_SECONDS = /^\d+$/;
const IMF_FIXDATE =
  /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/;

function clamp(seconds: number): number {
  return Math.min(MAX_RETRY_AFTER_SECONDS, Math.max(0, seconds));
}

/**
 * Seconds to wait, or null when the header is absent or unusable.
 * `now` (epoch milliseconds) is only used for HTTP-date values.
 */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (value === null) {
    return null;
  }
  const trimmed = value.trim();

  if (DELAY_SECONDS.test(trimmed)) {
    const seconds = Number(trimmed);
    // A very long digit string becomes Infinity; that is clamped too.
    return clamp(Number.isFinite(seconds) ? seconds : MAX_RETRY_AFTER_SECONDS);
  }

  if (IMF_FIXDATE.test(trimmed)) {
    const at = Date.parse(trimmed);
    if (Number.isNaN(at)) {
      return null;
    }
    return clamp(Math.ceil((at - now) / 1000));
  }

  return null;
}
