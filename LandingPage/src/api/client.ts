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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
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
