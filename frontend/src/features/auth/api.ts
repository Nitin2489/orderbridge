import {
  useMutation,
  useQuery,
  useQueryClient,
  type Query,
  type QueryClient,
} from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import type { z } from "zod";

import { api, isApiError } from "@/lib/api";

import { SIGN_IN_PATH } from "./roles";
import {
  authenticatedUserSchema,
  otpRequestAcceptedSchema,
  type AuthenticatedUser,
  type OtpRequestAccepted,
} from "./types";

/**
 * Calls to /api/v1/auth and the TanStack Query hooks built on them.
 *
 * The session itself is the backend's HttpOnly cookie. JavaScript never sees
 * a token: the client only caches who the backend says is signed in, under
 * SESSION_QUERY_KEY, and that cache is replaced on sign-in and cleared on
 * sign-out or expiry.
 */

export const SESSION_QUERY_KEY = ["auth", "session"] as const;

/** Mutation keys share the "auth" prefix so the expiry listener can skip them. */
export const AUTH_KEY_PREFIX = "auth";

function parseResponse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

export async function requestOtp(phoneNumber: string): Promise<OtpRequestAccepted> {
  const data = await api.post<unknown>("/auth/request-otp", {
    phone_number: phoneNumber,
  });
  return parseResponse(otpRequestAcceptedSchema, data);
}

export interface VerifyOtpInput {
  phoneNumber: string;
  code: string;
}

export async function verifyOtp({
  phoneNumber,
  code,
}: VerifyOtpInput): Promise<AuthenticatedUser> {
  const data = await api.post<unknown>("/auth/verify-otp", {
    phone_number: phoneNumber,
    code,
  });
  return parseResponse(authenticatedUserSchema, data);
}

/** The signed-in user, or null when the backend says there is no valid session. */
export async function fetchSession(signal?: AbortSignal): Promise<AuthenticatedUser | null> {
  try {
    const data = await api.get<unknown>("/auth/me", { signal });
    return parseResponse(authenticatedUserSchema, data);
  } catch (error) {
    if (isApiError(error) && error.status === 401) {
      return null;
    }
    throw error;
  }
}

export async function logout(): Promise<void> {
  await api.post<void>("/auth/logout");
}

function isSessionQuery(query: Query): boolean {
  const [scope, name] = query.queryKey;
  return scope === SESSION_QUERY_KEY[0] && name === SESSION_QUERY_KEY[1];
}

/**
 * Replace the client's view of the session. Everything cached for a previous
 * user is dropped first, so no protected data outlives the session it was
 * fetched under.
 */
async function replaceClientSession(
  queryClient: QueryClient,
  user: AuthenticatedUser | null,
): Promise<void> {
  // Stop in-flight requests (including a session refetch) from writing stale
  // results back after the switch.
  await queryClient.cancelQueries();
  queryClient.removeQueries({ predicate: (query) => !isSessionQuery(query) });
  queryClient.setQueryData<AuthenticatedUser | null>(SESSION_QUERY_KEY, user);
}

export function beginClientSession(
  queryClient: QueryClient,
  user: AuthenticatedUser,
): Promise<void> {
  return replaceClientSession(queryClient, user);
}

export function endClientSession(queryClient: QueryClient): Promise<void> {
  return replaceClientSession(queryClient, null);
}

export function useSessionQuery() {
  return useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: ({ signal }) => fetchSession(signal),
    staleTime: 60_000,
    // Re-check when the user comes back to the tab, so a session revoked or
    // expired in the meantime is noticed promptly.
    refetchOnWindowFocus: true,
  });
}

export function useRequestOtp() {
  return useMutation({
    mutationKey: [AUTH_KEY_PREFIX, "request-otp"],
    mutationFn: requestOtp,
  });
}

export function useVerifyOtp() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: [AUTH_KEY_PREFIX, "verify-otp"],
    mutationFn: verifyOtp,
    // The mutation stays pending until the session is in the cache, so the
    // Verify button cannot be pressed again while the redirect happens.
    onSuccess: (user) => beginClientSession(queryClient, user),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationKey: [AUTH_KEY_PREFIX, "logout"],
    mutationFn: logout,
    onSuccess: async () => {
      await endClientSession(queryClient);
      router.replace(SIGN_IN_PATH);
    },
  });
}
