import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { z } from "zod";

import { api } from "@/lib/api";

import { catalogueCategoriesSchema, cataloguePageSchema, type CataloguePage } from "./types";

/**
 * Calls to /api/v1/retailer/catalogue. The backend scopes every response to
 * the signed-in retailer from the session cookie; no retailer id is sent.
 */

/** Products per request (the backend allows up to 100). */
export const CATALOGUE_PAGE_SIZE = 24;

/** The backend's limits on the search and category parameters. */
export const SEARCH_MAX_LENGTH = 100;

export interface CatalogueFilters {
  /** Trimmed search text, or "" for none. */
  search: string;
  /** Exact category name from /categories, or null for all. */
  category: string | null;
}

export const catalogueKeys = {
  all: ["retailer", "catalogue"] as const,
  list: (filters: CatalogueFilters) => [...catalogueKeys.all, "list", filters] as const,
  categories: () => [...catalogueKeys.all, "categories"] as const,
};

function parseResponse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

export async function fetchCataloguePage(
  filters: CatalogueFilters,
  offset: number,
  signal?: AbortSignal,
): Promise<CataloguePage> {
  const params = new URLSearchParams({
    limit: String(CATALOGUE_PAGE_SIZE),
    offset: String(offset),
  });
  // The backend rejects empty values (min_length=1), so omit them.
  if (filters.search) {
    params.set("search", filters.search);
  }
  if (filters.category) {
    params.set("category", filters.category);
  }

  const data = await api.get<unknown>(`/retailer/catalogue?${params.toString()}`, { signal });
  return parseResponse(cataloguePageSchema, data);
}

export async function fetchCategories(signal?: AbortSignal): Promise<string[]> {
  const data = await api.get<unknown>("/retailer/catalogue/categories", { signal });
  return parseResponse(catalogueCategoriesSchema, data).categories;
}

/** Next offset, or undefined when every matching product has been loaded. */
export function nextCatalogueOffset(page: CataloguePage): number | undefined {
  const next = page.offset + page.items.length;
  return page.items.length > 0 && next < page.total ? next : undefined;
}

export function useCatalogue(filters: CatalogueFilters) {
  return useInfiniteQuery({
    queryKey: catalogueKeys.list(filters),
    queryFn: ({ pageParam, signal }) => fetchCataloguePage(filters, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: nextCatalogueOffset,
    // Keep the current results on screen while a new search or filter loads.
    placeholderData: keepPreviousData,
  });
}

export function useCatalogueCategories() {
  return useQuery({
    queryKey: catalogueKeys.categories(),
    queryFn: ({ signal }) => fetchCategories(signal),
    staleTime: 5 * 60_000,
  });
}
