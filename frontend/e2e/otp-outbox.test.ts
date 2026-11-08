// @vitest-environment node
/**
 * Unit tests for the Node-only OTP outbox reader (run by Vitest, not
 * Playwright: playwright.config.ts only collects *.spec.ts).
 */
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  EXPIRY_SAFETY_MARGIN_SECONDS,
  maskPhoneNumber,
  newestValidCode,
  normalizePhoneNumber,
  OTP_TTL_SECONDS,
  OutboxError,
  outboxPathFromEnv,
  parseIssuedAt,
  parseOutbox,
  readOtp,
} from "./otp-outbox";

const NOW = new Date("2026-11-07T10:00:00Z");

function line(phone: string, code: string, issuedAt: string): string {
  return `${JSON.stringify({ phone_number: phone, code, issued_at: issuedAt })}\n`;
}

describe("outboxPathFromEnv", () => {
  it("is null when E2E_OTP_OUTBOX is unset or blank", () => {
    expect(outboxPathFromEnv({})).toBeNull();
    expect(outboxPathFromEnv({ E2E_OTP_OUTBOX: "   " })).toBeNull();
  });

  it("resolves the configured path", () => {
    expect(outboxPathFromEnv({ E2E_OTP_OUTBOX: "/tmp/outbox.jsonl" })).toBe("/tmp/outbox.jsonl");
    expect(outboxPathFromEnv({ E2E_OTP_OUTBOX: "../.dev-otp-outbox.jsonl" })).toBe(
      path.resolve("../.dev-otp-outbox.jsonl"),
    );
  });
});

describe("normalizePhoneNumber", () => {
  it.each(["9000000001", "+919000000001", "+91 90000 00001", "919000000001", "09000000001"])(
    "normalises %j",
    (raw) => {
      expect(normalizePhoneNumber(raw)).toBe("+919000000001");
    },
  );

  it.each(["", "12345", "5000000001", "+449000000001"])("rejects %j", (raw) => {
    expect(normalizePhoneNumber(raw)).toBeNull();
  });

  it("masks numbers for messages", () => {
    expect(maskPhoneNumber("+919000000001")).toBe("+91******0001");
  });
});

describe("parseIssuedAt", () => {
  it.each([
    // Microseconds, as the backend writes them, become milliseconds.
    ["2026-11-07T10:30:15.123456+00:00", "2026-11-07T10:30:15.123Z"],
    ["2026-11-07T10:30:15.999999+00:00", "2026-11-07T10:30:15.999Z"],
    ["2026-11-07T10:30:15.123+00:00", "2026-11-07T10:30:15.123Z"],
    ["2026-11-07T10:30:15.5Z", "2026-11-07T10:30:15.500Z"],
    // No fractional seconds (the backend omits them when they are zero).
    ["2026-11-07T10:30:15+00:00", "2026-11-07T10:30:15.000Z"],
    ["2026-11-07T16:00:15+05:30", "2026-11-07T10:30:15.000Z"],
    ["2026-11-07T10:30:15Z", "2026-11-07T10:30:15.000Z"],
    ["2026-11-07T05:30:15-05:00", "2026-11-07T10:30:15.000Z"],
    ["2028-02-29T00:00:00+00:00", "2028-02-29T00:00:00.000Z"],
  ])("parses %s", (value, expected) => {
    expect(parseIssuedAt(value)?.toISOString()).toBe(expected);
  });

  it.each([
    "2026-11-07T10:30:15", // no offset
    "2026-11-07T10:30:15.123456", // no offset
    "2026-11-07T10:30:15+0000", // offset without colon
    "2026-11-07T10:30:15 +00:00",
    "2026-11-07T10:30:15.+00:00", // empty fraction
    "2026-11-07T10:30:15+25:00", // impossible offset
    "2026-11-07T10:30:15+05:60", // impossible offset minutes
    "2026-13-07T10:30:15+00:00", // impossible month
    "2026-02-30T10:30:15+00:00", // impossible day
    "2026-11-07T24:30:15+00:00", // impossible hour
    "2026-11-07T10:60:15+00:00", // impossible minute
    "2026-11-07",
    "not a timestamp",
    "",
  ])("rejects %j", (value) => {
    expect(parseIssuedAt(value)).toBeNull();
  });
});

