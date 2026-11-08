import { describe, expect, it } from "vitest";

import { lineAmount, paiseToAmount, perUnitPrice, sumAmounts, toPaise } from "./money";

describe("toPaise", () => {
  it.each([
    ["777.60", 77760],
    ["777.6", 77760],
    ["2030", 203000],
    ["0.05", 5],
    ["0", 0],
  ])("reads %j", (amount, paise) => {
    expect(toPaise(amount)).toBe(paise);
  });

  it.each(["", "-1.00", "1.005", "1,000.00", "₹10", "abc", "1e3"])("refuses %j", (amount) => {
    expect(toPaise(amount)).toBeNull();
  });
});

describe("paiseToAmount", () => {
  it("formats with two decimals", () => {
    expect(paiseToAmount(77760)).toBe("777.60");
    expect(paiseToAmount(5)).toBe("0.05");
    expect(paiseToAmount(0)).toBe("0.00");
  });

  it("refuses negative or fractional paise", () => {
    expect(() => paiseToAmount(-1)).toThrow(RangeError);
    expect(() => paiseToAmount(1.5)).toThrow(RangeError);
  });
});

describe("lineAmount", () => {
  it("multiplies exactly, with no floating-point drift", () => {
    expect(lineAmount("777.60", 5)).toBe("3888.00");
    expect(lineAmount("2030.00", 2)).toBe("4060.00");
    expect(lineAmount("0.10", 3)).toBe("0.30"); // 0.1 * 3 in floats is 0.30000000000000004
  });

  it("is null for unusable input", () => {
    expect(lineAmount("abc", 2)).toBeNull();
    expect(lineAmount("10.00", -1)).toBeNull();
    expect(lineAmount("10.00", 1.5)).toBeNull();
  });
});

describe("sumAmounts", () => {
  it("adds exactly", () => {
    expect(sumAmounts(["3888.00", "4060.00"])).toBe("7948.00");
    expect(sumAmounts(["0.10", "0.20"])).toBe("0.30");
    expect(sumAmounts([])).toBe("0.00");
  });

  it("is null when any amount is unusable", () => {
    expect(sumAmounts(["1.00", null])).toBeNull();
    expect(sumAmounts(["1.00", "x"])).toBeNull();
  });
});

describe("perUnitPrice", () => {
  it.each([
    // The three client products: case price and units per case.
    ["777.60", 96, "8.10"],
    ["2030.00", 20, "101.50"],
    ["4720.00", 40, "118.00"],
  ])("%s for %i units is %s each", (casePrice, units, unit) => {
    expect(perUnitPrice(casePrice, units)).toBe(unit);
  });

  it("is null rather than rounded when it is not whole paise", () => {
    expect(perUnitPrice("100.00", 3)).toBeNull();
  });

  it("is null for unusable input", () => {
    expect(perUnitPrice("abc", 10)).toBeNull();
    expect(perUnitPrice("100.00", 0)).toBeNull();
  });
});
