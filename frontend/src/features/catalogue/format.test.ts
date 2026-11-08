import { describe, expect, it } from "vitest";

import {
  describeScheme,
  formatCount,
  formatIndiaDate,
  formatIndiaDateTime,
  formatPercent,
  formatRupees,
  parseOffsetTimestamp,
  schemeBenefit,
  schemeValidity,
} from "./format";
import type { CatalogueScheme } from "./types";

describe("formatRupees", () => {
  it.each([
    ["777.60", "₹777.60"],
    ["7776.00", "₹7,776.00"],
    ["123456.5", "₹1,23,456.50"],
    ["12345678.00", "₹1,23,45,678.00"],
    ["0", "₹0.00"],
    ["-50.00", "-₹50.00"],
  ])("formats %s as %s without floating point", (amount, expected) => {
    expect(formatRupees(amount)).toBe(expected);
  });

  it("keeps more precise amounts exactly as sent", () => {
    expect(formatRupees("10.125")).toBe("₹10.125");
  });
});

describe("formatPercent", () => {
  it.each([
    ["3.00", "3"],
    ["2.50", "2.5"],
    ["10", "10"],
    ["10.00", "10"],
    ["0.50", "0.5"],
    ["12.25", "12.25"],
    ["100", "100"],
  ])("formats %s as %s", (value, expected) => {
    expect(formatPercent(value)).toBe(expected);
  });
});

const baseScheme: CatalogueScheme = {
  id: "s1",
  name: "Scheme",
  scheme_type: "free_goods",
  minimum_quantity: 10,
  free_quantity: 1,
  discount_percent: "0.00",
  starts_at: "2026-01-01T00:00:00Z",
  ends_at: null,
  reference: null,
};

describe("schemeBenefit", () => {
  it("explains free goods in plain words", () => {
    expect(schemeBenefit(baseScheme, "case")).toBe(
      "For every 10 cases you order, you get 1 case free.",
    );
  });

  it("explains a slab discount in plain words", () => {
    const slab: CatalogueScheme = {
      ...baseScheme,
      scheme_type: "slab_discount",
      minimum_quantity: 5,
      free_quantity: 0,
      discount_percent: "3.00",
    };
    expect(schemeBenefit(slab, "case")).toBe(
      "Get 3% off this product when you order 5 or more cases.",
    );
  });
});

describe("describeScheme", () => {
  it("describes free goods from the configured quantities", () => {
    expect(describeScheme(baseScheme, "case")).toBe("Buy 10 cases, get 1 free");
  });

  it("describes a slab discount from the configured percentage", () => {
    const slab: CatalogueScheme = {
      ...baseScheme,
      scheme_type: "slab_discount",
      minimum_quantity: 5,
      free_quantity: 0,
      discount_percent: "3.00",
    };
    expect(describeScheme(slab, "case")).toBe("3% off on 5 or more cases");
  });
});

describe("formatCount", () => {
  it.each([
    [0, "0"],
    [340, "340"],
    [1000, "1,000"],
    [123456, "1,23,456"],
  ])("formats %i as %s", (value, expected) => {
    expect(formatCount(value)).toBe(expected);
  });
});

describe("describeScheme wording", () => {
  it("uses the singular unit for a quantity of one", () => {
    expect(describeScheme({ ...baseScheme, minimum_quantity: 1 }, "case")).toBe(
      "Buy 1 case, get 1 free",
    );
  });

  it("drops trailing zeros from a fractional slab percentage", () => {
    const slab: CatalogueScheme = {
      ...baseScheme,
      scheme_type: "slab_discount",
      minimum_quantity: 20,
      free_quantity: 0,
      discount_percent: "2.50",
    };
    expect(describeScheme(slab, "case")).toBe("2.5% off on 20 or more cases");
  });

  it("uses the singular unit for a slab threshold of one", () => {
    const slab: CatalogueScheme = {
      ...baseScheme,
      scheme_type: "slab_discount",
      minimum_quantity: 1,
      free_quantity: 0,
      discount_percent: "3.00",
    };
    expect(describeScheme(slab, "case")).toBe("3% off on 1 or more case");
  });
});

