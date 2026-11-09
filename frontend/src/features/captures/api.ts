import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import type { z } from "zod";

import { sameSession } from "@/features/desk/api";
import { apiRequestWithStatus, api } from "@/lib/api";

import { captureFailure, needsReload } from "./errors";
import { isCaptureId } from "./paths";
import {
  captureDetailSchema,
  capturePageSchema,
  retailerDirectorySchema,
  submitCaptureResponseSchema,
  type CaptureDetail,
  type CapturePage,
  type CaptureStatus,
  type LineDecision,
} from "./types";

/**
 * Calls to /api/v1/captures and the hooks built on them.
 *
 * Every change names the capture version it was based on; a stale version
 * is refused by the backend and the capture is re-read. Nothing is retried
 * automatically. Cache writes are checked against the signed-in user, so a
 * late response never reaches another user's session.
 */

export const CAPTURE_PAGE_SIZE = 20;

export const captureKeys = {
  all: ["captures"] as const,
  lists: () => [...captureKeys.all, "list"] as const,
  list: (status: CaptureStatus | null) => [...captureKeys.lists(), status] as const,
  detail: (captureId: string) => [...captureKeys.all, "detail", captureId] as const,
  retailers: (search: string) => ["desk", "retailers", search] as const,
};

function parse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

function captureUrl(captureId: string, action = ""): string {
  return `/captures/${encodeURIComponent(captureId)}${action}`;
}

export async function fetchCapturePage(
  status: CaptureStatus | null,
  offset: number,
  signal?: AbortSignal,
): Promise<CapturePage> {
  const params = new URLSearchParams({
    limit: String(CAPTURE_PAGE_SIZE),
    offset: String(offset),
  });
  if (status) {
    params.set("status", status);
  }
  return parse(capturePageSchema, await api.get<unknown>(`/captures?${params}`, { signal }));
}

export async function fetchCapture(captureId: string, signal?: AbortSignal) {
  return parse(captureDetailSchema, await api.get<unknown>(captureUrl(captureId), { signal }));
}

export async function fetchRetailers(search: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: "50" });
  if (search) {
    params.set("search", search);
  }
  return parse(
    retailerDirectorySchema,
    await api.get<unknown>(`/desk/retailers?${params}`, { signal }),
  ).items;
}

export function useCaptures(status: CaptureStatus | null) {
  return useInfiniteQuery({
    queryKey: captureKeys.list(status),
    queryFn: ({ pageParam, signal }) => fetchCapturePage(status, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: (page: CapturePage) => {
      const next = page.offset + page.items.length;
      return page.items.length > 0 && next < page.total ? next : undefined;
    },
    placeholderData: keepPreviousData,
  });
}

export function useCapture(captureId: string) {
  return useQuery({
    queryKey: captureKeys.detail(captureId),
    queryFn: ({ signal }) => fetchCapture(captureId, signal),
    enabled: isCaptureId(captureId),
  });
}

export function useRetailerDirectory(search: string, enabled: boolean) {
  return useQuery({
    queryKey: captureKeys.retailers(search),
    queryFn: ({ signal }) => fetchRetailers(search, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

function writeCapture(
  queryClient: QueryClient,
  sessionUserId: string,
  capture: CaptureDetail,
): void {
  if (!sameSession(queryClient, sessionUserId)) {
    return;
  }
  queryClient.setQueryData(captureKeys.detail(capture.id), capture);
  void queryClient.invalidateQueries({ queryKey: captureKeys.lists() });
}

function reloadCapture(queryClient: QueryClient, captureId: string): void {
  void queryClient.invalidateQueries({ queryKey: captureKeys.detail(captureId) });
  void queryClient.invalidateQueries({ queryKey: captureKeys.lists() });
}

export interface CreateCaptureVariables {
  message: string;
  clientRequestId: string;
  retailerId: string | null;
  /** Order desk: create it even though it looks like a recent duplicate. */
  allowDuplicate?: boolean;
  sessionUserId: string;
}

export function useCreateCapture() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...captureKeys.all, "create"],
    mutationFn: async ({
      message,
      clientRequestId,
      retailerId,
      allowDuplicate,
    }: CreateCaptureVariables) => {
      const body: Record<string, string | boolean> = { client_request_id: clientRequestId, message };
      if (retailerId) {
        body.retailer_id = retailerId;
      }
      if (allowDuplicate) {
        body.allow_duplicate = true;
      }
      const { data } = await apiRequestWithStatus<unknown>("/captures", {
        method: "POST",
        body,
      });
      return parse(captureDetailSchema, data);
    },
    onSuccess: (capture, { sessionUserId }) => writeCapture(queryClient, sessionUserId, capture),
  });
}

export interface ReviewVariables {
  lineNumber: number;
  decision: LineDecision;
  expectedVersion: number;
  sessionUserId: string;
}

export async function reviewLine(
  captureId: string,
  { lineNumber, decision, expectedVersion }: Omit<ReviewVariables, "sessionUserId">,
): Promise<CaptureDetail> {
  const data = await api.post<unknown>(captureUrl(captureId, `/lines/${lineNumber}`), {
    expected_version: expectedVersion,
    ...decision,
  });
  return parse(captureDetailSchema, data);
}

export function useReviewLine(captureId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...captureKeys.all, "review", captureId],
    mutationFn: (variables: ReviewVariables) => reviewLine(captureId, variables),
    onSuccess: (capture, { sessionUserId }) => writeCapture(queryClient, sessionUserId, capture),
    onError: (error, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId) && needsReload(captureFailure(error))) {
        reloadCapture(queryClient, captureId);
      }
    },
  });
}

