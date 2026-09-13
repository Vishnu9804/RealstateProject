import { apiClient } from "./client";
import type { LoginResult, OwnerVerificationGrant, OwnerVerificationStatus, UserSummary, VerificationCodeResult } from "./types";

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
  changeOwnPassword: (body: ChangeOwnPasswordRequest, grant: string): Promise<LoginResult> =>
    apiClient.patch("/auth/me/password", body, withGrant(grant)),
  endOtherSessions: (): Promise<LoginResult> => apiClient.post("/auth/me/end-other-sessions"),

  getOwnerVerification: (): Promise<OwnerVerificationStatus> => apiClient.get("/auth/owner-verification"),
  requestOwnerCode: (): Promise<VerificationCodeResult> => apiClient.post("/auth/owner-verification/code"),
  confirmOwnerVerification: (body: { code?: string; password?: string }): Promise<OwnerVerificationGrant> =>
    apiClient.post("/auth/owner-verification/confirm", body),

  requestRecoveryCode: (): Promise<VerificationCodeResult> => apiClient.post("/auth/recovery/code"),
  resetWithRecoveryCode: (body: { code: string; new_password: string }): Promise<{ username: string }> =>
    apiClient.post("/auth/recovery/reset", body),

  listEmployees: (): Promise<UserSummary[]> => apiClient.get("/users"),
  createEmployee: (body: CreateEmployeeRequest, grant: string): Promise<UserSummary> =>
    apiClient.post("/users", body, withGrant(grant)),
  updateEmployee: (userId: string, body: UpdateEmployeeRequest, grant: string): Promise<UserSummary> =>
    apiClient.patch(`/users/${encodeURIComponent(userId)}`, body, withGrant(grant)),
  deleteEmployee: (userId: string): Promise<void> => apiClient.delete(`/users/${encodeURIComponent(userId)}`),
};
