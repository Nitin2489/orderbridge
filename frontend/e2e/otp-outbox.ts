/**
 * Reads one-time codes from the backend's development OTP outbox.
 *
 * Node only: imported by Playwright spec files, which run in the test
 * runner process. Nothing here is bundled into the app or reachable from
 * the browser, and the outbox path never leaves this process.
 *
 * The backend (APP_ENV=development, AUTH_DEBUG_OTP=true,
 * AUTH_DEV_OTP_OUTBOX_PATH set) appends one JSON line per issued code:
 *   {"phone_number":"+919000000001","code":"123456","issued_at":"<UTC ISO>"}
 * Point E2E_OTP_OUTBOX at the same file (an absolute path, or one relative
 * to the frontend directory, from which Playwright runs).
 *
 * Errors never include a code, and phone numbers in them are masked.
 */

import { readFile } from "node:fs/promises";
import path from "node:path";

export const OUTBOX_ENV = "E2E_OTP_OUTBOX";

/** The backend's AUTH_OTP_TTL_SECONDS default: older codes have expired. */
export const OTP_TTL_SECONDS = 300;

/**
 * A record's time is when the code was delivered, a moment after the backend
 * created (and started the expiry of) the challenge. Codes this close to
 * expiry are treated as expired, so a test never types one that the backend
 * has just stopped accepting.
 */
export const EXPIRY_SAFETY_MARGIN_SECONDS = 5;

const DEFAULT_TIMEOUT_MS = 5_000;
const DEFAULT_POLL_MS = 100;
const MAX_TIMEOUT_MS = 30_000;

const SEPARATORS = /[\s\-().]/g;
const INDIAN_MOBILE = /^[6-9][0-9]{9}$/;
const CODE = /^[0-9]{6}$/;
const OFFSET_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:(Z)|([+-])(\d{2}):(\d{2}))$/;

/** Real-world UTC offsets range from -12:00 to +14:00. */
const MAX_OFFSET_MINUTES = 14 * 60;

export interface OutboxRecord {
  phoneNumber: string;
  code: string;
  issuedAt: Date;
}

/**
 * Only the variable this module reads. Narrower than NodeJS.ProcessEnv (which
 * Next.js augments with a required NODE_ENV), so tests can pass a plain
 * object; process.env still satisfies it.
 */
type OutboxEnv = Readonly<Record<string, string | undefined>>;

/** The outbox path from E2E_OTP_OUTBOX, or null when it is not configured. */
export function outboxPathFromEnv(env: OutboxEnv = process.env): string | null {
  const value = env[OUTBOX_ENV]?.trim();
  return value ? path.resolve(value) : null;
}

/**
 * +91XXXXXXXXXX, mirroring the backend's normalisation, or null. Accepts
 * "9000000001", "+91 90000 00001", "09000000001" and similar.
 */
export function normalizePhoneNumber(raw: string): string | null {
  const compact = raw.trim().replace(SEPARATORS, "");
  let national = compact;
  if (compact.startsWith("+91")) {
    national = compact.slice(3);
  } else if (compact.startsWith("91") && compact.length === 12) {
    national = compact.slice(2);
  } else if (compact.startsWith("0") && compact.length === 11) {
    national = compact.slice(1);
  }
  return INDIAN_MOBILE.test(national) ? `+91${national}` : null;
}

/** "+919000000001" -> "+91******0001", for messages. */
export function maskPhoneNumber(phoneNumber: string): string {
  return phoneNumber.length > 7
    ? `${phoneNumber.slice(0, 3)}${"*".repeat(phoneNumber.length - 7)}${phoneNumber.slice(-4)}`
    : "***";
}

/**
 * An ISO 8601 timestamp with an explicit offset (Z or ±HH:MM) as a Date, or
 * null when it is malformed or names an impossible date, time or offset.
 *
 * Fractional seconds are normalised to exactly three digits (milliseconds):
 * the backend writes microseconds ("…:15.123456+00:00"). The date is then
 * built from validated fields with Date.UTC, so nothing depends on how
 * leniently the JavaScript engine parses date strings.
 */
export function parseIssuedAt(value: string): Date | null {
  const match = OFFSET_TIMESTAMP.exec(value);
  if (!match) {
    return null;
  }
  const [, y, mo, d, h, mi, sec, fraction = "", zulu, sign, offH = "0", offM = "0"] = match;
  const [year, month, day, hour, minute, second] = [y, mo, d, h, mi, sec].map(Number) as [
    number,
    number,
    number,
    number,
    number,
    number,
  ];
  const milliseconds = Number(fraction.slice(0, 3).padEnd(3, "0"));
  const offsetMinutes = zulu ? 0 : Number(offH) * 60 + Number(offM);

  if (
    year < 1000 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    // Day 0 of the next month is the last day of this one.
    day > new Date(Date.UTC(year, month, 0)).getUTCDate() ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    Number(offM) > 59 ||
    offsetMinutes > MAX_OFFSET_MINUTES
  ) {
    return null;
  }

  const local = Date.UTC(year, month - 1, day, hour, minute, second, milliseconds);
  // Local wall-clock time minus its offset is UTC: 16:00+05:30 is 10:30Z.
  const direction = sign === "-" ? -1 : 1;
  return new Date(local - direction * offsetMinutes * 60_000);
}