describe("parseOutbox", () => {
  it("ignores an incomplete final line", () => {
    const text =
      line("+919000000001", "111111", "2026-11-07T09:59:00+00:00") +
      '{"phone_number":"+919000000001","code":"2222';

    expect(parseOutbox(text).map((record) => record.code)).toEqual(["111111"]);
  });

  it("skips malformed and invalid records", () => {
    const text =
      "not json\n" +
      "[1,2,3]\n" +
      line("+919000000001", "12345", "2026-11-07T09:59:00+00:00") + // 5 digits
      line("12345", "123456", "2026-11-07T09:59:00+00:00") + // bad number
      line("+919000000001", "123456", "2026-11-07T09:59:00") + // no offset
      line("+919000000001", "654321", "2026-11-07T09:59:00.123456+00:00") +
      "\n";

    const records = parseOutbox(text);

    expect(records.map((record) => record.code)).toEqual(["654321"]);
    expect(records[0]?.phoneNumber).toBe("+919000000001");
  });

  it("is empty for an empty file", () => {
    expect(parseOutbox("")).toEqual([]);
  });
});

describe("newestValidCode", () => {
  const records = parseOutbox(
    line("+919000000001", "111111", "2026-11-07T09:54:00+00:00") + // expired (6 min)
      line("+919000000001", "222222", "2026-11-07T09:58:00+00:00") +
      line("+919000000002", "999999", "2026-11-07T09:59:30+00:00") +
      line("+919000000001", "333333", "2026-11-07T09:59:00+00:00"),
  );

  it("picks the newest unexpired code for the number", () => {
    expect(newestValidCode(records, "90000 00001", { now: NOW })).toBe("333333");
    expect(newestValidCode(records, "+919000000002", { now: NOW })).toBe("999999");
  });

  it("treats codes within the safety margin of expiry as expired", () => {
    const issued = "2026-11-07T10:00:00+00:00";
    const usable = (OTP_TTL_SECONDS - EXPIRY_SAFETY_MARGIN_SECONDS) * 1000; // 295 s
    const only = parseOutbox(line("+919000000001", "555555", issued));
    const at = (ms: number) => new Date(Date.parse(issued) + ms);

    expect(EXPIRY_SAFETY_MARGIN_SECONDS).toBe(5);
    expect(newestValidCode(only, "9000000001", { now: at(usable - 1) })).toBe("555555");
    expect(newestValidCode(only, "9000000001", { now: at(usable) })).toBeNull();
    // Still inside the backend's 300 s lifetime, but too close to its end.
    expect(newestValidCode(only, "9000000001", { now: at(usable + 2_000) })).toBeNull();
  });

  it("ignores expired codes", () => {
    const later = new Date("2026-11-07T10:04:30Z"); // 333333 is 5.5 minutes old
    expect(newestValidCode(records, "9000000001", { now: later })).toBeNull();
  });

  it("returns null for other or invalid numbers", () => {
    expect(newestValidCode(records, "9000000003", { now: NOW })).toBeNull();
    expect(newestValidCode(records, "12345", { now: NOW })).toBeNull();
  });
});

describe("readOtp", () => {
  let directory: string;
  let outbox: string;

  beforeEach(async () => {
    directory = await mkdtemp(path.join(tmpdir(), "otp-outbox-"));
    outbox = path.join(directory, "outbox.jsonl");
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  it("reads the newest valid code from the file", async () => {
    await writeFile(
      outbox,
      line("+919000000001", "222222", "2026-11-07T09:58:00+00:00") +
        line("+919000000001", "333333", "2026-11-07T09:59:00+00:00"),
    );

    await expect(readOtp("9000000001", { outboxPath: outbox, now: () => NOW })).resolves.toBe(
      "333333",
    );
  });

  it("waits for a record that arrives while polling", async () => {
    setTimeout(() => {
      void writeFile(outbox, line("+919000000001", "444444", "2026-11-07T09:59:59+00:00"));
    }, 150);

    await expect(
      readOtp("9000000001", {
        outboxPath: outbox,
        now: () => NOW,
        timeoutMs: 2_000,
        pollIntervalMs: 20,
      }),
    ).resolves.toBe("444444");
  });

  it("times out with a message that names no code and masks the number", async () => {
    await writeFile(outbox, line("+919000000002", "987654", "2026-11-07T09:59:00+00:00"));

    const failure = await readOtp("9000000001", {
      outboxPath: outbox,
      now: () => NOW,
      timeoutMs: 100,
      pollIntervalMs: 20,
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(OutboxError);
    const message = (failure as Error).message;
    expect(message).toContain("+91******0001");
    expect(message).not.toContain("9000000001");
    expect(message).not.toContain("987654");
    expect(message).not.toContain(outbox);
  });

  it("treats a missing file as no code yet", async () => {
    await expect(
      readOtp("9000000001", { outboxPath: outbox, now: () => NOW, timeoutMs: 50 }),
    ).rejects.toBeInstanceOf(OutboxError);
  });

  it("refuses to run without a configured outbox", async () => {
    await expect(readOtp("9000000001", { outboxPath: null })).rejects.toThrow(
      "E2E_OTP_OUTBOX is not set.",
    );
  });
});
