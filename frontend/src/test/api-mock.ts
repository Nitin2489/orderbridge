import { vi } from "vitest";

/**
 * A fetch stub for component tests.
 *
 * It answers the frontend's own /api/v1 requests with the response shapes
 * the FastAPI backend documents. It tests how the UI handles those
 * responses; it is not evidence that the real backend behaves this way
 * (the Playwright tests in e2e/ cover that against the running stack).
 */

export interface RecordedRequest {
  method: string;
  /** Path relative to /api/v1, without the query string. */
  path: string;
  query: URLSearchParams;
  body: unknown;
}

type Handler = (request: RecordedRequest) => Response | Promise<Response>;

export function json(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Handler {
  return () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    });
}

export function noContent(): Handler {
  return () => new Response(null, { status: 204 });
}

export function networkFailure(): Handler {
  return () => {
    throw new TypeError("Failed to fetch");
  };
}

/** Respond with `handlers` in turn; the last one repeats. */
export function sequence(...handlers: Handler[]): Handler {
  let index = 0;
  return (request) => {
    const handler = handlers[Math.min(index, handlers.length - 1)];
    index += 1;
    if (!handler) {
      throw new Error("sequence() needs at least one handler");
    }
    return handler(request);
  };
}

/** A response that only arrives when `resolve` is called. */
export function deferred(handler: Handler): { handler: Handler; resolve: () => void } {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return {
    handler: async (request) => {
      await gate;
      return handler(request);
    },
    resolve: () => release(),
  };
}

/**
 * Install the stub. Keys are "METHOD /path" relative to /api/v1, for
 * example "POST /auth/verify-otp"; the query string is not part of the key
 * (inspect it via `calls(...)[i].query`). Unmatched requests get a 500 so a
 * missing route fails the test visibly.
 */
export function mockApi(routes: Record<string, Handler>) {
  const requests: RecordedRequest[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const [path = "", queryString = ""] = url.replace(/^\/api\/v1/, "").split("?");
    const query = new URLSearchParams(queryString);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    const request = { method, path, query, body };
    requests.push(request);

    const handler = routes[`${method} ${path}`];
    if (!handler) {
      return json(500, { detail: `No mock for ${method} ${path}` })(request);
    }
    return handler(request);
  });

  vi.stubGlobal("fetch", fetchMock);

  return {
    requests,
    /** Requests made to one route. */
    calls: (route: string) => requests.filter((r) => `${r.method} ${r.path}` === route),
  };
}

export const NOT_AUTHENTICATED = json(401, { detail: "Not authenticated." });

export const retailerUser = {
  id: "5f0c8a52-3d1e-4c2b-9d1a-111111111111",
  full_name: "Amit Sharma",
  phone_number: "+919000000001",
  role: "retailer",
  retailer: {
    id: "7a1b2c3d-4e5f-4a6b-8c7d-222222222222",
    business_name: "Sharma General Store",
    owner_name: "Amit Sharma",
    address: "Napier Town, Jabalpur",
  },
};

export const deskUser = {
  id: "9e8d7c6b-5a4f-4e3d-9c2b-333333333333",
  full_name: "Order Desk Operator",
  phone_number: "+919000000010",
  role: "order_desk",
  retailer: null,
};