describe("formatIndiaDate", () => {
  it.each([
    // IST is UTC+05:30, so India's 1 Jan starts at 2026-12-31T18:30:00Z.
    ["2026-12-31T18:29:59Z", "31 Dec 2026"],
    ["2026-12-31T18:29:59.999Z", "31 Dec 2026"],
    ["2026-12-31T18:30:00Z", "1 Jan 2027"],
    // 18:00 UTC is 23:30 in India: still 31 December (regression guard).
    ["2026-12-31T18:00:00Z", "31 Dec 2026"],
    ["2026-12-31T18:00:00+00:00", "31 Dec 2026"],
    ["2026-12-31T18:30:00+00:00", "1 Jan 2027"],
    // Positive offsets.
    ["2027-01-01T00:00:00+05:30", "1 Jan 2027"],
    ["2026-12-31T23:59:59+05:30", "31 Dec 2026"],
    ["2027-01-01T00:10:00+05:45", "31 Dec 2026"], // = 18:25Z
    ["2027-01-01T03:30:00+09:00", "1 Jan 2027"], // = 18:30Z
    ["2027-01-01T08:00:00+14:00", "31 Dec 2026"], // = 18:00Z
    // Negative offsets.
    ["2026-12-31T13:00:00-05:00", "31 Dec 2026"], // = 18:00Z
    ["2026-12-31T13:30:00-05:00", "1 Jan 2027"], // = 18:30Z
    ["2026-12-31T06:30:00-12:00", "1 Jan 2027"], // = 18:30Z
    // Seconds and fractions are optional; fractions of any length.
    ["2026-06-15T10:00Z", "15 Jun 2026"],
    ["2026-06-15T10:00:00.123456Z", "15 Jun 2026"],
    // Leap years.
    ["2028-02-28T20:00:00Z", "29 Feb 2028"],
    ["2027-02-28T20:00:00Z", "1 Mar 2027"],
    ["2028-02-29T00:00:00+05:30", "29 Feb 2028"],
  ])("formats %s as %s in India time", (timestamp, expected) => {
    expect(formatIndiaDate(timestamp)).toBe(expected);
  });

  it.each([
    // No offset: would depend on the machine's time zone.
    "2026-12-31T18:00:00",
    "2026-12-31",
    // Impossible dates and times.
    "2027-02-29T00:00:00Z",
    "2026-02-30T00:00:00Z",
    "2026-13-01T00:00:00Z",
    "2026-00-10T00:00:00Z",
    "2026-12-31T24:00:00Z",
    "2026-12-31T18:60:00Z",
    "2026-12-31T18:00:60Z",
    // Malformed or out-of-range offsets.
    "2026-12-31T18:00:00+0530",
    "2026-12-31T18:00:00+05",
    "2026-12-31T18:00:00+05:60",
    "2026-12-31T18:00:00+14:30",
    "2026-12-31T18:00:00z",
    // Other junk.
    "2026-12-31 18:00:00Z",
    " 2026-12-31T18:00:00Z",
    "not a date",
    "",
  ])("returns null for %j", (timestamp) => {
    expect(formatIndiaDate(timestamp)).toBeNull();
  });
});

describe("formatIndiaDateTime", () => {
  it.each([
    // IST is UTC+05:30.
    ["2026-11-07T10:00:00Z", "7 Nov 2026, 3:30 pm IST"],
    ["2026-11-07T10:00:00+00:00", "7 Nov 2026, 3:30 pm IST"],
    ["2026-11-07T10:00:00.123456+00:00", "7 Nov 2026, 3:30 pm IST"],
    ["2026-11-07T15:30:00+05:30", "7 Nov 2026, 3:30 pm IST"],
    ["2026-11-07T05:00:00-05:00", "7 Nov 2026, 3:30 pm IST"],
    // Minutes are zero-padded; seconds are not shown.
    ["2026-11-07T04:05:59+05:30", "7 Nov 2026, 4:05 am IST"],
    // Midnight and noon in India.
    ["2026-12-31T18:30:00Z", "1 Jan 2027, 12:00 am IST"],
    ["2026-11-07T06:30:00Z", "7 Nov 2026, 12:00 pm IST"],
    // The last minute of the India day stays on that date.
    ["2026-12-31T18:29:59Z", "31 Dec 2026, 11:59 pm IST"],
  ])("formats %s as %j", (timestamp, expected) => {
    expect(formatIndiaDateTime(timestamp)).toBe(expected);
  });

  it.each([
    "2026-11-07T10:00:00", // no offset: the instant is unknown
    "2026-02-30T10:00:00Z",
    "2026-11-07T24:00:00Z",
    "2026-11-07T10:00:00+05:60",
    "not a date",
    "",
  ])("returns null for %j", (timestamp) => {
    expect(formatIndiaDateTime(timestamp)).toBeNull();
  });
});

describe("parseOffsetTimestamp", () => {
  it("returns the same instant for equivalent offsets", () => {
    const utc = parseOffsetTimestamp("2026-12-31T18:30:00Z");
    expect(utc).toBe(Date.UTC(2026, 11, 31, 18, 30));
    expect(parseOffsetTimestamp("2027-01-01T00:00:00+05:30")).toBe(utc);
    expect(parseOffsetTimestamp("2026-12-31T13:30:00-05:00")).toBe(utc);
    expect(parseOffsetTimestamp("2026-12-31T18:30:00-00:00")).toBe(utc);
  });
});

describe("schemeValidity", () => {
  it("is null for an open-ended scheme", () => {
    expect(schemeValidity(baseScheme)).toBeNull();
  });

  it("omits the end date rather than guess when it has no offset", () => {
    expect(schemeValidity({ ...baseScheme, ends_at: "2026-12-31T18:00:00" })).toBeNull();
  });

  it("shows the end date in India time when it crosses midnight there", () => {
    // 19:00 UTC on 31 Dec is 00:30 on 1 Jan in India.
    expect(schemeValidity({ ...baseScheme, ends_at: "2026-12-31T19:00:00Z" })).toBe(
      "Valid until 1 Jan 2027",
    );
  });

  it("keeps the India date when UTC is still the same evening", () => {
    // 18:00 UTC on 31 Dec is 23:30 on 31 Dec in India.
    expect(schemeValidity({ ...baseScheme, ends_at: "2026-12-31T18:00:00Z" })).toBe(
      "Valid until 31 Dec 2026",
    );
  });

  it("omits a malformed end date", () => {
    expect(schemeValidity({ ...baseScheme, ends_at: "2026-02-30T00:00:00Z" })).toBeNull();
  });
});
