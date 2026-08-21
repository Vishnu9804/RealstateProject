import { ApiError } from "../api/client";

export function friendlyError(err: unknown): string {
  return err instanceof ApiError ? `${err.status}: ${err.message}` : "Could not reach the backend.";
}
