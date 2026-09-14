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

// The one place the login token is stored/read — AuthProvider (see
// state/AuthProvider.tsx) is the only other thing that touches this key
// directly, for restoring/clearing a session; every other piece of the app
// just gets the header attached automatically below.
export const AUTH_TOKEN_STORAGE_KEY = "authToken";

export function getStoredAuthToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    // Private-browsing/storage-disabled edge case — same "just act logged
    // out" fallback AuthProvider uses on the same failure.
    return null;
  }
}

export function setStoredAuthToken(token: string): void {
  try {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  } catch {
    // Nothing to do — a session that can't persist still works for the
    // current tab's lifetime, it just won't survive a refresh.
  }
}

export function clearStoredAuthToken(): void {
  try {
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    // See setStoredAuthToken.
  }
}

type ExtraHeaders = Record<string, string>;

/**
 * Content-Type only when there is a body (keeps preflights narrow). A 401 ends
 * the session app-wide via "auth:unauthorized" — but only if the token that
 * request carried is still the stored one, so a sign-in that happened while
 * an older request was in flight is never undone by it.
 */
async function send(path: string, options?: RequestInit): Promise<Response> {
  const hasBody = options?.body !== undefined;
  const token = getStoredAuthToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  });

  if (response.status === 401 && getStoredAuthToken() === token) {
    clearStoredAuthToken();
    window.dispatchEvent(new Event("auth:unauthorized"));
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = body?.detail ?? response.statusText;
    throw new ApiError(response.status, typeof message === "string" ? message : JSON.stringify(message));
  }
  return response;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

const jsonBody = (body: unknown) => (body !== undefined ? JSON.stringify(body) : undefined);

export const apiClient = {
  get: <T,>(path: string): Promise<T> => request<T>(path),
  getBlob: async (path: string): Promise<Blob> => (await send(path, { cache: "no-store" })).blob(),
  post: <T,>(path: string, body?: unknown, headers?: ExtraHeaders): Promise<T> =>
    request<T>(path, { method: "POST", body: jsonBody(body), headers }),
  put: <T,>(path: string, body?: unknown, headers?: ExtraHeaders): Promise<T> =>
    request<T>(path, { method: "PUT", body: jsonBody(body), headers }),
  patch: <T,>(path: string, body?: unknown, headers?: ExtraHeaders): Promise<T> =>
    request<T>(path, { method: "PATCH", body: jsonBody(body), headers }),
  delete: <T,>(path: string, headers?: ExtraHeaders): Promise<T> => request<T>(path, { method: "DELETE", headers }),
};
