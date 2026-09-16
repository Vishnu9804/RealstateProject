/**
 * Every backend call goes through here — one place to know the base URL,
 * attach headers, and turn a non-2xx response into a typed error.
 *
 * Defaults to the backend on the SAME host that served this page (just a
 * different port), not a hardcoded "localhost" — matches the pattern used
 * by Frontend/ and LandingPage/. VITE_API_BASE_URL overrides it for
 * pointing at a different backend host/port entirely.
 */
const DEFAULT_API_BASE_URL = `${window.location.protocol}//${window.location.hostname}:8000/api`;

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL;

/** Must equal the backend's DASHBOARD_KEY when that is set (see
 *  Backend/Middleware/dashboard_access.py). Sent only when configured, so a
 *  local setup without a key keeps its GETs free of a CORS preflight. */
const DASHBOARD_KEY: string = import.meta.env.VITE_DASHBOARD_KEY || "";

/** `path?cursor=...` for the hourly feeds; the first request has no cursor. */
export function withCursor(path: string, cursor: string | null): string {
  return cursor ? `${path}?cursor=${encodeURIComponent(cursor)}` : path;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const hasBody = options?.body !== undefined;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...(DASHBOARD_KEY ? { "X-Dashboard-Key": DASHBOARD_KEY } : {}),
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
};