/**
 * Confirm the same unit for several lines, one request after another, each
 * naming the version the previous one returned. Stops at the first error;
 * lines already confirmed stay confirmed (each request is its own change).
 */
export function useConfirmUnitForLines(captureId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...captureKeys.all, "units", captureId],
    mutationFn: async ({
      lines,
      unit,
      expectedVersion,
      sessionUserId,
    }: {
      lines: { lineNumber: number; quantity: number }[];
      unit: "case" | "piece";
      expectedVersion: number;
      sessionUserId: string;
    }) => {
      let version = expectedVersion;
      let latest: CaptureDetail | null = null;
      for (const { lineNumber, quantity } of lines) {
        latest = await reviewLine(captureId, {
          lineNumber,
          decision: { action: "confirm_unit", unit, quantity },
          expectedVersion: version,
        });
        version = latest.version;
        writeCapture(queryClient, sessionUserId, latest);
      }
      return latest;
    },
    onError: (_error, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId)) {
        reloadCapture(queryClient, captureId);
      }
    },
  });
}

export function useDiscardCapture(captureId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...captureKeys.all, "discard", captureId],
    mutationFn: async ({
      reason,
      expectedVersion,
    }: {
      reason: string;
      expectedVersion: number;
      sessionUserId: string;
    }) =>
      parse(
        captureDetailSchema,
        await api.post<unknown>(captureUrl(captureId, "/discard"), {
          expected_version: expectedVersion,
          reason,
        }),
      ),
    onSuccess: (capture, { sessionUserId }) => writeCapture(queryClient, sessionUserId, capture),
    onError: (error, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId) && needsReload(captureFailure(error))) {
        reloadCapture(queryClient, captureId);
      }
    },
  });
}

export function useSubmitCapture(captureId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...captureKeys.all, "submit", captureId],
    mutationFn: async ({
      clientRequestId,
      expectedVersion,
    }: {
      clientRequestId: string;
      expectedVersion: number;
      sessionUserId: string;
    }) => {
      const { data } = await apiRequestWithStatus<unknown>(captureUrl(captureId, "/submit"), {
        method: "POST",
        body: { expected_version: expectedVersion, client_request_id: clientRequestId },
      });
      return parse(submitCaptureResponseSchema, data);
    },
    onSuccess: (result, { sessionUserId }) => {
      writeCapture(queryClient, sessionUserId, result.capture);
      if (sameSession(queryClient, sessionUserId)) {
        // A new pending order exists for the retailer and the desk.
        void queryClient.invalidateQueries({ queryKey: ["retailer", "orders"] });
        void queryClient.invalidateQueries({ queryKey: ["desk", "orders"] });
      }
    },
    onError: (error, { sessionUserId }) => {
      if (sameSession(queryClient, sessionUserId) && needsReload(captureFailure(error))) {
        reloadCapture(queryClient, captureId);
      }
    },
  });
}
