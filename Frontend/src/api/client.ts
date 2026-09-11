/**
 * Every backend call goes through here — one place to know the base URL,
 * attach headers, and turn a non-2xx response into a typed error, instead
 * of every page repeating its own fetch() boilerplate.
 */

// Defaults to the backend on the SAME host that served this page (just a
// different port), not a hardcoded "localhost". That matters because the
// WhatsApp inquiry form link (see Backend/Config/settings.py's
// inquiry_form_base_url) is deliberately built with this machine's LAN IP so
// a phone can open it — a bundle that always pointed at "localhost:8000"
// would be unreachable from that phone (and even from this same machine in
// a tab opened via the LAN IP, since browsers block a private-IP page from
// silently calling "localhost"). VITE_API_BASE_URL still overrides this for
// pointing at a different backend host/port entirely.
const DEFAULT_API_BASE_URL = `${window.location.protocol}//${window.location.hostname}:8000/api`;

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL;

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Content-Type is attached only when there is actually a body to describe.
 *
 * It reads as harmless boilerplate on a GET, but it is not: the backend is
 * a different origin (a different port is a different origin), and
 * "application/json" is not one of the values CORS lets through without
 * asking first. So every bodyless request carrying it became TWO round
 * trips — an OPTIONS preflight, then the real request. Dropping it makes a
 * GET a "simple" request, which the browser sends straight out.
 *
 * It also unblocks HTTP caching in practice: fewer moving parts between the
 * request and the browser's cache, and the conditional-request exchange
 * (see Backend/Middleware/http_cache.py) is only worth having if asking
 * "has this changed?" is genuinely cheaper than re-fetching.
 */
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const hasBody = options?.body !== undefined;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // `...options` first, headers last: spreading options AFTER the headers
    // would let an options object that carries its own `headers` replace the
    // computed ones wholesale rather than merge with them.
    ...options,
    headers: {
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = body?.detail ?? response.statusText;
    throw new ApiError(response.status, typeof message === "string" ? message : JSON.stringify(message));
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T,>(path: string): Promise<T> => request<T>(path),
  post: <T,>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T,>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T,>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T,>(path: string): Promise<T> => request<T>(path, { method: "DELETE" }),
};
