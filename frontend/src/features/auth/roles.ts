/**
 * Roles and the workspace each one lands in.
 *
 * Mirrors UserRole in backend/app/models/enums.py. The role always comes
 * from the backend session (/auth/me or /auth/verify-otp), never from the
 * client. These checks only decide what to render; the backend enforces
 * authorisation on every protected endpoint.
 */
export const USER_ROLES = ["retailer", "order_desk"] as const;

export type UserRole = (typeof USER_ROLES)[number];

export interface Workspace {
  path: string;
  label: string;
}

const WORKSPACES: Record<UserRole, Workspace> = {
  retailer: { path: "/retailer", label: "Retailer" },
  order_desk: { path: "/desk", label: "Order desk" },
};

export function isUserRole(value: string): value is UserRole {
  return (USER_ROLES as readonly string[]).includes(value);
}

/** The workspace for a role, or null for a role this app does not support. */
export function workspaceFor(role: string): Workspace | null {
  return isUserRole(role) ? WORKSPACES[role] : null;
}

export const SIGN_IN_PATH = "/sign-in";
