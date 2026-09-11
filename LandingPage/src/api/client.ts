/**
 * One place that knows where the backend is. Same reasoning as
 * Frontend/src/api/client.ts: the base URL is derived from whatever host
 * served this page rather than hardcoded to "localhost", so opening the
 * site from a phone on the LAN reaches the same machine's API instead of
 * the phone's own loopback.
 */

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
 * Content-Type only when there is a body to describe — same reasoning as
 * Frontend/src/api/client.ts. On a public site this matters more, not less:
 * the API is a different origin, and sending "application/json" on a GET
 * turned every visitor's first look at the listings into an OPTIONS
 * preflight followed by the real request. Without it a GET is a "simple"
 * request and goes straight out, which also lets the browser's own cache
 * and the conditional-request exchange (Backend/Middleware/http_cache.py)
 * do their job on the heaviest responses this site serves — the photos.
 */
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const hasBody = options?.body !== undefined;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // `...options` first, headers last — see the Frontend client's note.
    ...options,
    headers: {
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = body?.detail ?? response.statusText;
    throw new ApiError(response.status, typeof message === "string" ? message : "Something went wrong.");
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T,>(path: string): Promise<T> => request<T>(path),
  post: <T,>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
};
