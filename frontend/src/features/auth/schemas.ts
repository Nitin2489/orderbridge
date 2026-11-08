import { z } from "zod";

/**
 * Phone and code validation, mirroring backend/app/core/phone.py and
 * OtpVerifyRequest so that most mistakes are caught before a request.
 * The backend still validates everything.
 */

const SEPARATORS = /[\s\-().]/g;
const INDIAN_MOBILE = /^[6-9][0-9]{9}$/;

export const PHONE_REQUIRED_MESSAGE = "Enter your mobile number.";
export const PHONE_INVALID_MESSAGE = "Enter a valid 10-digit Indian mobile number.";
export const CODE_INCOMPLETE_MESSAGE = "Enter all 6 digits of the code.";

export const OTP_LENGTH = 6;

/**
 * Normalise to +91XXXXXXXXXX, or return null when the value is not an Indian
 * mobile number. Accepts +91XXXXXXXXXX, 91XXXXXXXXXX, 0XXXXXXXXXX and
 * XXXXXXXXXX, with optional spaces, hyphens, dots or brackets.
 */
export function normalizePhoneNumber(raw: string): string | null {
  const compact = raw.trim().replace(SEPARATORS, "");

  let national: string;
  if (compact.startsWith("+91")) {
    national = compact.slice(3);
  } else if (compact.startsWith("91") && compact.length === 12) {
    national = compact.slice(2);
  } else if (compact.startsWith("0") && compact.length === 11) {
    national = compact.slice(1);
  } else {
    national = compact;
  }

  return INDIAN_MOBILE.test(national) ? `+91${national}` : null;
}

/** "+919000000001" -> "+91 90000 00001", for display only. */
export function formatPhoneNumber(e164: string): string {
  const national = e164.startsWith("+91") ? e164.slice(3) : e164;
  if (national.length !== 10) {
    return e164;
  }
  return `+91 ${national.slice(0, 5)} ${national.slice(5)}`;
}

/** The 10-digit national part of a normalised number, for prefilling the field. */
export function nationalNumber(e164: string): string {
  return e164.startsWith("+91") ? e164.slice(3) : e164;
}

export const phoneFormSchema = z.object({
  phone: z
    .string()
    .trim()
    .min(1, PHONE_REQUIRED_MESSAGE)
    .transform((value, ctx) => {
      const normalised = normalizePhoneNumber(value);
      if (normalised === null) {
        ctx.addIssue({ code: "custom", message: PHONE_INVALID_MESSAGE });
        return z.NEVER;
      }
      return normalised;
    }),
});

export type PhoneFormInput = z.input<typeof phoneFormSchema>;
export type PhoneFormValues = z.output<typeof phoneFormSchema>;

export const otpCodeSchema = z
  .string()
  .regex(new RegExp(`^[0-9]{${OTP_LENGTH}}$`), CODE_INCOMPLETE_MESSAGE);
