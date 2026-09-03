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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
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
