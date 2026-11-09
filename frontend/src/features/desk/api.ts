import {
  infiniteQueryOptions,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import type { z } from "zod";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import type { AuthenticatedUser } from "@/features/auth/types";
import { isOrderId } from "@/features/orders/paths";
import type { OrderStatus } from "@/features/orders/status";
import { api } from "@/lib/api";

import { deskFailure, needsRefresh } from "./errors";
import {
  confirmOrderResponseSchema,
  deskOrderDetailSchema,
  deskOrderPageSchema,
  deskOrderPreviewSchema,
  rejectOrderResponseSchema,
  type ConfirmOrderResponse,
  type DeskOrderDetail,
  type DeskOrderPage,
  type DeskOrderPreview,
  type RejectOrderResponse,
} from "./types";

/**
 * Calls to /api/v1/desk/orders and the hooks built on them.
 *
 * Mutations are never retried automatically (the app's default), and every
 * cache write after one is checked against the session: a response that
 * arrives after the signed-in user changed is not written into the new
 * session's cache.
 */

/** Orders per request (the backend allows up to 100). */
export const DESK_PAGE_SIZE = 50;

export interface QueueFilters {
  status: OrderStatus | null;
  /** Trimmed search text, or "" for none. */
  search: string;
  retailerId: string | null;
  createdFrom: string | null;
  createdTo: string | null;
}

export const deskKeys = {
  all: ["desk", "orders"] as const,
  lists: () => [...deskKeys.all, "list"] as const,
  list: (filters: QueueFilters) => [...deskKeys.lists(), filters] as const,
  detail: (orderId: string) => [...deskKeys.all, "detail", orderId] as const,
  preview: (orderId: string) => [...deskKeys.all, "preview", orderId] as const,
};

function parseResponse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

function orderUrl(orderId: string, action = ""): string {
  return `/desk/orders/${encodeURIComponent(orderId)}${action}`;
}

export async function fetchQueuePage(
  filters: QueueFilters,
  offset: number,
  signal?: AbortSignal,
): Promise<DeskOrderPage> {
  const params = new URLSearchParams({ limit: String(DESK_PAGE_SIZE), offset: String(offset) });
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.search) {
    params.set("search", filters.search);
  }
  if (filters.retailerId) {
    params.set("retailer_id", filters.retailerId);
  }
  if (filters.createdFrom) {
    params.set("created_from", filters.createdFrom);
  }
  if (filters.createdTo) {
    params.set("created_to", filters.createdTo);
  }
  const data = await api.get<unknown>(`/desk/orders?${params.toString()}`, { signal });
  return parseResponse(deskOrderPageSchema, data);
}

export async function fetchDeskOrder(orderId: string, signal?: AbortSignal) {
  const data = await api.get<unknown>(orderUrl(orderId), { signal });
  return parseResponse(deskOrderDetailSchema, data);
}

export async function fetchPreview(orderId: string, signal?: AbortSignal) {
  const data = await api.get<unknown>(orderUrl(orderId, "/preview"), { signal });
  return parseResponse(deskOrderPreviewSchema, data);
}

export async function confirmOrder(
  orderId: string,
  approvedFingerprint: string,
): Promise<ConfirmOrderResponse> {
  const data = await api.post<unknown>(orderUrl(orderId, "/confirm"), {
    approved_fingerprint: approvedFingerprint,
  });
  return parseResponse(confirmOrderResponseSchema, data);
}

export async function rejectOrder(orderId: string, reason: string): Promise<RejectOrderResponse> {
  const data = await api.post<unknown>(orderUrl(orderId, "/reject"), { reason });
  return parseResponse(rejectOrderResponseSchema, data);
}

export function nextQueueOffset(page: DeskOrderPage): number | undefined {
  const next = page.offset + page.items.length;
  return page.items.length > 0 && next < page.total ? next : undefined;
}

