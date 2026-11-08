import type { CatalogueProduct, CatalogueScheme } from "./types";

/**
 * Display formatting for catalogue values. Amounts are decimal strings from
 * the backend and are formatted as text, never converted to floating point
 * or recalculated here.
 *
 * Nothing here depends on Intl locale or time zone data, which varies
 * between Node and browser builds (a Node build without full time zone
 * data silently ignores timeZone: "Asia/Kolkata"). Output is the same on
 * every machine and in every machine time zone.
 */

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** "12345678" -> "1,23,45,678" (Indian digit grouping). */
function groupIndian(digits: string): string {
  const lastThree = digits.slice(-3);
  const rest = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return rest ? `${rest},${lastThree}` : lastThree;
}

/** "123456.5" -> "₹1,23,456.50" (Indian digit grouping). */
export function formatRupees(amount: string): string {
  const match = DECIMAL.exec(amount.trim());
  if (!match) {
    return `₹${amount}`;
  }
  const [, sign = "", whole = "0", fraction = ""] = match;
  const paise = fraction.length <= 2 ? fraction.padEnd(2, "0") : fraction;
  return `${sign}₹${groupIndian(whole)}.${paise}`;
}

/**
 * A decimal percentage string without trailing zeros: "3.00" -> "3",
 * "2.50" -> "2.5", "0.50" -> "0.5", "10" -> "10". Anything that is not a
 * plain decimal is returned unchanged rather than guessed at.
 */
export function formatPercent(value: string): string {
  const match = DECIMAL.exec(value.trim());
  if (!match) {
    return value;
  }
  const [, sign = "", whole = "0", fraction = ""] = match;
  const significant = fraction.replace(/0+$/, "");
  const integer = whole.replace(/^0+(?=\d)/, "");
  return significant ? `${sign}${integer}.${significant}` : `${sign}${integer}`;
}

/** A whole-number count with Indian digit grouping: 123456 -> "1,23,456". */
export function formatCount(value: number): string {
  if (!Number.isSafeInteger(value)) {
    return String(value);
  }
  const sign = value < 0 ? "-" : "";
  return `${sign}${groupIndian(String(Math.abs(value)))}`;
}

/** "case" -> "cases" for counts other than one. */
export function unitLabel(unitName: string, count: number): string {
  return count === 1 ? unitName : `${unitName}s`;
}

/**
 * One-line description of the scheme rule, as configured in the backend:
 * "Buy 10 cases, get 1 free" or "3% off on 5 or more cases". The unit
 * follows the threshold in both ("1 case", "1 or more case").
 */
export function describeScheme(scheme: CatalogueScheme, unitName: string): string {
  const count = scheme.minimum_quantity;
  const unit = unitLabel(unitName, count);

  if (scheme.scheme_type === "free_goods") {
    return `Buy ${count} ${unit}, get ${scheme.free_quantity} free`;
  }
  return `${formatPercent(scheme.discount_percent)}% off on ${count} or more ${unit}`;
}

/**
 * The scheme's benefit in plain words, from its configured fields only:
 * "For every 10 cases you order, you get 1 case free." or "Get 3% off
 * this product when you order 5 or more cases." Nothing is calculated.
 */
export function schemeBenefit(scheme: CatalogueScheme, unitName: string): string {
  const count = scheme.minimum_quantity;
  if (scheme.scheme_type === "free_goods") {
    const free = scheme.free_quantity;
    return (
      `For every ${count} ${unitLabel(unitName, count)} you order, ` +
      `you get ${free} ${unitLabel(unitName, free)} free.`
    );
  }
  return (
    `Get ${formatPercent(scheme.discount_percent)}% off this product when you ` +
    `order ${count} or more ${unitLabel(unitName, count)}.`
  );
}

/**
 * ISO 8601 date-time with an explicit offset: "Z" or "±HH:MM", optional
 * seconds and fractional seconds. The backend stores scheme dates as
 * timezone-aware values, so it always sends an offset. A timestamp without
 * one would be read in the machine's local time zone, so it is rejected.
 */
const OFFSET_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:(Z)|([+-])(\d{2}):(\d{2}))$/;

