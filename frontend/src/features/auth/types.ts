import { z } from "zod";

/**
 * Response shapes of /api/v1/auth, mirroring backend/app/schemas/auth.py.
 *
 * Responses are parsed at runtime rather than cast, so a contract change is
 * caught at the boundary instead of surfacing as a broken screen.
 */

export const retailerProfileSchema = z.object({
  id: z.string(),
  business_name: z.string(),
  owner_name: z.string(),
  address: z.string(),
});

/**
 * AuthenticatedUser. `role` is kept as a plain string here on purpose: the
 * backend currently sends "retailer" or "order_desk" (see roles.ts), and any
 * other value must be denied by the UI rather than crash the session.
 */
export const authenticatedUserSchema = z.object({
  id: z.string(),
  full_name: z.string(),
  phone_number: z.string(),
  role: z.string(),
  retailer: retailerProfileSchema.nullable(),
});

/**
 * OtpRequestAccepted: a generic message. Codes are delivered by the backend
 * (SMS, or the development console and outbox). The one exception is
 * demo_code, present only for the demonstration accounts a deployment lists
 * in AUTH_DEMO_OTP_NUMBERS, and only for a browser that has unlocked
 * reviewer access (a reviewed deployment without SMS). z.object strips any
 * other key, so nothing else code-like reaches the UI.
 */
export const otpRequestAcceptedSchema = z.object({
  message: z.string(),
  // Anything other than six digits is ignored rather than shown.
  demo_code: z
    .string()
    .regex(/^\d{6}$/)
    .optional()
    .catch(undefined),
});

/**
 * GET /auth/demo-access: whether this deployment offers reviewer access to
 * the demonstration accounts, and whether this browser has unlocked it. The
 * access key and the access cookie never reach JavaScript.
 */
export const demoAccessStatusSchema = z.object({
  enabled: z.boolean(),
  active: z.boolean(),
});

export type DemoAccessStatus = z.infer<typeof demoAccessStatusSchema>;
export type RetailerProfile = z.infer<typeof retailerProfileSchema>;
export type AuthenticatedUser = z.infer<typeof authenticatedUserSchema>;
export type OtpRequestAccepted = z.infer<typeof otpRequestAcceptedSchema>;
