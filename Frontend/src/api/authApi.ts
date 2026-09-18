import { apiClient } from "./client";
import type { LoginResult, OwnerVerificationGrant, UserSummary } from "./types";

export interface ChangeOwnPasswordRequest {
  current_password: string;
  new_password: string;
}

export interface CreateEmployeeRequest {
  username: string;
  password: string;
}

export interface UpdateEmployeeRequest {
  username?: string;
  password?: string;
}

/** Proof from OwnerVerificationDialog; the backend answers 428 without it. */
const withGrant = (grant: string) => ({ "X-Owner-Verification": grant });

export const authApi = {
  login: (body: { username: string; password: string }): Promise<LoginResult> => apiClient.post("/auth/login", body),
  getMe: (): Promise<UserSummary> => apiClient.get("/auth/me"),
  /** Swaps the current, still-valid token for a fresh one (AuthProvider calls it while the user is active). */
  refresh: (): Promise<LoginResult> => apiClient.post("/auth/refresh"),
  changeOwnPassword: (body: ChangeOwnPasswordRequest): Promise<LoginResult> => apiClient.patch("/auth/me/password", body),
  endOtherSessions: (): Promise<LoginResult> => apiClient.post("/auth/me/end-other-sessions"),

  /** The one step-up check in the app: confirms the caller's own (admin) password before a staff login is added or changed. */
  confirmOwnerVerification: (body: { password: string }): Promise<OwnerVerificationGrant> =>
    apiClient.post("/auth/owner-verification/confirm", body),

  listEmployees: (): Promise<UserSummary[]> => apiClient.get("/users"),
  createEmployee: (body: CreateEmployeeRequest, grant: string): Promise<UserSummary> =>
    apiClient.post("/users", body, withGrant(grant)),
  updateEmployee: (userId: string, body: UpdateEmployeeRequest, grant: string): Promise<UserSummary> =>
    apiClient.patch(`/users/${encodeURIComponent(userId)}`, body, withGrant(grant)),
  deleteEmployee: (userId: string): Promise<void> => apiClient.delete(`/users/${encodeURIComponent(userId)}`),
};
