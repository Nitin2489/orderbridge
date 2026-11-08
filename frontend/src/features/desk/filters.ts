/**
 * Queue date filters. The desk picks calendar dates in India; the backend
 * wants timezone-aware timestamps with created_from inclusive and
 * created_to exclusive. "From 5 Nov to 7 Nov" therefore becomes
 * [5 Nov 00:00 IST, 8 Nov 00:00 IST).
 */

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const IST_OFFSET = "+05:30";

function parseDate(value: string): { year: number; month: number; day: number } | null {
  const match = DATE.exec(value);
  if (!match) {
    return null;
  }
  const [year, month, day] = [match[1], match[2], match[3]].map(Number) as [
    number,
    number,
    number,
  ];
  const check = new Date(Date.UTC(year, month - 1, day));
  if (
    check.getUTCFullYear() !== year ||
    check.getUTCMonth() !== month - 1 ||
    check.getUTCDate() !== day
  ) {
    return null;
  }
  return { year, month, day };
}

function pad(value: number, width = 2): string {
  return value.toString().padStart(width, "0");
}

/** Start of a calendar day in India, as an ISO timestamp with offset. */
export function startOfIndiaDay(date: string): string | null {
  return parseDate(date) ? `${date}T00:00:00${IST_OFFSET}` : null;
}

/** Start of the day after `date` in India. */
export function startOfNextIndiaDay(date: string): string | null {
  const parsed = parseDate(date);
  if (!parsed) {
    return null;
  }
  const next = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day + 1));
  return (
    `${pad(next.getUTCFullYear(), 4)}-${pad(next.getUTCMonth() + 1)}-${pad(next.getUTCDate())}` +
    `T00:00:00${IST_OFFSET}`
  );
}

export interface DateRange {
  createdFrom: string | null;
  createdTo: string | null;
  /** Set when the dates cannot be used; the filter is then not applied. */
  error: string | null;
}

/** The backend range for inclusive "from" and "to" dates (either may be empty). */
export function dateRange(from: string, to: string): DateRange {
  const createdFrom = from ? startOfIndiaDay(from) : null;
  const createdTo = to ? startOfNextIndiaDay(to) : null;
  if ((from && !createdFrom) || (to && !createdTo)) {
    return { createdFrom: null, createdTo: null, error: "Enter valid dates." };
  }
  if (from && to && from > to) {
    return {
      createdFrom: null,
      createdTo: null,
      error: "The start date must not be after the end date.",
    };
  }
  return { createdFrom, createdTo, error: null };
}