function toRecord(value: unknown): OutboxRecord | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { phone_number: phone, code, issued_at: issuedAt } = value as Record<string, unknown>;
  if (typeof phone !== "string" || typeof code !== "string" || typeof issuedAt !== "string") {
    return null;
  }
  const phoneNumber = normalizePhoneNumber(phone);
  const issued = parseIssuedAt(issuedAt);
  if (!phoneNumber || !CODE.test(code) || !issued) {
    return null;
  }
  return { phoneNumber, code, issuedAt: issued };
}

/**
 * The complete, well-formed records in an outbox file's text.
 *
 * A final line without its newline is ignored: it may still be being
 * written. Lines that are not valid records are skipped.
 */
export function parseOutbox(text: string): OutboxRecord[] {
  const lines = text.split("\n");
  // The element after the last "\n" is "" for a complete file, or a partial line.
  lines.pop();

  const records: OutboxRecord[] = [];
  for (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    try {
      const record = toRecord(JSON.parse(line));
      if (record) {
        records.push(record);
      }
    } catch {
      // Not JSON: skip it.
    }
  }
  return records;
}

/**
 * The newest code for `phoneNumber` that has not expired at `now`, or null.
 * "Not expired" leaves EXPIRY_SAFETY_MARGIN_SECONDS of the lifetime unused.
 *
 * Requesting a code inside the backend's 60-second resend cooldown issues
 * nothing new and keeps the previous code valid, so the newest unexpired
 * record is the right one in both cases.
 */
export function newestValidCode(
  records: readonly OutboxRecord[],
  phoneNumber: string,
  { now = new Date(), ttlSeconds = OTP_TTL_SECONDS }: { now?: Date; ttlSeconds?: number } = {},
): string | null {
  const target = normalizePhoneNumber(phoneNumber);
  if (!target) {
    return null;
  }
  // Valid while younger than the lifetime minus the safety margin.
  const oldest = now.getTime() - (ttlSeconds - EXPIRY_SAFETY_MARGIN_SECONDS) * 1000;
  let newest: OutboxRecord | null = null;
  for (const record of records) {
    if (
      record.phoneNumber === target &&
      record.issuedAt.getTime() > oldest &&
      (!newest || record.issuedAt >= newest.issuedAt)
    ) {
      newest = record;
    }
  }
  return newest?.code ?? null;
}

export class OutboxError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OutboxError";
  }
}

export interface ReadOtpOptions {
  /** Defaults to E2E_OTP_OUTBOX. */
  outboxPath?: string | null;
  /** How long to wait for a record; at most 30 seconds. Default 5 seconds. */
  timeoutMs?: number;
  pollIntervalMs?: number;
  now?: () => Date;
}

async function readText(outboxPath: string): Promise<string> {
  try {
    return await readFile(outboxPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return ""; // Not created yet: no code issued since the backend started.
    }
    const code = (error as NodeJS.ErrnoException).code ?? "unknown error";
    throw new OutboxError(`Could not read the OTP outbox (${code}).`);
  }
}

/**
 * Wait (bounded) for the newest valid code for `phoneNumber` in the outbox.
 *
 * Call it after the sign-in page has reached the code step: the backend
 * writes the record before it answers the request.
 */
export async function readOtp(phoneNumber: string, options: ReadOtpOptions = {}): Promise<string> {
  const target = normalizePhoneNumber(phoneNumber);
  if (!target) {
    throw new OutboxError("Not a valid Indian mobile number.");
  }
  const outboxPath = options.outboxPath === undefined ? outboxPathFromEnv() : options.outboxPath;
  if (!outboxPath) {
    throw new OutboxError(`${OUTBOX_ENV} is not set.`);
  }

  const timeoutMs = Math.min(Math.max(options.timeoutMs ?? DEFAULT_TIMEOUT_MS, 0), MAX_TIMEOUT_MS);
  const pollIntervalMs = Math.max(options.pollIntervalMs ?? DEFAULT_POLL_MS, 10);
  const now = options.now ?? (() => new Date());
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    const code = newestValidCode(parseOutbox(await readText(outboxPath)), target, {
      now: now(),
    });
    if (code) {
      return code;
    }
    if (Date.now() >= deadline) {
      throw new OutboxError(
        `No unexpired OTP for ${maskPhoneNumber(target)} reached the outbox within ` +
          `${timeoutMs} ms. Check that the backend runs with APP_ENV=development, ` +
          `AUTH_DEBUG_OTP=true and AUTH_DEV_OTP_OUTBOX_PATH pointing at ${OUTBOX_ENV}, ` +
          "and see the backend terminal for outbox errors.",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
}
