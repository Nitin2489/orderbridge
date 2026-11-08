import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { z } from "zod";

import { deskKeys, sameSession } from "@/features/desk/api";
import { api, apiRequestWithStatus } from "@/lib/api";

import {
  CSV_TYPE,
  SessionChangedError,
  canVerifyDownloads,
  fetchBatchCsv,
} from "./download";
import { exportFailure } from "./errors";
import { csvFilename, isBatchId } from "./paths";
import { saveFile } from "./save-file";
import {
  eligibleExportSchema,
  exportBatchDetailSchema,
  exportBatchPageSchema,
  exportBatchSummarySchema,
  type CreateExportResult,
  type DownloadableBatch,
  type ExportBatchPage,
} from "./types";

/**
 * Calls to /api/v1/desk/exports and the hooks built on them.
 *
 * The backend decides which orders are exported, builds the CSV and
 * reports the batch's count and total; nothing here computes or filters
 * them. Export creation is never retried automatically.
 *
 * Session safety: a mutation carries the id of the user who started it, and
 * its response is ignored (no cache writes or invalidations, no file saved)
 * if another user is signed in, or nobody is, by the time it arrives. The
 * screens are also keyed by the signed-in user, so their banners and
 * in-progress steps start afresh after a session change.
 */

/** Batches per history request (the backend allows up to 100). */
export const EXPORT_HISTORY_PAGE_SIZE = 20;

export const exportKeys = {
  all: ["desk", "exports"] as const,
  eligible: () => [...exportKeys.all, "eligible"] as const,
  history: () => [...exportKeys.all, "history"] as const,
  batch: (batchId: string) => [...exportKeys.all, "batch", batchId] as const,
};

function parseResponse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

export async function fetchEligible(signal?: AbortSignal) {
  const data = await api.get<unknown>("/desk/exports/eligible", { signal });
  return parseResponse(eligibleExportSchema, data);
}

export async function fetchHistoryPage(offset: number, signal?: AbortSignal) {
  const params = new URLSearchParams({
    limit: String(EXPORT_HISTORY_PAGE_SIZE),
    offset: String(offset),
  });
  const data = await api.get<unknown>(`/desk/exports?${params.toString()}`, { signal });
  return parseResponse(exportBatchPageSchema, data);
}

export async function fetchBatch(batchId: string, signal?: AbortSignal) {
  const data = await api.get<unknown>(`/desk/exports/${encodeURIComponent(batchId)}`, {
    signal,
  });
  return parseResponse(exportBatchDetailSchema, data);
}

/**
 * Export every confirmed order waiting at this moment. The body carries
 * only the idempotency key: no cutoff, no order ids, no totals.
 */
export async function createExport(clientRequestId: string): Promise<CreateExportResult> {
  const { status, data } = await apiRequestWithStatus<unknown>("/desk/exports", {
    method: "POST",
    body: { client_request_id: clientRequestId },
  });
  return { batch: parseResponse(exportBatchSummarySchema, data), created: status === 201 };
}

export function nextHistoryOffset(page: ExportBatchPage): number | undefined {
  const next = page.offset + page.items.length;
  return page.items.length > 0 && next < page.total ? next : undefined;
}

export function useEligibleExport() {
  return useQuery({
    queryKey: exportKeys.eligible(),
    queryFn: ({ signal }) => fetchEligible(signal),
    staleTime: 0,
  });
}

export function useExportHistory() {
  return useInfiniteQuery({
    queryKey: exportKeys.history(),
    queryFn: ({ pageParam, signal }) => fetchHistoryPage(pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: nextHistoryOffset,
    placeholderData: keepPreviousData,
  });
}

export function useExportBatch(batchId: string) {
  return useQuery({
    queryKey: exportKeys.batch(batchId),
    queryFn: ({ signal }) => fetchBatch(batchId, signal),
    enabled: isBatchId(batchId),
  });
}

export interface CreateExportVariables {
  clientRequestId: string;
  sessionUserId: string;
}

export const EXPORT_MUTATION_KEY = [...exportKeys.all, "mutation"] as const;

export function useCreateExport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...EXPORT_MUTATION_KEY, "create"],
    mutationFn: ({ clientRequestId }: CreateExportVariables) => createExport(clientRequestId),
    onSuccess: (_result, { sessionUserId }) => {
      if (!sameSession(queryClient, sessionUserId)) {
        return;
      }
      // The waiting summary and history changed, and the exported orders'
      // status changed in the desk's queue and order details.
      void queryClient.invalidateQueries({ queryKey: exportKeys.all });
      void queryClient.invalidateQueries({ queryKey: deskKeys.all });
    },
    onError: (error, { sessionUserId }) => {
      if (!sameSession(queryClient, sessionUserId)) {
        return;
      }
      void queryClient.invalidateQueries({ queryKey: exportKeys.all });
      if (exportFailure(error).kind === "uncertain") {
        // The export may have happened: order statuses may have changed.
        void queryClient.invalidateQueries({ queryKey: deskKeys.all });
      }
    },
  });
}

export interface DownloadVariables {
  sessionUserId: string;
}

export interface DownloadResult {
  verified: boolean;
}

/**
 * Download a batch's CSV and save it. With Web Crypto the checksum must
 * match first; without it the file is saved unverified (the UI says so).
 * Nothing is saved if the session changed during the download.
 */
export function useBatchDownload(batch: DownloadableBatch) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...EXPORT_MUTATION_KEY, "download", batch.id],
    mutationFn: async ({ sessionUserId }: DownloadVariables): Promise<DownloadResult> => {
      const file = await fetchBatchCsv(batch, { verify: canVerifyDownloads() });
      if (!sameSession(queryClient, sessionUserId)) {
        throw new SessionChangedError();
      }
      saveFile(file.bytes, csvFilename(batch.batch_number), CSV_TYPE);
      return { verified: file.verified };
    },
  });
}
