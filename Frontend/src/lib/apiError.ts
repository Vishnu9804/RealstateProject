import { ApiError } from "../api/client";

export function friendlyError(err: unknown): string {
  return err instanceof ApiError ? `${err.status}: ${err.message}` : "Could not reach the backend.";
}

/** The server's own message without the status prefix — for sign-in and account dialogs. */
export function plainError(err: unknown): string {
  return err instanceof ApiError ? err.message : "Could not reach the server.";
}