/** The queue's query, shared by the queue and the desk summary's counts. */
export function deskQueueOptions(filters: QueueFilters) {
  return infiniteQueryOptions({
    queryKey: deskKeys.list(filters),
    queryFn: ({ pageParam, signal }) => fetchQueuePage(filters, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: nextQueueOffset,
  });
}

export function useDeskQueue(filters: QueueFilters) {
  return useInfiniteQuery({ ...deskQueueOptions(filters), placeholderData: keepPreviousData });
}

export function useDeskOrder(orderId: string) {
  return useQuery({
    queryKey: deskKeys.detail(orderId),
    queryFn: ({ signal }) => fetchDeskOrder(orderId, signal),
    enabled: isOrderId(orderId),
  });
}

export function useDeskPreview(orderId: string, enabled: boolean) {
  return useQuery({
    queryKey: deskKeys.preview(orderId),
    queryFn: ({ signal }) => fetchPreview(orderId, signal),
    enabled,
    // Always priced afresh on opening; never replaced behind the reviewer's
    // back while they read it.
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
}

/** True while `userId` is still the signed-in user. */
export function sameSession(queryClient: QueryClient, userId: string): boolean {
  return queryClient.getQueryData<AuthenticatedUser | null>(SESSION_QUERY_KEY)?.id === userId;
}

/** Re-read an order (and its preview and the queue) from the server. */
export function refreshOrder(queryClient: QueryClient, orderId: string): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const [scope, kind, kindOrFilters, id] = query.queryKey;
      if (scope !== "desk" || kind !== "orders") {
        return false;
      }
      return (
        kindOrFilters === "list" ||
        ((kindOrFilters === "detail" || kindOrFilters === "preview") && id === orderId)
      );
    },
  });
}

function writeOrder(queryClient: QueryClient, order: DeskOrderDetail): void {
  queryClient.setQueryData(deskKeys.detail(order.id), order);
  // The order left (or may have left) review: its preview is stale, and the
  // queue has changed. Mark only; nothing refetches a preview it disabled.
  void queryClient.invalidateQueries({ queryKey: deskKeys.preview(order.id), refetchType: "none" });
  void queryClient.invalidateQueries({ queryKey: deskKeys.lists() });
}

export interface ConfirmVariables {
  fingerprint: string;
  sessionUserId: string;
}

export interface RejectVariables {
  reason: string;
  sessionUserId: string;
}

export const DESK_MUTATION_KEY = ["desk", "orders", "mutation"] as const;

/*
 * Session safety: each mutation carries the id of the user who started it.
 * A response that arrives after that user signed out or another user signed
 * in is ignored entirely: nothing is written to, or invalidated in, the
 * cache (which by then belongs to the new session). The review screen is
 * keyed by the signed-in user, so its banners and in-progress steps are not
 * carried across a session change either (see order-review-screen.tsx).
 */

export function useConfirmOrder(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...DESK_MUTATION_KEY, "confirm", orderId],
    mutationFn: ({ fingerprint }: ConfirmVariables) => confirmOrder(orderId, fingerprint),
    onSuccess: (result, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId)) {
        writeOrder(queryClient, result.order);
      }
    },
    onError: (error, { sessionUserId }) => {
      if (!sameSession(queryClient, sessionUserId)) {
        return;
      }
      const failure = deskFailure(error);
      if (failure.kind === "outdated") {
        // The fresh preview replaces the approved one: the desk must review
        // and confirm again. Its fingerprint is kept exactly as sent.
        queryClient.setQueryData<DeskOrderPreview>(deskKeys.preview(orderId), failure.preview);
      } else if (failure.kind === "issues") {
        void queryClient.invalidateQueries({ queryKey: deskKeys.preview(orderId) });
      } else if (needsRefresh(failure)) {
        void refreshOrder(queryClient, orderId);
      }
    },
  });
}

export function useRejectOrder(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...DESK_MUTATION_KEY, "reject", orderId],
    mutationFn: ({ reason }: RejectVariables) => rejectOrder(orderId, reason),
    onSuccess: (result, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId)) {
        writeOrder(queryClient, result.order);
      }
    },
    onError: (error, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId) && needsRefresh(deskFailure(error))) {
        void refreshOrder(queryClient, orderId);
      }
    },
  });
}
