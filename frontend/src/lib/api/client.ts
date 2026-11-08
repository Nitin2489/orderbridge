import type { ApiErrorBody } from "@/types/api";

import { parseRetryAfter } from "./retry-after";

/**
 * Typed client for the OrderBridge API.
 *
 * All requests go to relative /api/v1 paths on the frontend's own origin;
 * next.config.ts proxies them to the FastAPI backend. The session cookie is
 * HttpOnly and first-party, so requests use credentials: "same-origin" and
 * never handle tokens directly.
 *
 * Intended for the browser (client components, TanStack Query). Relative
 * URLs do not resolve in server-side fetches.
 */
export const API_BASE_PATH = "/api/v1";

export class ApiError extends Error {
  /** HTTP status, or 0 when the server could not be reached. */
  readonly status: number;
  /** The parsed JSON error body, when the server sent one. */
  readonly body: ApiErrorBody | null;
  /**
   * Seconds the server asked the client to wait (Retry-After header, sent
   * with 429), or null when it gave none or an unusable value.
   */
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    message: string,
    body: ApiErrorBody | null,
    retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiRequestOptions {
  method?: HttpMethod;
  /** Serialised as JSON. */
  body?: unknown;
  signal?: AbortSignal;
  headers?: HeadersInit;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJson(response: Response): boolean {
  return (response.headers.get("content-type") ?? "").includes("application/json");
}

/** A human-readable message; never raw server internals. */
function errorMessage(status: number, body: ApiErrorBody | null): string {
  const detail = body?.detail;

  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail)) {
    return "Some details are not valid. Please check them and try again.";
  }
  if (status === 401) {
    return "Please sign in to continue.";
  }
  if (status === 403) {
    return "You do not have access to this.";
  }
  if (status === 404) {
    return "This could not be found.";
  }
  if (status >= 500) {
    return "Something went wrong on our side. Please try again.";
  }
  return "The request could not be completed.";
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null;

  if (isJson(response)) {
    try {
      const parsed: unknown = await response.json();
      body = isRecord(parsed) ? (parsed as ApiErrorBody) : null;
    } catch {
      body = null;
    }
  }

  return new ApiError(
    response.status,
    errorMessage(response.status, body),
    body,
    parseRetryAfter(response.headers.get("retry-after")),
  );
}

const UNREACHABLE = "Unable to reach OrderBridge. Check your connection and try again.";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * Send a request to `${API_BASE_PATH}${path}` and return the successful
 * response. Throws ApiError for a non-2xx response or a network failure;
 * aborts are re-thrown unchanged.
 */
async function send(path: string, options: ApiRequestOptions, accept: string): Promise<Response> {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("API paths must be relative and start with a single '/'.");
  }

  const headers = new Headers(options.headers);
  headers.set("Accept", accept);

  let body: string | undefined;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_PATH}${path}`, {
      method: options.method ?? "GET",
      headers,
      body,
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (error) {
    if (isAbort(error)) {
      throw error;
    }
    throw new ApiError(0, UNREACHABLE, null);
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  return response;
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return undefined as T;
  }

  if (!isJson(response)) {
    throw new ApiError(response.status, "Unexpected response from the server.", null);
  }

  return (await response.json()) as T;
}

/**
 * Send a request to `${API_BASE_PATH}${path}` and return the parsed JSON.
 *
 * Throws ApiError for any non-2xx response (with the parsed error body) and
 * for network failures (status 0). Aborts are re-thrown unchanged so that
 * TanStack Query can cancel requests. A 204 response resolves to undefined.
 */
export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  return readJson<T>(await send(path, options, "application/json"));
}

/**
 * Like apiRequest, but also returns the HTTP status, for endpoints whose
 * successful statuses mean different things (e.g. 201 created, 200 the
 * result of an identical earlier request).
 */
export async function apiRequestWithStatus<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<{ status: number; data: T }> {
  const response = await send(path, options, "application/json");
  return { status: response.status, data: await readJson<T>(response) };
}

export interface ApiDownload {
  /** The exact response bytes. */
  bytes: ArrayBuffer;
  /** The Content-Type header, or "" when there was none. */
  contentType: string;
}

/**
 * GET a file (for example a CSV) through the same-origin proxy, with the
 * session cookie, and return its exact bytes. Errors are the same ApiErrors
 * as apiRequest (the backend answers errors in JSON); a connection lost
 * while reading the body is a network failure (status 0).
 */
export async function apiDownload(
  path: string,
  { accept, signal }: { accept: string; signal?: AbortSignal },
): Promise<ApiDownload> {
  const response = await send(path, { method: "GET", signal }, accept);
  let bytes: ArrayBuffer;
  try {
    bytes = await response.arrayBuffer();
  } catch (error) {
    if (isAbort(error)) {
      throw error;
    }
    throw new ApiError(0, UNREACHABLE, null);
  }
  return { bytes, contentType: response.headers.get("content-type") ?? "" };
}

type RequestInitWithoutMethod = Omit<ApiRequestOptions, "method" | "body">;

/** Convenience wrappers around apiRequest. */
export const api = {
  get: <T>(path: string, options?: RequestInitWithoutMethod) =>
    apiRequest<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestInitWithoutMethod) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
};