const MINUTE_MS = 60 * 1000;

/** India Standard Time is UTC+05:30 all year (no daylight saving). */
const IST_OFFSET_MS = (5 * 60 + 30) * MINUTE_MS;

/** Real-world UTC offsets range from -12:00 to +14:00. */
const MAX_OFFSET_MINUTES = 14 * 60;

function daysInMonth(year: number, month: number): number {
  // Day 0 of the next month is the last day of this one (UTC, leap-aware).
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * The instant (epoch milliseconds, UTC) of an ISO timestamp with an explicit
 * offset, or null when it is malformed: missing offset, impossible date or
 * time (e.g. 30 Feb, 24:00), or an offset outside ±14:00.
 *
 * Parsed field by field instead of with Date.parse, so the result never
 * depends on the JavaScript engine's lenient parsing or the machine's time
 * zone. Fractional seconds are accepted and truncated; they cannot move an
 * instant across a calendar day.
 */
export function parseOffsetTimestamp(timestamp: string): number | null {
  const match = OFFSET_TIMESTAMP.exec(timestamp);
  if (!match) {
    return null;
  }
  const [, y, mo, d, h, mi, sec = "0", zulu, sign, offH = "0", offM = "0"] = match;
  const year = Number(y);
  const month = Number(mo);
  const day = Number(d);
  const hour = Number(h);
  const minute = Number(mi);
  const second = Number(sec);
  const offsetMinutes = zulu ? 0 : Number(offH) * 60 + Number(offM);

  if (
    year < 1000 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth(year, month) ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    Number(offM) > 59 ||
    offsetMinutes > MAX_OFFSET_MINUTES
  ) {
    return null;
  }

  // Local wall-clock time minus its offset is UTC: 10:00+05:30 is 04:30Z.
  const local = Date.UTC(year, month - 1, day, hour, minute, second);
  const direction = sign === "-" ? -1 : 1;
  return local - direction * offsetMinutes * MINUTE_MS;
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/**
 * The calendar date of `timestamp` in India (Asia/Kolkata), e.g.
 * "2026-12-31T18:30:00Z" -> "1 Jan 2027" but "2026-12-31T18:00:00Z" ->
 * "31 Dec 2026" (23:30 in India). Null for a malformed timestamp or one
 * without an explicit offset.
 */
export function formatIndiaDate(timestamp: string): string | null {
  const instant = parseOffsetTimestamp(timestamp);
  if (instant === null) {
    return null;
  }
  // Shift to IST, then read the UTC fields: no machine time zone involved.
  const india = new Date(instant + IST_OFFSET_MS);
  return `${india.getUTCDate()} ${MONTHS[india.getUTCMonth()]} ${india.getUTCFullYear()}`;
}

/**
 * The date and time of `timestamp` in India (Asia/Kolkata), e.g.
 * "2026-11-07T10:00:00Z" -> "7 Nov 2026, 3:30 pm IST". Seconds are not
 * shown. Null for a malformed timestamp or one without an explicit offset.
 */
export function formatIndiaDateTime(timestamp: string): string | null {
  const instant = parseOffsetTimestamp(timestamp);
  if (instant === null) {
    return null;
  }
  const india = new Date(instant + IST_OFFSET_MS);
  const hours = india.getUTCHours();
  const hour12 = hours % 12 === 0 ? 12 : hours % 12;
  const minutes = String(india.getUTCMinutes()).padStart(2, "0");
  const period = hours < 12 ? "am" : "pm";
  const date = `${india.getUTCDate()} ${MONTHS[india.getUTCMonth()]} ${india.getUTCFullYear()}`;
  return `${date}, ${hour12}:${minutes} ${period} IST`;
}

/** "Valid until 31 Dec 2026", or null when there is no (valid) end date. */
export function schemeValidity(scheme: CatalogueScheme): string | null {
  if (!scheme.ends_at) {
    return null;
  }
  const end = formatIndiaDate(scheme.ends_at);
  return end ? `Valid until ${end}` : null;
}

/** "96 units per case". */
export function packSize(product: CatalogueProduct): string {
  return `${formatCount(product.units_per_case)} units per ${product.unit_name}`;
}
