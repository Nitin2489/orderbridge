import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { z } from "zod";

import { api } from "@/lib/api";

import { isOrderId } from "./paths";
import type { OrderStatus } from "./status";
import {
  retailerOrderDetailSchema,
  retailerOrderPageSchema,
  type RetailerOrderDetail,
  type RetailerOrderPage,
} from "./types";

/**
 * Calls to /api/v1/retailer/orders. The backend scopes every request to the
 * signed-in retailer from the session cookie; no retailer id is sent.
 *
 * Cached order data is dropped whenever the signed-in user changes (the
 * session switch removes every non-session query), so one retailer's orders
 * never show for another.
 */

/** Orders per request (the backend allows up to 100). */
export const ORDERS_PAGE_SIZE = 20;

export const orderKeys = {
  all: ["retailer", "orders"] as const,
  list: (status: OrderStatus | null) => [...orderKeys.all, "list", { status }] as const,
  detail: (orderId: string) => [...orderKeys.all, "detail", orderId] as const,
};

function parseResponse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

export async function fetchOrdersPage(
  status: OrderStatus | null,
  offset: number,
  signal?: AbortSignal,
): Promise<RetailerOrderPage> {
  const params = new URLSearchParams({
    limit: String(ORDERS_PAGE_SIZE),
    offset: String(offset),
  });
  if (status) {
    params.set("status", status);
  }
  const data = await api.get<unknown>(`/retailer/orders?${params.toString()}`, { signal });
  return parseResponse(retailerOrderPageSchema, data);
}

export async function fetchOrder(orderId: string, signal?: AbortSignal): Promise<RetailerOrderDetail> {
  const data = await api.get<unknown>(`/retailer/orders/${encodeURIComponent(orderId)}`, {
    signal,
  });
  return parseResponse(retailerOrderDetailSchema, data);
}

export async function repeatOrder(
  sourceOrderId: string,
  clientRequestId: string,
): Promise<RetailerOrderDetail> {
  const data = await api.post<unknown>(
    `/retailer/orders/${encodeURIComponent(sourceOrderId)}/repeat`,
    { client_request_id: clientRequestId },
  );
  return parseResponse(retailerOrderDetailSchema, data);
}

/** Next offset, or undefined when every matching order has been loaded. */
export function nextOrdersOffset(page: RetailerOrderPage): number | undefined {
  const next = page.offset + page.items.length;
  return page.items.length > 0 && next < page.total ? next : undefined;
}

export function useOrders(status: OrderStatus | null) {
  return useInfiniteQuery({
    queryKey: orderKeys.list(status),
    queryFn: ({ pageParam, signal }) => fetchOrdersPage(status, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: nextOrdersOffset,
    // Keep the current list on screen while another filter loads.
    placeholderData: keepPreviousData,
  });
}

export function useOrder(orderId: string) {
  return useQuery({
    queryKey: orderKeys.detail(orderId),
    queryFn: ({ signal }) => fetchOrder(orderId, signal),
    // Malformed ids are "not found" without asking the server.
    enabled: isOrderId(orderId),
  });
}

export interface RepeatVariables {
  sourceOrderId: string;
  clientRequestId: string;
}

export const REPEAT_ORDER_MUTATION_KEY = ["retailer", "orders", "repeat"] as const;

export function useRepeatOrder(options: {
  onSuccess?: (order: RetailerOrderDetail, variables: RepeatVariables) => void;
  onError?: (error: Error, variables: RepeatVariables) => void;
}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: REPEAT_ORDER_MUTATION_KEY,
    mutationFn: ({ sourceOrderId, clientRequestId }: RepeatVariables) =>
      repeatOrder(sourceOrderId, clientRequestId),
    onSuccess: (order, variables) => {
      // The history now has a new order.
      void queryClient.invalidateQueries({ queryKey: orderKeys.all });
      options.onSuccess?.(order, variables);
    },
    onError: (error, variables) => options.onError?.(error, variables),
  });
}
