import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { sameSession } from "@/features/desk/api";
import { api, apiDownload, isApiError } from "@/lib/api";

import { exportKeys } from "./api";
import { SessionChangedError } from "./download";
import { saveFile } from "./save-file";

/**
 * TallyPrime XML sales vouchers for an export batch, and the Tally masters
 * they need. The backend builds the XML from the batch's stored CSV and
 * checks that every voucher balances; GST is not included.
 */

export const tallyMastersSchema = z.object({
  batch_number: z.string(),
  voucher_type: z.string(),
  sales_ledger: z.string().nullable(),
  discount_ledger: z.string().nullable(),
  discount_needed: z.boolean(),
  party_ledgers: z.array(z.string()),
  stock_items: z.array(z.object({ name: z.string(), unit: z.string() })),
  units: z.array(z.string()),
  missing_settings: z.array(z.string()),
  xml_url: z.string(),
});
export type TallyMasters = z.infer<typeof tallyMastersSchema>;

export const XML_ACCEPT = "application/xml, application/json;q=0.9";
export const XML_TYPE = "application/xml;charset=utf-8";

export class UnexpectedXmlError extends Error {
  constructor() {
    super("The server did not return an XML file.");
    this.name = "UnexpectedXmlError";
  }
}

function batchPath(batchId: string): string {
  return `/desk/exports/${encodeURIComponent(batchId)}`;
}

export function xmlFilename(batchNumber: string): string {
  const safe = batchNumber.replace(/[^A-Za-z0-9-]/g, "");
  return `orderbridge-tally-${safe || "export"}.xml`;
}

export async function fetchTallyMasters(
  batchId: string,
  signal?: AbortSignal,
): Promise<TallyMasters> {
  const data = await api.get<unknown>(`${batchPath(batchId)}/tally-masters`, { signal });
  const result = tallyMastersSchema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

export function useTallyMasters(batchId: string) {
  return useQuery({
    queryKey: [...exportKeys.batch(batchId), "tally-masters"],
    queryFn: ({ signal }) => fetchTallyMasters(batchId, signal),
  });
}

export function useTallyXmlDownload(batch: { id: string; batch_number: string }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: [...exportKeys.all, "mutation", "tally-xml", batch.id],
    mutationFn: async ({ sessionUserId }: { sessionUserId: string }) => {
      const { bytes, contentType } = await apiDownload(`${batchPath(batch.id)}/tally.xml`, {
        accept: XML_ACCEPT,
      });
      if (!contentType.toLowerCase().startsWith("application/xml")) {
        throw new UnexpectedXmlError();
      }
      if (!sameSession(queryClient, sessionUserId)) {
        throw new SessionChangedError();
      }
      saveFile(bytes, xmlFilename(batch.batch_number), XML_TYPE);
    },
  });
}

/** A message for a failed XML download, or null when nothing should be shown. */
export function xmlDownloadErrorMessage(error: unknown): string | null {
  if (error instanceof SessionChangedError) {
    return null;
  }
  if (error instanceof UnexpectedXmlError) {
    return "The server did not return an XML file, so nothing was saved.";
  }
  if (isApiError(error)) {
    if (error.status === 0) {
      return error.message;
    }
    if (error.status === 409) {
      // The backend explains what is missing or inconsistent.
      return error.message;
    }
    if (error.status === 404) {
      return "This export batch was not found.";
    }
    if (error.status === 403) {
      return "The download was refused. Reload the page and try again.";
    }
  }
  return "The Tally XML could not be downloaded. Please try again.";
}
