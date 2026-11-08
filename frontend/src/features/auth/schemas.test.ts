import { describe, expect, it } from "vitest";

import {
  formatPhoneNumber,
  normalizePhoneNumber,
  otpCodeSchema,
  PHONE_INVALID_MESSAGE,
  PHONE_REQUIRED_MESSAGE,
  phoneFormSchema,
} from "./schemas";

describe("normalizePhoneNumber", () => {
  it.each([
    ["9000000001", "+919000000001"],
    ["+919000000001", "+919000000001"],
    ["919000000001", "+919000000001"],
    ["09000000001", "+919000000001"],
    ["+91 90000 00001", "+919000000001"],
    ["(900) 000-0001", "+919000000001"],
    ["  6123456789  ", "+916123456789"],
  ])("accepts %j as %s", (raw, expected) => {
    expect(normalizePhoneNumber(raw)).toBe(expected);
  });

  it.each([
    "",
    "12345",
    "5000000001", // Indian mobile numbers start with 6-9
    "90000000012", // 11 digits without a leading 0
    "+9190000000", // too short after +91
    "+449000000001", // other country code
    "90000abc01",
  ])("rejects %j", (raw) => {
    expect(normalizePhoneNumber(raw)).toBeNull();
  });
});

describe("phoneFormSchema", () => {
  it("outputs the normalised number", () => {
    expect(phoneFormSchema.parse({ phone: "90000 00001" })).toEqual({
      phone: "+919000000001",
    });
  });

  it("asks for a number when empty", () => {
    const result = phoneFormSchema.safeParse({ phone: "   " });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.message).toBe(PHONE_REQUIRED_MESSAGE);
  });

  it("explains an invalid number", () => {
    const result = phoneFormSchema.safeParse({ phone: "12345" });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.message).toBe(PHONE_INVALID_MESSAGE);
  });
});

describe("otpCodeSchema", () => {
  it("accepts exactly six digits", () => {
    expect(otpCodeSchema.safeParse("123456").success).toBe(true);
  });

  it.each(["12345", "1234567", "12345a", ""])("rejects %j", (code) => {
    expect(otpCodeSchema.safeParse(code).success).toBe(false);
  });
});

describe("formatPhoneNumber", () => {
  it("groups the national number for display", () => {
    expect(formatPhoneNumber("+919000000001")).toBe("+91 90000 00001");
  });
});
