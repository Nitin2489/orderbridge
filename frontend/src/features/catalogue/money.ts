/**
 * Exact arithmetic on the backend's decimal money strings, for display
 * estimates only (a cart's subtotal at catalogue prices, a case price per
 * unit). Amounts are converted to integer paise, so there is no floating
 * point anywhere; any input that is not a plain non-negative amount with
 * at most two decimal places gives null instead of a guess.
 *
 * The backend remains authoritative: it prices every order, applies
 * schemes, and stores the totals the retailer and order desk see.
 */

const AMOUNT = /^(\d+)(?:\.(\d{1,2}))?$/;

/** "777.60" -> 77760 paise, or null. */
export function toPaise(amount: string): number | null {
  const match = AMOUNT.exec(amount);
  if (!match) {
    return null;
  }
  const rupees = Number(match[1]);
  const paise = Number((match[2] ?? "").padEnd(2, "0"));
  const total = rupees * 100 + paise;
  return Number.isSafeInteger(total) ? total : null;
}

/** 77760 paise -> "777.60". */
export function paiseToAmount(paise: number): string {
  if (!Number.isSafeInteger(paise) || paise < 0) {
    throw new RangeError("Paise must be a non-negative safe integer.");
  }
  return `${Math.floor(paise / 100)}.${String(paise % 100).padStart(2, "0")}`;
}

/** price x quantity, exactly ("777.60" x 5 -> "3888.00"), or null. */
export function lineAmount(price: string, quantity: number): string | null {
  const paise = toPaise(price);
  if (paise === null || !Number.isSafeInteger(quantity) || quantity < 0) {
    return null;
  }
  const total = paise * quantity;
  return Number.isSafeInteger(total) ? paiseToAmount(total) : null;
}

/** The exact sum of amounts, or null if any of them is unusable. */
export function sumAmounts(amounts: readonly (string | null)[]): string | null {
  let total = 0;
  for (const amount of amounts) {
    const paise = amount === null ? null : toPaise(amount);
    if (paise === null) {
      return null;
    }
    total += paise;
    if (!Number.isSafeInteger(total)) {
      return null;
    }
  }
  return paiseToAmount(total);
}

/**
 * The price of one unit inside a case ("777.60" / 96 -> "8.10"), only when
 * it divides into whole paise. Otherwise null: a rounded figure would be a
 * price the backend never charged.
 */
export function perUnitPrice(casePrice: string, unitsPerCase: number): string | null {
  const paise = toPaise(casePrice);
  if (paise === null || !Number.isSafeInteger(unitsPerCase) || unitsPerCase <= 0) {
    return null;
  }
  if (paise % unitsPerCase !== 0) {
    return null;
  }
  return paiseToAmount(paise / unitsPerCase);
}
